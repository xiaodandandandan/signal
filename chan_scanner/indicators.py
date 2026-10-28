"""Chart indicators; these values do not participate in stock selection.

MACD follows the source repository's ``core/_engine/chanlun_core.py`` formula:
EMA starts at the first close, DIF = EMA(fast) - EMA(slow), DEA = EMA(DIF),
and the histogram is twice DIF minus DEA. Values are returned from the first bar
for aligned charts, including bars before the recommended warm-up threshold.
"""

from collections.abc import Mapping
import math


def _period(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    try:
        factor = 2.0 / (value + 1)
    except OverflowError as exc:
        raise ValueError(f"{name} is too large") from exc
    if factor == 0:
        raise ValueError(f"{name} is too large")
    return value


def _closes(bars):
    try:
        rows = iter(bars)
    except TypeError as exc:
        raise ValueError("bars must be an iterable of mappings") from exc
    result = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or "close" not in row:
            raise ValueError(f"bar {index} must have a close price")
        if isinstance(row["close"], bool):
            raise ValueError(f"bar {index} has an invalid close price")
        try:
            value = float(row["close"])
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"bar {index} has an invalid close price") from exc
        if not math.isfinite(value):
            raise ValueError(f"bar {index} close price must be finite")
        result.append(value)
    return result


def _ema(values, period):
    # Preserve the upstream arithmetic order for identical floating-point output.
    factor = 2.0 / (period + 1)
    result, previous = [], None
    for value in values:
        previous = value if previous is None else value * factor + previous * (1 - factor)
        if not math.isfinite(previous):
            raise ValueError("close prices are too large for a finite EMA")
        result.append(previous)
    return result


def macd(bars, fast=12, slow=26, signal=9):
    """Return aligned DIF, DEA and doubled MACD histogram arrays, without rounding.

    ``fast``, ``slow`` and ``signal`` are positive integers, with fast < slow.
    The first close seeds both price EMAs; the first DIF seeds DEA. The first
    DIF, DEA and histogram values therefore equal zero. ``warmup_bars`` is
    slow + signal (35 by default), matching the upstream minimum-history gate.
    Shorter inputs still return all calculated values for charting.
    """
    fast = _period(fast, "fast")
    slow = _period(slow, "slow")
    signal = _period(signal, "signal")
    if fast >= slow:
        raise ValueError("fast must be smaller than slow")
    closes = _closes(bars)
    quick, gradual = _ema(closes, fast), _ema(closes, slow)
    dif = [left - right for left, right in zip(quick, gradual)]
    if any(not math.isfinite(value) for value in dif):
        raise ValueError("close prices are too large for a finite DIF")
    dea = _ema(dif, signal)
    histogram = [(left - right) * 2 for left, right in zip(dif, dea)]
    if any(not math.isfinite(value) for value in histogram):
        raise ValueError("close prices are too large for a finite MACD histogram")
    return {
        "dif": dif,
        "dea": dea,
        "histogram": histogram,
        "parameters": {"fast": fast, "slow": slow, "signal": signal},
        "warmup_bars": slow + signal,
    }
