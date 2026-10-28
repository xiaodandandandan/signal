"""MACD formula, chart alignment, validation and optional upstream parity tests."""

from datetime import datetime, timedelta
import importlib.util
import math
import os
from pathlib import Path
import random
import unittest
from unittest.mock import patch

from chan_scanner.indicators import macd
from chan_scanner.market import CHINA_TZ
from chan_scanner.scanner import analyze, run_scan


def bars(closes):
    return [{"close": value} for value in closes]


def full_bars(closes, last_day=datetime(2026, 10, 27)):
    first_day = last_day - timedelta(days=len(closes) - 1)
    return [
        {"date": int((first_day + timedelta(days=index)).strftime("%Y%m%d")),
         "open": value, "high": value + 0.25, "low": value - 0.25,
         "close": value, "volume": 100}
        for index, value in enumerate(closes)
    ]


class MACDTests(unittest.TestCase):
    def test_empty_and_short_input_keeps_alignment_and_warmup_metadata(self):
        self.assertEqual(macd([]), {
            "dif": [], "dea": [], "histogram": [],
            "parameters": {"fast": 12, "slow": 26, "signal": 9}, "warmup_bars": 35,
        })
        for count in (1, 2, 10, 34, 35, 120):
            with self.subTest(count=count):
                result = macd(bars([float(i + 10) for i in range(count)]))
                for key in ("dif", "dea", "histogram"):
                    self.assertEqual(len(result[key]), count)
                    self.assertEqual(result[key][0], 0.0)
                    self.assertTrue(all(isinstance(value, float) for value in result[key]))
                self.assertEqual(result["warmup_bars"], 35)

    def test_hand_computed_custom_parameters_and_double_histogram(self):
        result = macd(bars([1, 2, 3]), fast=2, slow=3, signal=2)
        for actual, expected in zip(result["dif"], (0, 1 / 6, 11 / 36)):
            self.assertAlmostEqual(actual, expected, places=14)
        for actual, expected in zip(result["dea"], (0, 1 / 9, 13 / 54)):
            self.assertAlmostEqual(actual, expected, places=14)
        for actual, expected in zip(result["histogram"], (0, 1 / 9, 7 / 54)):
            self.assertAlmostEqual(actual, expected, places=14)
        self.assertEqual(result["parameters"], {"fast": 2, "slow": 3, "signal": 2})
        self.assertEqual(result["warmup_bars"], 5)

    def test_constant_price_does_not_manufacture_macd_momentum(self):
        result = macd(bars([10.1] * 120))
        for key in ("dif", "dea", "histogram"):
            for value in result[key]:
                self.assertAlmostEqual(value, 0.0, places=12)

    def test_generator_does_not_mutate_input_and_future_does_not_change_prefix(self):
        source = bars([20.0 + math.sin(i) for i in range(120)])
        before = [dict(row) for row in source]
        result = macd(iter(source))
        prefix = macd(source[:40])
        for key in ("dif", "dea", "histogram"):
            self.assertEqual(result[key][:40], prefix[key])
        self.assertEqual(source, before)

    def test_signal_period_one_makes_dea_equal_dif_and_histogram_zero(self):
        result = macd(bars([1, 3, 2, 4]), fast=1, slow=2, signal=1)
        self.assertEqual(result["dif"], result["dea"])
        self.assertEqual(result["histogram"], [0.0] * 4)

    def test_invalid_parameters_are_rejected_even_with_empty_bars(self):
        for name in ("fast", "slow", "signal"):
            for value in (0, -1, True, 2.0, "9", None, math.nan, math.inf, 10 ** 1000):
                with self.subTest(name=name, value=repr(value)), self.assertRaises(ValueError):
                    macd([], **{name: value})
        for fast, slow in ((12, 12), (26, 12)):
            with self.assertRaises(ValueError):
                macd([], fast=fast, slow=slow)

    def test_missing_invalid_and_nonfinite_close_prices_are_rejected(self):
        for value in (None, True, math.nan, math.inf, -math.inf, "nan", "bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                macd(bars([1, value]))
        for invalid in (None, 3, [{}], [3], ["row"]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                macd(invalid)

    def test_nonfinite_derived_histogram_is_rejected(self):
        with self.assertRaises(ValueError):
            macd(bars([-1.7e308, 1.7e308]), fast=1, slow=3, signal=9)


class ScannerMACDTests(unittest.TestCase):
    NOW = datetime(2026, 10, 28, 10, 0, tzinfo=CHINA_TZ)

    def test_analyze_macd_uses_same_completed_bars_as_price_chart(self):
        source = full_bars([10.0] * 60 + [20.0] * 10 + [21.0],
                           last_day=datetime(2026, 10, 28))
        result = analyze({"symbol": "sh600000"}, source, now=self.NOW)
        self.assertEqual(result["dropped_bars"], 1)
        self.assertEqual(result["macd"], macd(result["bars"]))
        self.assertEqual(len(result["macd"]["dif"]), result["bar_count"])
        self.assertEqual(result["date"], 20261027)

    def test_scan_keeps_macd_in_matched_rows_only_and_never_uses_it_as_filter(self):
        class Provider:
            provenance = {"provider": "offline_macd_test"}

            def list_symbols(self):
                return [{"symbol": "sh600000"}, {"symbol": "sh600001"}]

            def daily_bars(self, symbol, count=120):
                values = ([float(i) for i in range(1, 121)] if symbol == "sh600001"
                          else [10.0] * 60 + [20.0] * 10 + [21.0])
                return full_bars(values)[-count:]

        def negative_macd(rows):
            result = macd(rows)
            result["histogram"] = [-999.0] * len(rows)
            return result

        with patch("chan_scanner.scanner.macd", side_effect=negative_macd):
            result = run_scan(Provider(), now=self.NOW)
        self.assertEqual(result["hits"], 1)
        self.assertEqual(result["rows"][0]["symbol"], "sh600000")
        self.assertEqual(result["rows"][0]["macd"]["histogram"], [-999.0] * 71)
        self.assertTrue(all("macd" not in record for record in result["records"]))


class UpstreamMACDParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        default = (Path(__file__).resolve().parents[3] / "work" / "upstream" /
                   "Stock_workDashboard-main" / "core" / "_engine" / "chanlun_core.py")
        source = Path(os.environ.get("CHAN_UPSTREAM_PATH", str(default)))
        if not source.is_file():
            raise unittest.SkipTest("Optional upstream parity: set CHAN_UPSTREAM_PATH to chanlun_core.py")
        spec = importlib.util.spec_from_file_location("_macd_upstream_for_parity", source)
        cls.upstream = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.upstream)

    def assert_parity(self, closes):
        expected_dif, expected_dea, expected_histogram = self.upstream.macd(closes)
        result = macd(bars(closes))
        # Exact equality also verifies operation order and lack of rounding.
        self.assertEqual(result["dif"], expected_dif)
        self.assertEqual(result["dea"], expected_dea)
        self.assertEqual(result["histogram"], expected_histogram)

    def test_deterministic_samples_match_upstream_exactly(self):
        for count in (35, 36, 63, 120, 800):
            samples = (
                [10.1] * count,
                [float(i + 1) for i in range(count)],
                [float(count - i) for i in range(count)],
                [20 + 4 * math.sin(i * math.pi / 7) for i in range(count)],
            )
            for case, closes in enumerate(samples):
                with self.subTest(count=count, case=case):
                    self.assert_parity(closes)

    def test_seeded_random_samples_match_upstream_exactly(self):
        rng = random.Random(11703)
        for case in range(30):
            closes = [rng.uniform(1.0, 200.0) for _ in range(rng.randint(35, 400))]
            with self.subTest(case=case):
                self.assert_parity(closes)


if __name__ == "__main__":
    unittest.main()
