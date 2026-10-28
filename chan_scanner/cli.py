"""Command line entrypoint, no package installation needed."""

import argparse
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import sys

from .market import MarketData, normalize_symbol, validate_bars
from .report import atomic_text, json_text, save_result
from .scanner import CHINA, ScanFailure, Settings, run_scan


class FileData:
    """Offline inputs use exactly the same validation, filtering and structures."""
    def __init__(self, rows, demo=False):
        if not isinstance(rows, list) or not rows:
            raise ValueError("离线文件须为非空数组，每项包含 symbol/name/bars")
        self.rows = {}
        for row in rows:
            if not isinstance(row, dict) or "bars" not in row:
                raise ValueError("离线记录须包含 symbol/name/bars")
            symbol = normalize_symbol(row["symbol"])
            if symbol in self.rows:
                raise ValueError("离线数据有重复股票")
            self.rows[symbol] = {**row, "bars": validate_bars(row["bars"])}
        self.provenance = {"provider": "synthetic_demo" if demo else "local_file",
                           "adjustment": "synthetic" if demo else "user_supplied",
                           "period": "day", "volume_unit": "shares"}

    def list_symbols(self):
        return [{"symbol": key, "name": str(row.get("name", key))} for key, row in self.rows.items()]

    def daily_bars(self, symbol, count=120):
        return self.rows[symbol]["bars"][-count:]


def demo_data():
    closes = [10 + 2 * math.sin(i * math.pi / 10) for i in range(200)] + [10.0] * 60 + [20.0] * 10 + [21.0]
    day = datetime.now(CHINA).date() - timedelta(days=1)
    dates = []
    while len(dates) < len(closes):
        if day.weekday() < 5:
            dates.append(int(day.strftime("%Y%m%d")))
        day -= timedelta(days=1)
    dates.reverse()
    bars = [{"date": date, "open": close * 0.998, "high": close * 1.02,
             "low": close * 0.98, "close": close, "volume": 1000000}
            for date, close in zip(dates, closes)]
    return [{"symbol": "sh600000", "name": "合成行情演示（非真实股票结果）", "bars": bars}]


def parser():
    ap = argparse.ArgumentParser(description="沪深北日K扫描：金叉或MA5/MA10差价不超过1元 + 均线多头 + MA5/MA20最新斜率为正 + MA60向上；只算笔与中枢")
    source = ap.add_mutually_exclusive_group()
    source.add_argument("--codes", help="只分析指定代码，逗号分隔；不设则获取沪深北全市场")
    source.add_argument("--input", type=Path, help="离线JSON文件 [{symbol,name,bars}, ...]")
    source.add_argument("--demo", action="store_true", help="使用明确标注的合成行情，不联网")
    ap.add_argument("--count", type=int, default=120, help="每只股票日K数量，默认120")
    ap.add_argument("--workers", type=int, default=8, help="取数并发，1..32，默认8")
    ap.add_argument("--cross-lookback", type=int, default=1, help="最近N根内发生金叉，默认1（最新完成K）")
    ap.add_argument("--trend-bars", type=int, default=3, help="MA60连续上升次数，默认3")
    ap.add_argument("--bullish-periods", default="5,10,20,60", help="多头均线周期，严格升序，至少含5/10/60")
    ap.add_argument("--near-ma-gap", type=float, default=1.0, help="MA5与MA10近距支路的最大绝对差价（元），默认1.0")
    ap.add_argument("--strict-cross", action="store_true", help="禁用均线近距支路，仅保留原金叉筛选")
    ap.add_argument("--min-gap", type=int, default=4, help="笔端点最小合并K线间距，默认4，沿用原仓库")
    ap.add_argument("--include-forming", action="store_true", help="包含当日盘中未完成日K，并在报告标记")
    ap.add_argument("--limit", type=int, default=0, help="仅试扫前N只；默认0扫全市场")
    ap.add_argument("--timeout", type=float, default=15, help="单次网络请求超时秒数")
    ap.add_argument("--retries", type=int, default=2, help="网络失败最大重试次数，默认2")
    ap.add_argument("--output-dir", type=Path, default=Path("results"), help="结果文件夹，默认results")
    ap.add_argument("--open", action="store_true", help="完成后启动可重新扫描的本机页面，并保持服务运行")
    return ap


def main(argv=None):
    ap = parser()
    args = ap.parse_args(argv)
    try:
        periods = tuple(int(p.strip()) for p in args.bullish_periods.split(","))
        settings = Settings(count=args.count, workers=args.workers,
                            cross_lookback=args.cross_lookback, trend_bars=args.trend_bars,
                            bullish_periods=periods, include_forming=args.include_forming,
                            near_ma_gap=None if args.strict_cross else args.near_ma_gap,
                            min_gap=args.min_gap, limit=args.limit).validate()
        symbols = None
        if args.demo or args.input:
            rows = demo_data() if args.demo else json.loads(args.input.read_text(encoding="utf-8-sig"))
            provider = FileData(rows, demo=args.demo)
            symbols = provider.list_symbols()
        else:
            provider = MarketData(timeout=args.timeout, retries=args.retries)
            if args.codes is not None:
                codes = [code.strip() for code in args.codes.split(",")]
                if not codes or any(not code for code in codes):
                    raise ValueError("--codes 必须为非空股票代码列表")
                symbols = [{"symbol": normalize_symbol(code), "name": normalize_symbol(code)} for code in codes]

        def progress(stage, done, total):
            if stage == "list":
                print("读取股票列表与完成日 K 日期…", flush=True)
            else:
                print(f"日 K 分析进度：{done}/{total}", flush=True)

        result = run_scan(provider=provider, symbols=symbols, settings=settings, progress=progress)
        directory = save_result(result, args.output_dir)
        print(f"数据日期 {result['market_date']}；分析 {result['success']}/{result['requested']}；命中 {result['hits']}；失败 {len(result['errors'])}；旧日期 {result['stale']}；历史不足 {result['insufficient']}")
        for warning in result["warnings"]:
            print("提示：" + warning)
        print(f"K 线报告：{directory / 'report.html'}")
        print(f"命中列表：{directory / 'matches.csv'}")
        print(f"全量结构：{directory / 'structures.jsonl'}")
        if args.open:
            from .server import main as serve_main
            serve_args = ["--output-dir", str(args.output_dir.resolve())]
            if args.demo:
                serve_args.append("--demo")
            return serve_main(serve_args)
        return 0
    except ScanFailure as exc:
        atomic_text(args.output_dir / "attempt.json", json_text(exc.diagnostic))
        print(f"扫描失败：{exc}。诊断：{args.output_dir / 'attempt.json'}", file=sys.stderr)
        return 1
    except (ValueError, KeyError, OSError) as exc:
        print(f"参数或文件错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
