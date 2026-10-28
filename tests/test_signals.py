"""Deterministic condition and boundary tests; no network or external packages."""

import math
import unittest

from chan_scanner.signals import evaluate, ma_series


def bars(closes):
    return [{"date": f"bar-{index:03}", "close": close} for index, close in enumerate(closes)]


class MovingAverageTests(unittest.TestCase):
    def test_warmup_and_exact_window_alignment(self):
        result = ma_series(bars([1, 2, 3, 4, 5]), periods=(2, 5, 10))
        self.assertEqual(result["2"], [None, 1.5, 2.5, 3.5, 4.5])
        self.assertEqual(result["5"], [None, None, None, None, 3.0])
        self.assertEqual(result["10"], [None] * 5)
        self.assertEqual(ma_series([], periods=(5,)), {"5": []})

    def test_bars_iterable_and_no_input_mutation(self):
        source = bars(range(1, 64))
        before = [dict(row) for row in source]
        self.assertEqual(ma_series(iter(source)), ma_series(source))
        evaluate(source)
        self.assertEqual(source, before)

    def test_decimal_constant_does_not_create_phantom_crossover_or_trend(self):
        result = evaluate(bars([10.1] * 100), cross_lookback=30)
        self.assertFalse(result["cross"]["occurred"])
        self.assertFalse(result["bullish"])
        self.assertFalse(result["ma60_up"])
        self.assertEqual(result["trend_delta"], 0.0)

    def test_invalid_periods_and_close_prices_are_rejected(self):
        for periods in ((), (0,), (True,), (2.5,), (5, 5), "5"):
            with self.subTest(periods=periods), self.assertRaises(ValueError):
                ma_series(bars([1.0] * 100), periods=periods)
        for close in (float("nan"), float("inf"), -float("inf"), True, None, "bad"):
            with self.subTest(close=close), self.assertRaises(ValueError):
                ma_series(bars([close]))
        with self.assertRaises(ValueError):
            ma_series([{}])


class EvaluateTests(unittest.TestCase):
    @staticmethod
    def matching_closes():
        # MA5 == MA10 at the penultimate bar, then crosses; MA20 > MA60
        # and every final MA60 transition rises because each old price is 10.
        return [10.0] * 60 + [20.0] * 10 + [21.0]

    def test_three_conditions_match_and_equality_before_cross_is_allowed(self):
        source = bars(self.matching_closes())
        result = evaluate(source)
        self.assertTrue(result["matched"])
        self.assertFalse(result["insufficient_bars"])
        self.assertTrue(result["bullish"])
        self.assertTrue(result["ma60_up"])
        self.assertEqual(result["cross"], {
            "occurred": True, "date": "bar-070", "index": 70, "bars_ago": 0
        })
        self.assertEqual(result["reasons"], [])
        self.assertGreater(result["trend_delta"], 0)

    def test_continuing_uptrend_without_recent_cross_is_not_match(self):
        result = evaluate(bars(range(1, 101)))
        self.assertTrue(result["bullish"])
        self.assertTrue(result["ma60_up"])
        self.assertFalse(result["cross"]["occurred"])
        self.assertFalse(result["matched"])

    def test_lookback_is_inclusive_and_uses_most_recent_cross(self):
        source = bars(self.matching_closes() + [22.0, 23.0])
        self.assertFalse(evaluate(source, cross_lookback=2)["matched"])
        result = evaluate(source, cross_lookback=3)
        self.assertTrue(result["matched"])
        self.assertEqual(result["cross"]["bars_ago"], 2)
        self.assertEqual(result["cross"]["index"], 70)

    def test_historical_cross_cannot_override_current_bearish_order(self):
        source = bars(self.matching_closes() + [1.0] * 5)
        result = evaluate(source, cross_lookback=10)
        self.assertTrue(result["cross"]["occurred"])
        self.assertLessEqual(result["ma"]["5"], result["ma"]["10"])
        self.assertFalse(result["bullish"])
        self.assertFalse(result["matched"])

    def test_ma60_total_gain_does_not_replace_each_transition_rising(self):
        source = bars([10.0] * 60 + [30.0, 9.0, 30.0])
        result = evaluate(source)
        self.assertGreater(result["trend_delta"], 0)
        self.assertFalse(result["ma60_up"])
        self.assertFalse(result["matched"])

    def test_flat_ma60_transition_is_not_up(self):
        source = bars([10.0] * 60 + [20.0, 10.0, 20.0])
        result = evaluate(source)
        self.assertGreater(result["trend_delta"], 0)
        self.assertFalse(result["ma60_up"])

    def test_history_minimum_includes_all_trend_transitions(self):
        for count in (0, 1, 10, 59, 60, 61, 62):
            with self.subTest(count=count):
                result = evaluate(bars(range(1, count + 1)))
                self.assertTrue(result["insufficient_bars"])
                self.assertFalse(result["matched"])
                self.assertIsNone(result["trend_delta"])
        result = evaluate(bars(range(1, 64)))
        self.assertFalse(result["insufficient_bars"])
        self.assertTrue(result["ma60_up"])
        self.assertFalse(evaluate(bars(range(1, 64)), trend_bars=4)["ma60_up"])

    def test_optional_bullish_periods_add_real_constraint_and_warmup(self):
        result = evaluate(bars(self.matching_closes()), bullish_periods=(5, 10, 60))
        self.assertTrue(result["matched"])
        self.assertNotIn("20", result["ma"])
        result = evaluate(bars(self.matching_closes()), bullish_periods=(5, 10, 20, 60, 120))
        self.assertTrue(result["insufficient_bars"])
        self.assertFalse(result["matched"])
        self.assertIsNone(result["ma"]["120"])

    def test_invalid_configuration_rejected_before_evaluation(self):
        for kwargs in (
            {"cross_lookback": 0}, {"cross_lookback": True},
            {"trend_bars": 0}, {"trend_bars": 1.5},
            {"bullish_periods": (10, 5, 60)}, {"bullish_periods": (5, 10, 20)},
            {"bullish_periods": (5, 10, 60, 60)},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                evaluate([], **kwargs)

    def test_nonfinite_old_price_is_not_silently_ignored(self):
        source = bars([math.nan] + list(range(1, 101)))
        with self.assertRaises(ValueError):
            evaluate(source)


class NearMovingAverageTests(unittest.TestCase):
    @staticmethod
    def nearby_closes(gap=0.0):
        # Latest MA5-MA10 = (last-five price - preceding-five price) / 2.
        # The latest cross is absent, while MA10 > MA20 > MA60 and MA60 rises.
        return [10.0] * 100 + [20.0] * 10 + [30.0] * 3 + [30.1 - 2 * gap, 29.9 + 2 * gap] + [30.0 + 2 * gap] * 5

    def test_absolute_price_gap_boundaries_include_positive_and_negative_two(self):
        for gap in (2.0, -2.0, 1.999999, -1.999999, 0.0):
            with self.subTest(gap=gap):
                result = evaluate(bars(self.nearby_closes(gap)), near_ma_gap=2)
                self.assertTrue(result["matched"])
                self.assertTrue(result["near_match"])
                self.assertFalse(result["strict_match"])
                self.assertFalse(result["cross"]["occurred"])
                self.assertAlmostEqual(result["ma5_ma10_gap"], gap)
                self.assertAlmostEqual(result["ma5_ma10_gap_abs"], abs(gap))
                self.assertEqual(result["near_ma_gap"], 2.0)
                self.assertIsInstance(result["near_ma_gap"], float)
                self.assertEqual(result["match_type"], "near")
                self.assertEqual(result["match_label"], "均线接近")
                self.assertEqual(result["reasons"], [])

    def test_gap_outside_either_boundary_is_not_added_without_strict_cross(self):
        for gap in (2.000001, -2.000001, 3.0, -3.0):
            with self.subTest(gap=gap):
                result = evaluate(bars(self.nearby_closes(gap)), near_ma_gap=2)
                self.assertFalse(result["matched"])
                self.assertFalse(result["near_match"])
                self.assertFalse(result["strict_match"])
                self.assertIsNone(result["match_type"])
                self.assertIsNone(result["match_label"])
                self.assertTrue(result["reasons"])

    def test_boundary_tolerates_only_floating_point_noise(self):
        for gap in (2.0 + 1e-13, -2.0 - 1e-13):
            with self.subTest(gap=gap):
                self.assertTrue(evaluate(bars(self.nearby_closes(gap)), near_ma_gap=2)["near_match"])
        for gap in (2.0 + 1e-9, -2.0 - 1e-9):
            with self.subTest(gap=gap):
                self.assertFalse(evaluate(bars(self.nearby_closes(gap)), near_ma_gap=2)["near_match"])

    def test_below_ma10_with_no_cross_is_admitted_and_facts_stay_truthful(self):
        # Absolute 2 currency units qualifies even though the relative gap is 40%.
        source = bars([1.0] * 100 + [2.0] * 10 + [7.0] * 3 + [13.0, 1.0] + [3.0] * 5)
        result = evaluate(source, near_ma_gap=2)
        self.assertEqual(result["ma"]["5"], 3.0)
        self.assertEqual(result["ma"]["10"], 5.0)
        self.assertEqual(result["ma5_ma10_gap"], -2.0)
        self.assertTrue(result["matched"])
        self.assertTrue(result["near_match"])
        self.assertFalse(result["bullish"])
        self.assertFalse(result["cross"]["occurred"])
        self.assertTrue(result["lower_bullish"])
        self.assertTrue(result["ma60_up"])
        self.assertEqual(result["reasons"], [])
        self.assertTrue(result["strict_reasons"])

    def test_zero_threshold_requires_equal_averages(self):
        equal = evaluate(bars(self.nearby_closes()), near_ma_gap=0)
        self.assertTrue(equal["near_match"])
        self.assertFalse(equal["bullish"])
        unequal = evaluate(bars(self.nearby_closes(-0.001)), near_ma_gap=0)
        self.assertFalse(unequal["matched"])

    def test_none_keeps_original_strict_library_default(self):
        source = bars(self.nearby_closes(0.5))
        default = evaluate(source)
        self.assertEqual(default, evaluate(source, near_ma_gap=None))
        self.assertTrue(default["bullish"])
        self.assertFalse(default["matched"])
        self.assertFalse(default["near_match"])
        self.assertIsNone(default["near_ma_gap"])
        self.assertTrue(evaluate(source, near_ma_gap=2)["matched"])

    def test_true_cross_wider_than_two_is_retained(self):
        source = bars([10.0] * 60 + [20.0] * 10 + [50.0])
        result = evaluate(source, near_ma_gap=2)
        self.assertEqual(result["ma5_ma10_gap"], 3.0)
        self.assertTrue(result["matched"])
        self.assertTrue(result["strict_match"])
        self.assertFalse(result["near_match"])
        self.assertEqual(result["match_type"], "cross")
        self.assertEqual(result["match_label"], "金叉")
        self.assertEqual(result["reasons"], [])

    def test_true_cross_label_takes_precedence_when_both_branches_match(self):
        result = evaluate(bars(EvaluateTests.matching_closes()), near_ma_gap=2)
        self.assertTrue(result["strict_match"])
        self.assertTrue(result["near_match"])
        self.assertEqual(result["match_type"], "cross")
        self.assertEqual(result["match_label"], "金叉")

    def test_near_branch_cannot_bypass_flat_or_falling_ma60(self):
        for outgoing in (30.0, 31.0):
            source = self.nearby_closes()
            source[57:60] = [outgoing] * 3
            with self.subTest(outgoing=outgoing):
                result = evaluate(bars(source), near_ma_gap=2)
                self.assertEqual(result["ma5_ma10_gap_abs"], 0.0)
                self.assertTrue(result["lower_bullish"])
                self.assertFalse(result["ma60_up"])
                self.assertFalse(result["near_match"])
                self.assertFalse(result["matched"])
                self.assertEqual(result["reasons"], ["MA60 未连续 3 次上升"])

    def test_near_branch_cannot_bypass_ma10_ma20_ma60_order(self):
        source = bars([10.0] * 100 + [50.0] * 10 + [30.0] * 10)
        result = evaluate(source, near_ma_gap=2)
        self.assertEqual(result["ma5_ma10_gap_abs"], 0.0)
        self.assertTrue(result["ma60_up"])
        self.assertLess(result["ma"]["10"], result["ma"]["20"])
        self.assertFalse(result["lower_bullish"])
        self.assertFalse(result["near_match"])
        self.assertFalse(result["matched"])

    def test_near_branch_obeys_all_remaining_configured_periods(self):
        source = bars([10.0] * 100 + [5.0] * 5 + [50.0] * 5 + [30.0] * 3 + [59.0, 1.0] + [30.0] * 5)
        self.assertTrue(evaluate(source, near_ma_gap=2)["near_match"])
        result = evaluate(source, near_ma_gap=2, bullish_periods=(5, 10, 15, 20, 60))
        self.assertGreater(result["ma"]["15"], result["ma"]["10"])
        self.assertFalse(result["lower_bullish"])
        self.assertFalse(result["near_match"])
        self.assertFalse(result["matched"])

    def test_history_shortage_blocks_both_branches(self):
        for count in (0, 9, 10, 59, 60, 62):
            with self.subTest(count=count):
                result = evaluate(bars([10.0] * count), near_ma_gap=2)
                self.assertTrue(result["insufficient_bars"])
                self.assertFalse(result["near_match"])
                self.assertFalse(result["strict_match"])
                self.assertFalse(result["matched"])
        short_for_period = evaluate(bars(self.nearby_closes()[30:]), near_ma_gap=2,
                                    bullish_periods=(5, 10, 20, 60, 120))
        self.assertTrue(short_for_period["insufficient_bars"])
        self.assertFalse(short_for_period["near_match"])
        self.assertFalse(short_for_period["matched"])

    def test_invalid_thresholds_are_rejected_before_reading_bars(self):
        for value in (True, False, -1, -1e-13, math.nan, math.inf, -math.inf,
                      "2", [], {}, 2j, 10 ** 1000):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                evaluate([], near_ma_gap=value)


if __name__ == "__main__":
    unittest.main()


class SlopeTests(unittest.TestCase):
    def test_each_slope_can_independently_block_near_branch(self):
        base = NearMovingAverageTests.nearby_closes(0.5)
        self.assertTrue(evaluate(bars(base), near_ma_gap=1)["matched"])
        for period in (5, 20):
            for delta in (0, -0.1):
                source = base.copy()
                source[-period-1] = source[-1] - delta * period
                result = evaluate(bars(source), near_ma_gap=1)
                self.assertFalse(result[f"ma{period}_up"])
                self.assertAlmostEqual(result["ma_slopes"][str(period)], delta)
                self.assertFalse(result["matched"])
                self.assertTrue(any(f"MA{period} 最新斜率" in r for r in result["reasons"]))

    def test_cross_cannot_bypass_nonpositive_ma20_slope(self):
        source = EvaluateTests.matching_closes()
        source[-21] = source[-1]
        result = evaluate(bars(source), near_ma_gap=1)
        self.assertTrue(result["cross"]["occurred"])
        self.assertTrue(result["bullish"])
        self.assertTrue(result["ma5_up"])
        self.assertFalse(result["ma20_up"])
        self.assertFalse(result["strict_match"])
        self.assertFalse(result["near_match"])
