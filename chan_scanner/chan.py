# -*- coding: utf-8 -*-
"""缠论结构内核：包含处理 → 分型 → 笔 → 三笔重叠中枢。

四个底层函数抽取自 Stock_workDashboard 的 chanlun_core.py，算法保持原样。
输入日 K 按日期升序；合并 K 线始终保留原始 K 线索引。
mi 是合并后序号，用于笔间距；i 是原始序号，用于绘图。
来源、许可和上游局限见项目根目录 NOTICE.md。
"""

import math

MIN_GAP = 4


def merge_bars(bars):
    """包含处理，输入 [(idx, high, low)]，保留原始索引。"""
    res = []
    for idx, h, l in bars:
        hit = res and ((h >= res[-1]["h"] and l <= res[-1]["l"])
                       or (h <= res[-1]["h"] and l >= res[-1]["l"]))
        if hit:
            d = res[-1]
            up = h > d["h"] if len(res) < 2 else d["h"] > res[-2]["h"]
            if up:
                if h >= d["h"]:
                    d["h"] = h; d["ih"] = idx
                if l >= d["l"]:
                    d["l"] = l; d["il"] = idx
            else:
                if h <= d["h"]:
                    d["h"] = h; d["ih"] = idx
                if l <= d["l"]:
                    d["l"] = l; d["il"] = idx
            d["i1"] = idx
        else:
            res.append({"h": h, "l": l, "i0": idx, "i1": idx, "ih": idx, "il": idx})
    return res


def find_fenxing(mb):
    """返回 [{'mi','i','t','p'}]；t=1 顶分型，t=-1 底分型。"""
    fx = []
    for k in range(1, len(mb) - 1):
        c, p, n = mb[k], mb[k - 1], mb[k + 1]
        if c["h"] > p["h"] and c["h"] > n["h"]:
            fx.append({"mi": k, "i": c["ih"], "t": 1, "p": c["h"]})
        elif c["l"] < p["l"] and c["l"] < n["l"]:
            fx.append({"mi": k, "i": c["il"], "t": -1, "p": c["l"]})
    return fx


def find_bi(fx, min_gap=MIN_GAP):
    """按上游规则选端点：异型、合并间距达标、顶高于底；同型取极值。"""
    if len(fx) < 2:
        return []
    pts, last = [], None
    for f in fx:
        if last is None:
            last = f; continue
        if f["t"] == last["t"]:
            if (f["t"] == 1 and f["p"] > last["p"]) or (f["t"] == -1 and f["p"] < last["p"]):
                last = f
            continue
        if f["mi"] - last["mi"] < min_gap:
            continue
        ok = (last["t"] == -1 and f["p"] > last["p"]) or (last["t"] == 1 and f["p"] < last["p"])
        if not ok:
            continue
        pts.append(last)
        last = f
    if last is not None:
        pts.append(last)
    return pts


def find_zhongshu(pts):
    """每连续三笔独立求重叠区间；零宽度重叠不算中枢。"""
    zs = []
    for k in range(len(pts) - 3):
        segs = [(pts[k]["p"], pts[k + 1]["p"]),
                (pts[k + 1]["p"], pts[k + 2]["p"]),
                (pts[k + 2]["p"], pts[k + 3]["p"])]
        ZD = max(min(a, b) for a, b in segs)
        ZG = min(max(a, b) for a, b in segs)
        if ZD < ZG:
            zs.append({"ZD": ZD, "ZG": ZG, "i0": pts[k]["i"], "i1": pts[k + 3]["i"], "k": k})
    return zs


def calculate_structure(bars, min_gap=MIN_GAP):
    """返回原始索引坐标下的结构，绝不执行买卖点或背驰判断。

    返回 merged_bars、fractals、points、strokes、hubs。
    每个 stroke 含 start/end、start_price/end_price、direction、provisional。
    最后一段笔及使用它的中枢标 provisional=True，表示尾端仍可能移动。
    False 仅表示它不是当前尾段，不是交易确认或永久不变的保证。
    """
    if isinstance(min_gap, bool) or not isinstance(min_gap, int) or min_gap < 1:
        raise ValueError("min_gap 必须是大于等于 1 的整数")
    triples = []
    for i, b in enumerate(bars):
        high, low = b["high"], b["low"]
        if (isinstance(high, bool) or isinstance(low, bool)
                or not isinstance(high, (float, int)) or not isinstance(low, (float, int))
                or not math.isfinite(high) or not math.isfinite(low)
                or high < low or low <= 0):
            raise ValueError("第 %d 根 K 线的 high/low 无效" % i)
        triples.append((i, high, low))
    mb = merge_bars(triples)
    fx = find_fenxing(mb)
    pts = find_bi(fx, min_gap=min_gap)
    strokes = []
    for k, (a, b) in enumerate(zip(pts, pts[1:])):
        strokes.append({
            "start": a["i"], "end": b["i"],
            "start_price": a["p"], "end_price": b["p"],
            "direction": "up" if b["p"] > a["p"] else "down",
            "provisional": k == len(pts) - 2,
        })
    hubs = []
    for z in find_zhongshu(pts):
        hubs.append({**z, "provisional": z["k"] + 3 == len(pts) - 1})
    return {"merged_bars": mb, "fractals": fx, "points": pts,
            "strokes": strokes, "hubs": hubs}
