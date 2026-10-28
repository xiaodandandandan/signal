"""Self-contained structure tests; optional parity against the source checkout."""
import importlib.util
import math
import os
from pathlib import Path
import random
import unittest

from chan_scanner.chan import (calculate_structure, find_bi, find_fenxing,
                               find_zhongshu, merge_bars)


def point(mi, t, p, i=None):
    return {"mi": mi, "i": mi if i is None else i, "t": t, "p": p}


def wave_bars(n=150):
    return [{"date": 20260000 + i, "open": 20 + 4 * math.sin(i * math.pi / 7),
             "close": 20 + 4 * math.sin(i * math.pi / 7),
             "high": 20.2 + 4 * math.sin(i * math.pi / 7),
             "low": 19.8 + 4 * math.sin(i * math.pi / 7), "volume": 100}
            for i in range(n)]


class ChanTests(unittest.TestCase):
    def test_empty_and_short_input(self):
        empty = calculate_structure([])
        self.assertTrue(all(not v for v in empty.values()))
        for n in (1, 2):
            st = calculate_structure(wave_bars(n))
            self.assertEqual(st["fractals"], [])
            self.assertEqual(st["strokes"], [])
            self.assertEqual(st["hubs"], [])

    def test_inclusion_keeps_original_extreme_indices(self):
        rows = [(0, 5, 1), (1, 7, 3), (2, 6, 4), (3, 8, 2)]
        mb = merge_bars(rows)
        self.assertEqual(mb, [
            {"h": 5, "l": 1, "i0": 0, "i1": 0, "ih": 0, "il": 0},
            {"h": 8, "l": 4, "i0": 1, "i1": 3, "ih": 3, "il": 2},
        ])

    def test_flat_inclusion_uses_latest_extreme_indices(self):
        self.assertEqual(merge_bars([(0, 5, 1), (1, 5, 1)]),
                         [{"h": 5, "l": 1, "i0": 0, "i1": 1, "ih": 1, "il": 1}])

    def test_fractal_prices_and_raw_indices(self):
        mb = merge_bars([(0, 5, 3), (1, 7, 5), (2, 4, 2), (3, 6, 4)])
        self.assertEqual(find_fenxing(mb), [point(1, 1, 7), point(2, -1, 2)])

    def test_bi_gap_is_merged_index_not_raw_index(self):
        fx = [point(0, -1, 10, 0), point(2, 1, 16, 50),
              point(4, 1, 18, 80), point(8, -1, 12, 100)]
        self.assertEqual(find_bi(fx), [fx[0], fx[2], fx[3]])
        self.assertEqual(find_bi(fx[:2]), [fx[0]])

    def test_bi_same_type_keeps_more_extreme_and_enforces_price(self):
        fx = [point(1, -1, 10), point(3, -1, 9), point(7, 1, 8),
              point(8, 1, 15), point(12, -1, 10)]
        self.assertEqual(find_bi(fx), [fx[1], fx[3], fx[4]])

    def test_hub_strict_overlap(self):
        pts = [point(0, -1, 10), point(4, 1, 20), point(8, -1, 12), point(12, 1, 18)]
        self.assertEqual(find_zhongshu(pts), [{"ZD": 12, "ZG": 18, "i0": 0, "i1": 12, "k": 0}])
        boundary = [point(0, -1, 10), point(4, 1, 20), point(8, -1, 20), point(12, 1, 25)]
        self.assertEqual(find_zhongshu(boundary), [])
        self.assertEqual(find_zhongshu(pts[:3]), [])

    def test_hubs_are_rolling_windows_without_coalescing(self):
        pts = [point(i * 4, -1 if i % 2 == 0 else 1, p)
               for i, p in enumerate([10, 20, 12, 18, 11])]
        self.assertEqual(len(find_zhongshu(pts)), 2)
        self.assertEqual([z["k"] for z in find_zhongshu(pts)], [0, 1])

    def test_strokes_and_hubs_keep_tail_provisional(self):
        bars = wave_bars()
        untouched = [dict(b) for b in bars]
        st = calculate_structure(bars)
        self.assertEqual(bars, untouched)
        self.assertGreater(len(st["strokes"]), 4)
        self.assertTrue(st["strokes"][-1]["provisional"])
        self.assertFalse(any(s["provisional"] for s in st["strokes"][:-1]))
        self.assertGreater(len(st["hubs"]), 2)
        self.assertTrue(st["hubs"][-1]["provisional"])
        self.assertFalse(any(z["provisional"] for z in st["hubs"][:-1]))
        self.assertEqual(st["hubs"][-1]["i1"], st["points"][-1]["i"])
        for stroke in st["strokes"]:
            self.assertLess(stroke["start"], stroke["end"])

    def test_rejects_invalid_gap_and_high_low(self):
        for gap in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                calculate_structure([], min_gap=gap)
        for h, l in ((1, 2), (float("nan"), 1), (2, 0), (float("inf"), 1), (True, 1)):
            with self.assertRaises(ValueError):
                calculate_structure([{"high": h, "low": l}])


class UpstreamParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        default = (Path(__file__).resolve().parents[3] / "work" / "upstream" /
                   "Stock_workDashboard-main" / "core" / "_engine" / "chanlun_core.py")
        source = Path(os.environ.get("CHAN_UPSTREAM_PATH", str(default)))
        if not source.is_file():
            raise unittest.SkipTest("可选上游一致性测试：未找到 chanlun_core.py；可设置 CHAN_UPSTREAM_PATH")
        spec = importlib.util.spec_from_file_location("_chan_upstream_for_parity", source)
        cls.upstream = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.upstream)

    def assert_parity(self, bars):
        triples = [(i, b["high"], b["low"]) for i, b in enumerate(bars)]
        mb = self.upstream.merge_bars(triples)
        fx = self.upstream.find_fenxing(mb)
        pts = self.upstream.find_bi(fx)
        hubs = self.upstream.find_zhongshu(pts)
        st = calculate_structure(bars)
        self.assertEqual(st["merged_bars"], mb)
        self.assertEqual(st["fractals"], fx)
        self.assertEqual(st["points"], pts)
        self.assertEqual([{k: z[k] for k in ("ZD", "ZG", "i0", "i1", "k")}
                          for z in st["hubs"]], hubs)

    def test_fixed_and_short_samples_match_upstream(self):
        for n in (0, 1, 2, 3, 20, 150, 500):
            with self.subTest(n=n):
                self.assert_parity(wave_bars(n))

    def test_random_valid_ohlc_match_upstream(self):
        rng = random.Random(11703)
        for case in range(30):
            price = 50.0
            bars = []
            for i in range(rng.randint(50, 500)):
                opening = price
                price = max(3.0, price + rng.uniform(-3, 3))
                bars.append({"date": i, "open": opening, "close": price,
                             "high": max(opening, price) + rng.uniform(0, 3),
                             "low": max(0.1, min(opening, price) - rng.uniform(0, 2)),
                             "volume": 100})
            with self.subTest(case=case):
                self.assert_parity(bars)


if __name__ == "__main__":
    unittest.main()
