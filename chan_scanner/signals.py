"""Moving-average filters on a chronologically ordered completed-bar series.

The caller is responsible for removing unfinished/stale bars and choosing a
consistent price adjustment. There are no structure or trading-point filters.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import math


# Treat floating-point noise as equality, without rounding displayed MA values.
_REL_TOL = 1e-12
_ABS_TOL = 1e-12


def _greater(left: float, right: float) -> bool:
    return left > right and not math.isclose(
        left, right, rel_tol=_REL_TOL, abs_tol=_ABS_TOL
    )


def _positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _periods(values: Iterable[int], *, require_filter: bool = False) -> tuple[int, ...]:
    try:
        periods = tuple(values)
    except TypeError as exc:
        raise ValueError("periods must be an iterable of positive integers") from exc
    if not periods:
        raise ValueError("periods must not be empty")
    for value in periods:
        _positive_integer(value, "period")
    if len(set(periods)) != len(periods):
        raise ValueError("periods must be unique")
    if require_filter:
        if tuple(sorted(periods)) != periods:
            raise ValueError("bullish_periods must be strictly increasing")
        if not {5, 10, 60}.issubset(periods):
            raise ValueError("bullish_periods must include 5, 10 and 60")
    return periods


def _read_bars(bars: Iterable[Mapping]) -> tuple[list[Mapping], list[float]]:
    try:
        rows = list(bars)
    except TypeError as exc:
        raise ValueError("bars must be an iterable of mappings") from exc
    closes = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or "close" not in row:
            raise ValueError(f"bar {index} must have a close price")
        value = row["close"]
        if isinstance(value, bool):
            raise ValueError(f"bar {index} has an invalid close price")
        try:
            close = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"bar {index} has an invalid close price") from exc
        if not math.isfinite(close):
            raise ValueError(f"bar {index} close price must be finite")
        closes.append(close)
    return rows, closes


def _series(closes: list[float], periods: tuple[int, ...]) -> dict[str, list]:
    result = {}
    for period in periods:
        values = [None] * min(period - 1, len(closes))
        for index in range(period - 1, len(closes)):
            try:
                average = math.fsum(closes[index - period + 1 : index + 1]) / period
            except OverflowError as exc:
                raise ValueError("close prices are too large for a finite average") from exc
            if not math.isfinite(average):
                raise ValueError("close prices are too large for a finite average")
            values.append(average)
        result[str(period)] = values
    return result


def ma_series(
    bars: Iterable[Mapping], periods: Iterable[int] = (5, 10, 20, 60)
) -> dict[str, list[float | None]]:
    """Return SMA values aligned with input bars; warm-up entries are ``None``.

    Prices are never rounded. Each window uses ``math.fsum`` to avoid cumulative
    drift. Periods must be unique positive integers; input order is preserved.
    """
    selected = _periods(periods)
    _, closes = _read_bars(bars)
    return _series(closes, selected)


def evaluate(
    bars: Iterable[Mapping],
    cross_lookback: int = 1,
    trend_bars: int = 3,
    bullish_periods: Iterable[int] = (5, 10, 20, 60),
    near_ma_gap: float | None = None,
) -> dict:
    """Evaluate the crossover branch and an optional MA5/MA10 proximity branch.

    A crossover requires MA5 > MA10 on that bar and MA5 <= MA10 on
    the preceding bar. Search the latest ``cross_lookback`` bars, most recent
    first, while still requiring MA5 > MA10 at the current endpoint. MA60 must
    increase on every one of the last ``trend_bars`` transitions. Comparisons
    within 1e-12 absolute/relative tolerance count as equal.

    With ``near_ma_gap`` enabled, also admit a current absolute MA5/MA10 price
    difference no larger than that threshold, including equality. This branch
    requires the remaining configured averages (after removing MA5) to descend
    strictly by period; it does not require a crossover or MA5 > MA10. Both
    branches require enough history, the same MA60 upward trend, and positive
    latest one-bar MA5 and MA20 slopes. ``None``
    preserves the original crossover-only filter. The threshold is a price
    difference in the input currency, not a percentage.

    The default needs 63 bars. A longer crossover window searches the available
    valid MA history and does not add a separate warm-up requirement. Indices
    are zero-based positions in the supplied series; dates retain input format.
    """
    cross_lookback = _positive_integer(cross_lookback, "cross_lookback")
    trend_bars = _positive_integer(trend_bars, "trend_bars")
    periods = _periods(bullish_periods, require_filter=True)
    if near_ma_gap is not None:
        if isinstance(near_ma_gap, bool) or not isinstance(near_ma_gap, (int, float)):
            raise ValueError("near_ma_gap must be a non-negative finite number or None")
        try:
            near_ma_gap = float(near_ma_gap)
        except (ValueError, OverflowError) as exc:
            raise ValueError("near_ma_gap must be a non-negative finite number or None") from exc
        if not math.isfinite(near_ma_gap) or near_ma_gap < 0:
            raise ValueError("near_ma_gap must be a non-negative finite number or None")
    rows, closes = _read_bars(bars)
    series = _series(closes, tuple(sorted(set(periods) | {20})))
    count = len(rows)
    required = max(60 + trend_bars, max(periods), 11)
    insufficient = count < required
    latest = {key: (values[-1] if values else None) for key, values in series.items() if int(key) in periods}

    cross = {"occurred": False, "date": None, "index": None, "bars_ago": None}
    for index in range(count - 1, max(10, count - cross_lookback) - 1, -1):
        if _greater(series["5"][index], series["10"][index]) and not _greater(
            series["5"][index - 1], series["10"][index - 1]
        ):
            cross = {
                "occurred": True,
                "date": rows[index].get("date"),
                "index": index,
                "bars_ago": count - 1 - index,
            }
            break

    bullish = all(value is not None for value in latest.values()) and all(
        _greater(latest[str(short)], latest[str(long)])
        for short, long in zip(periods, periods[1:])
    )
    current_above = latest["5"] is not None and latest["10"] is not None and _greater(
        latest["5"], latest["10"]
    )
    lower_periods = tuple(period for period in periods if period != 5)
    lower_bullish = all(latest[str(period)] is not None for period in lower_periods) and all(
        _greater(latest[str(short)], latest[str(long)])
        for short, long in zip(lower_periods, lower_periods[1:])
    )
    gap = None
    if latest["5"] is not None and latest["10"] is not None:
        gap = latest["5"] - latest["10"]
        if not math.isfinite(gap):
            raise ValueError("MA5/MA10 difference must be finite")
    gap_abs = abs(gap) if gap is not None else None
    within_gap = near_ma_gap is not None and gap_abs is not None and not _greater(
        gap_abs, near_ma_gap
    )

    trend_delta = None
    ma60_up = False
    if count >= 60 + trend_bars:
        start = count - trend_bars - 1
        recent = series["60"][start:]
        trend_delta = recent[-1] - recent[0]
        ma60_up = all(_greater(right, left) for left, right in zip(recent, recent[1:]))

    slopes = {str(p): (series[str(p)][-1] - series[str(p)][-2]
                         if count >= p + 1 else None) for p in (5, 20)}
    upward = {str(p): count >= p + 1 and _greater(series[str(p)][-1], series[str(p)][-2])
              for p in (5, 20)}
    short_up = all(upward.values())
    common_reasons, strict_conditions = [], []
    for p in (5, 20):
        if not upward[str(p)]:
            common_reasons.append(f"MA{p} 最新斜率未大于零（最新值需高于前一根）")
    if insufficient:
        common_reasons.append(f"历史不足：至少需要 {required} 根完成K线，实际 {count} 根")
    if not cross["occurred"]:
        strict_conditions.append(f"最近 {cross_lookback} 根K线未出现 MA5 上穿 MA10")
    if not current_above:
        strict_conditions.append("当前 MA5 未高于 MA10")
    if not bullish:
        order = " > ".join(f"MA{period}" for period in periods)
        strict_conditions.append(f"当前均线未满足 {order}")
    if not ma60_up:
        common_reasons.append(f"MA60 未连续 {trend_bars} 次上升")

    strict_setup = cross["occurred"] and current_above and bullish
    near_setup = within_gap and lower_bullish
    strict_match = bool(not insufficient and ma60_up and short_up and strict_setup)
    near_match = bool(not insufficient and ma60_up and short_up and near_setup)
    matched = strict_match or near_match
    strict_reasons = common_reasons[:1] + strict_conditions + common_reasons[1:] if insufficient else strict_conditions + common_reasons
    match_type = "cross" if strict_match else ("near" if near_match else None)

    if matched:
        reasons = []
    elif near_ma_gap is None:
        reasons = strict_reasons.copy()
    else:
        reasons = common_reasons.copy()
        if not strict_setup and not near_setup:
            near_conditions = []
            if not within_gap:
                near_conditions.append(
                    "MA5/MA10 差值尚不可计算" if gap_abs is None
                    else f"|MA5-MA10|={gap_abs:g} 超过 {near_ma_gap:g}"
                )
            if not lower_bullish:
                order = " > ".join(f"MA{period}" for period in lower_periods)
                near_conditions.append(f"其余均线未满足 {order}")
            reasons.append("金叉分支：" + "、".join(strict_conditions)
                           + "；均线接近分支：" + "、".join(near_conditions))

    return {
        "matched": matched,
        "strict_match": strict_match,
        "near_match": near_match,
        "match_type": match_type,
        "match_label": "金叉" if strict_match else ("均线接近" if near_match else None),
        "insufficient_bars": insufficient,
        "ma": latest,
        "ma5_ma10_gap": gap,
        "ma5_ma10_gap_abs": gap_abs,
        "near_ma_gap": near_ma_gap,
        "cross": cross,
        "bullish": bool(bullish),
        "lower_bullish": bool(lower_bullish),
        "ma5_up": upward["5"],
        "ma20_up": upward["20"],
        "ma_slopes": slopes,
        "ma60_up": ma60_up,
        "trend_bars": trend_bars,
        "trend_delta": trend_delta,
        "reasons": reasons,
        "strict_reasons": strict_reasons,
    }
