"""Whole-market orchestration. Structure calculation never invokes buy/sell rules."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import math
import time

from .chan import calculate_structure
from .indicators import macd
from .market import MarketData, completed_daily_bars, normalize_symbol, validate_bars
from .signals import evaluate, ma_series

CHINA = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class Settings:
    count: int = 120
    workers: int = 8
    cross_lookback: int = 1
    trend_bars: int = 3
    bullish_periods: tuple = (5, 10, 20, 60)
    near_ma_gap: float | None = 1.0
    include_forming: bool = False
    min_gap: int = 4
    limit: int = 0
    min_success_ratio: float = 0.95

    def validate(self):
        for name, low, high in (("count", 63, 5000), ("workers", 1, 32),
                                ("cross_lookback", 1, 250), ("trend_bars", 1, 250),
                                ("min_gap", 1, 100), ("limit", 0, 20000)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} 必须为 {low}..{high} 的整数")
        if not isinstance(self.include_forming, bool):
            raise ValueError("include_forming 必须是布尔值")
        if not isinstance(self.min_success_ratio, (int, float)) or not math.isfinite(self.min_success_ratio) or not 0 < self.min_success_ratio <= 1:
            raise ValueError("min_success_ratio 必须在 (0,1] 内")
        # Delegate the period contract to the signal module, even for an empty input.
        evaluate([], cross_lookback=self.cross_lookback, trend_bars=self.trend_bars,
                 bullish_periods=self.bullish_periods, near_ma_gap=self.near_ma_gap)
        if self.count < max(60 + self.trend_bars, max(self.bullish_periods)):
            raise ValueError("count 不足以计算指定的均线和趋势窗口")
        return self


class ScanFailure(RuntimeError):
    def __init__(self, message, diagnostic):
        super().__init__(message)
        self.diagnostic = diagnostic


def analyze(item, bars, settings=None, now=None):
    """Analyze *every* valid symbol, including symbols failing the MA filter."""
    settings = (settings or Settings()).validate()
    clean = validate_bars(bars)
    clean, dropped = completed_daily_bars(clean, now=now,
                                          include_forming=settings.include_forming)
    condition = evaluate(clean, cross_lookback=settings.cross_lookback,
                         trend_bars=settings.trend_bars,
                         bullish_periods=settings.bullish_periods, near_ma_gap=settings.near_ma_gap)
    structure = calculate_structure(clean, min_gap=settings.min_gap)
    return {"symbol": normalize_symbol(item["symbol"]), "name": str(item.get("name", "")),
            "date": clean[-1]["date"] if clean else None,
            "close": clean[-1]["close"] if clean else None,
            "bar_count": len(clean), "dropped_bars": dropped,
            "condition": condition, "structure": structure, "bars": clean,
            "ma_series": ma_series(clean, periods=settings.bullish_periods),
            "macd": macd(clean)}


def run_scan(provider=None, symbols=None, settings=None, progress=None, now=None):
    """Return a reviewable result; never overwrite any files on a failed scan.

    `records` contains structures for every successfully fetched symbol. `rows`
    contains current-date MA matches, with bars for drawing an identical chart.
    """
    settings = (settings or Settings()).validate()
    provider = provider or MarketData()
    started = time.perf_counter()
    current = now or datetime.now(CHINA)
    if current.tzinfo is None:
        current = current.replace(tzinfo=CHINA)
    current = current.astimezone(CHINA)
    diagnostic = {"generated": current.isoformat(timespec="seconds"), "status": "running",
                  "stage": "list", "errors": [], "processed": 0}
    supplied_symbols = symbols is not None

    def say(stage, done, total):
        diagnostic.update(stage=stage, processed=done)
        if progress:
            progress(stage, done, total)

    try:
        say("list", 0, 0)
        universe = list(symbols) if supplied_symbols else provider.list_symbols()
        if not universe:
            raise ValueError("市场列表为空")
        universe = [{"symbol": normalize_symbol(x["symbol"]), "name": str(x.get("name", ""))}
                    for x in universe]
        if len({x["symbol"] for x in universe}) != len(universe):
            raise ValueError("市场列表存在重复股票")
        universe_total = len(universe)
        if settings.limit:
            universe = universe[:settings.limit]
        diagnostic["requested"] = len(universe)
        # An independent index anchors the latest completed session. If that
        # request fails, retain a visible warning and infer a date from stocks.
        reference_date, reference_error = None, None
        if not supplied_symbols:
            try:
                reference = validate_bars(provider.daily_bars("sh000001", count=80))
                reference, _ = completed_daily_bars(reference, now=current,
                                                     include_forming=settings.include_forming)
                if not reference:
                    raise ValueError("上证指数无已完成日 K")
                reference_date = reference[-1]["date"]
            except Exception as exc:
                reference_error = f"{type(exc).__name__}: {exc}"

        records, hits = [], []

        def one(item):
            bars = provider.daily_bars(item["symbol"], count=settings.count)
            bars = validate_bars(bars)[-settings.count:]
            return analyze(item, bars, settings, now=current)

        with ThreadPoolExecutor(max_workers=settings.workers) as executor:
            futures = {executor.submit(one, item): item for item in universe}
            for done, future in enumerate(as_completed(futures), 1):
                item = futures[future]
                try:
                    result = future.result()
                    if result["condition"]["matched"]:
                        hits.append(result)
                    record = {k: v for k, v in result.items() if k not in ("bars", "ma_series", "macd", "structure")}
                    # Preserve the requested pens/hubs for all symbols without
                    # duplicating hundreds of merged-bar internals in each row.
                    record["structure"] = {key: result["structure"][key] for key in ("strokes", "hubs")}
                    records.append(record)
                except Exception as exc:
                    diagnostic["errors"].append({"symbol": item["symbol"],
                                                 "error": f"{type(exc).__name__}: {exc}"})
                if done % 100 == 0 or done == len(universe):
                    say("scan", done, len(universe))

        ratio = len(records) / len(universe)
        diagnostic.update(success=len(records), success_ratio=ratio)
        if ratio < settings.min_success_ratio:
            raise ValueError(f"扫描成功率 {ratio:.1%} 低于 {settings.min_success_ratio:.0%}，保留上次结果")
        observed_dates = [r["date"] for r in records if r["date"] is not None]
        observed_latest = max(observed_dates) if observed_dates else None
        # Prefer the newest stock date if the reference itself is lagging.
        market_date = max(d for d in (reference_date, observed_latest) if d is not None) if (reference_date or observed_latest) else None
        if market_date is None:
            raise ValueError("所有股票均无已完成日 K，保留上次结果")
        for record in records:
            record["stale"] = record["date"] != market_date
        hits = [r for r in hits if r["date"] == market_date]
        hits.sort(key=lambda x: x["symbol"])
        records.sort(key=lambda x: x["symbol"])
        insufficient = sum(bool(r["condition"]["insufficient_bars"]) for r in records)
        stale = sum(r["stale"] for r in records)
        scope = "specified_symbols" if supplied_symbols else ("limited_market" if settings.limit else "all_a_shares")
        warnings = []
        if reference_error:
            warnings.append("交易日参考取数失败，数据日期采用本批股票最新日期：" + reference_error)
        if reference_date and observed_latest and reference_date != observed_latest:
            warnings.append(f"指数日期 {reference_date} 与股票最新日期 {observed_latest} 不同，已排除较旧截面")
        if settings.include_forming:
            warnings.append("包含盘中未完成日 K，均线条件和笔/中枢结构可能随行情变化")
        if diagnostic["errors"]:
            warnings.append(f"有 {len(diagnostic['errors'])} 只取数或计算失败，请查看失败明细")
        diagnostic.update(status="success", stage="done")
        return {"schema_version": 1, "generated": diagnostic["generated"], "scope": scope,
                "frequency": "day", "market_date": market_date,
                "reference_date": reference_date,
                "reference_method": "index_and_observed" if reference_date else "observed_only",
                "universe_total": universe_total, "requested": len(universe),
                "success": len(records), "errors": diagnostic["errors"],
                "complete": not diagnostic["errors"], "success_ratio": ratio,
                "insufficient": insufficient, "stale": stale,
                "hits": len(hits), "rows": hits, "records": records,
                "warnings": warnings, "settings": asdict(settings),
                "source": dict(provider.provenance),
                "elapsed_seconds": round(time.perf_counter() - started, 2)}
    except Exception as exc:
        diagnostic.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise ScanFailure(str(exc), diagnostic) from exc
