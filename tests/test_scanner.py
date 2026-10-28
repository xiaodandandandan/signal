"""Offline integration tests: real bar validation, MA filters, and Chan structures."""
from datetime import datetime, timedelta
import math
import unittest
from unittest.mock import patch

from chan_scanner.market import CHINA_TZ
from chan_scanner.scanner import ScanFailure, Settings, run_scan


NOW = datetime(2026, 10, 28, 10, 0, tzinfo=CHINA_TZ)
LAST_COMPLETED = datetime(2026, 10, 27)


def bars(closes, end=LAST_COMPLETED):
    start = end - timedelta(days=len(closes) - 1)
    return [{"date": int((start + timedelta(days=index)).strftime("%Y%m%d")),
             "open": close, "high": close + 0.5, "low": close - 0.5,
             "close": close, "volume": 1000}
            for index, close in enumerate(closes)]


def matching(end=LAST_COMPLETED):
    return bars([10.0] * 60 + [20.0] * 10 + [21.0], end=end)


def item(number):
    return {"symbol": "sh%06d" % (600000 + number), "name": "样本%d" % number}


class FakeProvider:
    provenance = {"provider": "offline_test", "adjustment": "fixture"}

    def __init__(self, universe, series=None, failures=(), reference=None, reference_failure=False):
        self.universe = universe
        self.series = series or {}
        self.failures = set(failures)
        self.reference = reference if reference is not None else matching()
        self.reference_failure = reference_failure

    def list_symbols(self):
        return self.universe

    def daily_bars(self, symbol, count=800):
        if symbol in self.failures or (symbol == "sh000001" and self.reference_failure):
            raise RuntimeError("模拟行情失败")
        if symbol == "sh000001":
            return self.reference[-count:]
        return self.series.get(symbol, matching())[-count:]


class ScannerIntegrationTests(unittest.TestCase):
    def test_all_successes_have_structure_even_without_ma_match(self):
        wave = bars([20 + 4 * math.sin(index * math.pi / 8) for index in range(120)])
        provider = FakeProvider([item(0), item(1)], {item(1)["symbol"]: wave})
        result = run_scan(provider, settings=Settings(near_ma_gap=None), now=NOW)
        self.assertEqual(result["success"], 2)
        self.assertEqual(len(result["records"]), 2)
        nonmatch = next(record for record in result["records"] if record["symbol"] == item(1)["symbol"])
        self.assertFalse(nonmatch["condition"]["matched"])
        self.assertGreater(len(nonmatch["structure"]["strokes"]), 0)
        self.assertGreater(len(nonmatch["structure"]["hubs"]), 0)
        self.assertEqual({row["symbol"] for row in result["rows"]}, {item(0)["symbol"]})

    def test_rows_require_all_three_ma_conditions(self):
        universe = [item(i) for i in range(3)]
        provider = FakeProvider(universe, {
            item(1)["symbol"]: bars([float(value) for value in range(1, 101)]),
            item(2)["symbol"]: bars([10.0] * 60 + [9.0] * 10 + [10.0]),
        })
        result = run_scan(provider, settings=Settings(near_ma_gap=None), now=NOW)
        self.assertEqual(result["hits"], 1)
        self.assertEqual(result["rows"][0]["symbol"], item(0)["symbol"])
        conditions = {record["symbol"]: record["condition"] for record in result["records"]}
        self.assertTrue(conditions[item(1)["symbol"]]["bullish"])
        self.assertTrue(conditions[item(1)["symbol"]]["ma60_up"])
        self.assertFalse(conditions[item(1)["symbol"]]["cross"]["occurred"])
        self.assertTrue(conditions[item(2)["symbol"]]["cross"]["occurred"])
        self.assertFalse(conditions[item(2)["symbol"]]["bullish"])

    def test_stale_matching_symbol_stays_in_records_but_not_rows(self):
        provider = FakeProvider([item(0), item(1)], {
            item(1)["symbol"]: matching(end=LAST_COMPLETED - timedelta(days=1)),
        })
        result = run_scan(provider, now=NOW)
        self.assertEqual(result["market_date"], 20261027)
        self.assertEqual(result["stale"], 1)
        self.assertTrue(result["records"][1]["condition"]["matched"])
        self.assertTrue(result["records"][1]["stale"])
        self.assertEqual([row["symbol"] for row in result["rows"]], [item(0)["symbol"]])

    def test_unfinished_today_cannot_trigger_crossover(self):
        provider = FakeProvider([item(0)], {item(0)["symbol"]: matching(end=datetime(2026, 10, 28))})
        result = run_scan(provider, settings=Settings(near_ma_gap=None), now=NOW)
        record = result["records"][0]
        self.assertEqual(record["date"], 20261027)
        self.assertEqual(record["dropped_bars"], 1)
        self.assertFalse(record["condition"]["cross"]["occurred"])
        self.assertEqual(result["hits"], 0)
        forming = run_scan(provider, settings=Settings(include_forming=True, near_ma_gap=None), now=NOW)
        self.assertEqual(forming["hits"], 1)
        self.assertTrue(any("未完成" in warning for warning in forming["warnings"]))

    def test_near_ma_branch_matches_without_current_crossover(self):
        provider = FakeProvider([item(0)], {
            item(0)["symbol"]: bars([10 + index * 0.1 for index in range(120)])})
        result = run_scan(provider, now=NOW)
        self.assertEqual(result["settings"]["near_ma_gap"], 1.0)
        self.assertEqual(result["hits"], 1)
        condition = result["rows"][0]["condition"]
        self.assertEqual(condition["match_type"], "near")
        self.assertTrue(condition["near_match"])
        self.assertFalse(condition["cross"]["occurred"])
        self.assertTrue(condition["bullish"])
        self.assertTrue(condition["ma60_up"])
        self.assertLessEqual(abs(condition["ma"]["5"] - condition["ma"]["10"]), 2.0)
        strict = run_scan(provider, settings=Settings(near_ma_gap=None), now=NOW)
        self.assertEqual(strict["hits"], 0)

    def test_settings_forward_invalid_near_gap_validation(self):
        for threshold in (-1.0, float("nan"), float("inf"), True):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                Settings(near_ma_gap=threshold).validate()

    def test_empty_and_duplicate_lists_fail_with_diagnostics(self):
        for universe in ([], [item(0), item(0)]):
            with self.subTest(universe=universe), self.assertRaises(ScanFailure) as context:
                run_scan(FakeProvider(universe), now=NOW)
            self.assertEqual(context.exception.diagnostic["status"], "failed")

    def test_more_than_five_percent_failures_raise_without_writing_files(self):
        universe = [item(i) for i in range(20)]
        provider = FakeProvider(universe, failures=[item(0)["symbol"], item(1)["symbol"]])
        with patch("builtins.open", side_effect=AssertionError("run_scan不得写文件")):
            with self.assertRaises(ScanFailure) as context:
                run_scan(provider, now=NOW)
        self.assertAlmostEqual(context.exception.diagnostic["success_ratio"], 0.9)
        self.assertEqual(len(context.exception.diagnostic["errors"]), 2)

    def test_exactly_five_percent_failure_is_partial_success(self):
        provider = FakeProvider([item(i) for i in range(20)], failures=[item(0)["symbol"]])
        result = run_scan(provider, now=NOW)
        self.assertFalse(result["complete"])
        self.assertEqual(result["success"], 19)
        self.assertEqual(result["requested"], 20)
        self.assertEqual(result["success_ratio"], 0.95)
        self.assertEqual(result["errors"][0]["symbol"], item(0)["symbol"])
        self.assertTrue(result["warnings"])

    def test_limited_and_specified_scopes_are_explicit(self):
        provider = FakeProvider([item(i) for i in range(5)])
        limited = run_scan(provider, settings=Settings(limit=2), now=NOW)
        self.assertEqual(limited["scope"], "limited_market")
        self.assertEqual(limited["universe_total"], 5)
        self.assertEqual(limited["requested"], 2)
        specified = run_scan(provider, symbols=[item(2)], now=NOW)
        self.assertEqual(specified["scope"], "specified_symbols")
        self.assertEqual(specified["requested"], 1)
        self.assertEqual(specified["reference_method"], "observed_only")

    def test_reference_failure_warns_and_uses_observed_date(self):
        result = run_scan(FakeProvider([item(0)], reference_failure=True), now=NOW)
        self.assertTrue(result["complete"])
        self.assertEqual(result["market_date"], 20261027)
        self.assertIsNone(result["reference_date"])
        self.assertEqual(result["reference_method"], "observed_only")
        self.assertTrue(any("参考取数失败" in warning for warning in result["warnings"]))

    def test_insufficient_history_is_counted_without_fetch_failure(self):
        provider = FakeProvider([item(0)], {item(0)["symbol"]: bars([10.0] * 30)})
        result = run_scan(provider, now=NOW)
        self.assertTrue(result["complete"])
        self.assertEqual(result["insufficient"], 1)
        self.assertEqual(result["success"], 1)
        self.assertEqual(result["hits"], 0)
        self.assertEqual(result["errors"], [])
        self.assertIn("structure", result["records"][0])


if __name__ == "__main__":
    unittest.main()
