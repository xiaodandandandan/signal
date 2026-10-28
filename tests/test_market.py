import copy
from datetime import datetime, timezone
import io
import json
import ssl
import unittest
from unittest.mock import patch
import urllib.error

from chan_scanner.market import (
    CHINA_TZ, MarketData, MarketDataError, MarketListError,
    completed_daily_bars, normalize_symbol, validate_bars,
)


def bar(day=20261027, **changes):
    result = {"date": day, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 300}
    result.update(changes)
    return result


def stock(code="600000.SH", name="浦发银行"):
    return {"SECUCODE": code, "SECURITY_CODE": code[:6], "SECURITY_NAME_ABBR": name}


def page(rows, pages=1, count=None):
    return {"success": True, "result": {"data": rows, "pages": pages,
                                        "count": len(rows) if count is None else count}}


class MarketValidationTests(unittest.TestCase):
    def test_symbols_preserve_exchange_and_bse_920(self):
        self.assertEqual(normalize_symbol("000001"), "sz000001")
        self.assertEqual(normalize_symbol("SH000001"), "sh000001")
        self.assertEqual(normalize_symbol("920001"), "bj920001")
        self.assertEqual(normalize_symbol("830001"), "bj830001")
        with self.assertRaises(ValueError):
            normalize_symbol(1)
        with self.assertRaises(ValueError):
            normalize_symbol("../../600000")

    def test_bars_sorted_copied_and_standardized(self):
        original = [bar("2026-10-28"), bar("20261027", close="10.5")]
        before = copy.deepcopy(original)
        result = validate_bars(original)
        self.assertEqual([row["date"] for row in result], [20261027, 20261028])
        self.assertEqual(result[0]["close"], 10.5)
        self.assertEqual(original, before)
        result[0]["close"] = 100
        self.assertEqual(original, before)

    def test_duplicate_date_in_mixed_formats_rejected(self):
        with self.assertRaisesRegex(ValueError, "重复日期"):
            validate_bars([bar(20261027), bar("2026-10-27")])

    def test_invalid_ohlcv_and_dates_rejected(self):
        for bad in [bar(close=float("nan")), bar(high=float("inf")),
                    bar(volume=-1), bar(high=10), bar(low=11),
                    bar(open=0), bar(close=True), bar(day="2026-02-30")]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_bars([bad])

    def test_completed_gate_uses_china_time(self):
        rows = [bar(20261027), bar(20261028), bar(20261029)]
        kept, dropped = completed_daily_bars(rows, datetime(2026, 10, 28, 6, 59, tzinfo=timezone.utc))
        self.assertEqual([row["date"] for row in kept], [20261027])
        self.assertEqual(dropped, 2)
        kept, dropped = completed_daily_bars(rows, datetime(2026, 10, 28, 7, 0, tzinfo=timezone.utc))
        self.assertEqual([row["date"] for row in kept], [20261027, 20261028])
        self.assertEqual(dropped, 1)

    def test_forming_opt_in_still_excludes_future(self):
        kept, dropped = completed_daily_bars([bar(20261027), bar(20261028), bar(20261029)],
                                             datetime(2026, 10, 28, 10), True)
        self.assertEqual([row["date"] for row in kept], [20261027, 20261028])
        self.assertEqual(dropped, 1)


class MarketUniverseTests(unittest.TestCase):
    def setUp(self):
        self.market = MarketData()
        self.rows = [stock(), stock("000001.SZ", "平安银行"), stock("920001.BJ", "北交样本")]

    def test_complete_universe_contains_bse(self):
        with patch.object(self.market, "_get", return_value=page(self.rows)):
            result = self.market.list_symbols()
        self.assertEqual({row["symbol"] for row in result}, {"sh600000", "sz000001", "bj920001"})

    def test_all_pages_fetched_and_ordered(self):
        rows = self.rows + [stock("%06d.SH" % (600001 + i)) for i in range(498)]
        responses = [page(rows[:500], 2, 501), page(rows[500:], 2, 501)]
        with patch.object(self.market, "_get", side_effect=responses) as get:
            result = self.market.list_symbols()
        self.assertEqual(len(result), 501)
        self.assertEqual(get.call_count, 2)
        self.assertIn("pageNumber=2", get.call_args_list[1].args[0])

    def test_missing_exchange_is_not_full_market(self):
        with patch.object(self.market, "_get", return_value=page(self.rows[:2])):
            with self.assertRaisesRegex(MarketListError, "沪深北"):
                self.market.list_symbols()

    def test_duplicate_symbol_rejected(self):
        with patch.object(self.market, "_get", return_value=page(self.rows + self.rows[:1])):
            with self.assertRaisesRegex(MarketListError, "重复"):
                self.market.list_symbols()

    def test_failure_and_empty_page_rejected(self):
        for response in [None, {"success": False}, page([]), page(self.rows, 2, 501)]:
            with self.subTest(response=response):
                with patch.object(self.market, "_get", return_value=response):
                    with self.assertRaises(MarketListError):
                        self.market.list_symbols()

    def test_later_page_failure_and_changed_metadata_rejected(self):
        rows = self.rows + [stock("%06d.SH" % (600001 + i)) for i in range(497)]
        for later in [MarketDataError("offline"), page([stock("600999.SH")], 2, 502)]:
            with patch.object(self.market, "_get", side_effect=[page(rows, 2, 501), later]):
                with self.assertRaises(MarketListError):
                    self.market.list_symbols()

    def test_daily_parse_and_null_response(self):
        payload = [{"day": "2026-10-27 15:00:00", "open": "10", "close": "11",
                    "high": "12", "low": "9", "volume": "300"}]
        with patch.object(self.market, "_get", return_value=payload):
            result = self.market.daily_bars("600000")
        self.assertEqual(result, [bar()])
        with patch.object(self.market, "_get", return_value=None):
            with self.assertRaises(MarketDataError):
                self.market.daily_bars("600000")

    def test_request_retries_transient_but_not_tls(self):
        market = MarketData(retries=2)
        with patch("chan_scanner.market.urllib.request.urlopen", side_effect=[
            urllib.error.HTTPError("url", 429, "rate limit", None, None),
            io.BytesIO(json.dumps({"ok": True}).encode()),
        ]) as request, patch("chan_scanner.market.time.sleep"):
            self.assertEqual(market._get("https://example.test", "https://example.test"), {"ok": True})
            self.assertEqual(request.call_count, 2)
        with patch("chan_scanner.market.urllib.request.urlopen", side_effect=
                   urllib.error.URLError(ssl.SSLCertVerificationError("bad cert"))) as request:
            with self.assertRaises(MarketDataError):
                market._get("https://example.test", "https://example.test")
            self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
