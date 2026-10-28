import csv
import io
import json
from pathlib import Path
import re
import tempfile
import unittest

from chan_scanner.cli import FileData, demo_data, main
from chan_scanner.report import csv_text, html_text, save_result
from chan_scanner.scanner import run_scan


class ReportTests(unittest.TestCase):
    def result(self):
        provider = FileData(demo_data(), demo=True)
        return run_scan(provider, symbols=provider.list_symbols())

    def test_offline_reversed_history_is_sorted_before_count(self):
        rows = demo_data()
        rows[0]["bars"].reverse()
        provider = FileData(rows)
        bars = provider.daily_bars("sh600000", count=80)
        self.assertEqual(bars[-1]["close"], 21)
        self.assertEqual(len(bars), 80)
        self.assertEqual(bars, sorted(bars, key=lambda x: x["date"]))

    def test_html_escapes_untrusted_json_and_roundtrips(self):
        payload = self.result()
        payload["rows"][0]["name"] = '</script><script>alert("x")</script>&'
        html = html_text(payload)
        self.assertNotIn('</script><script>alert', html)
        blob = re.search(r'<script type="application/json" id="scan-data">(.*?)</script>', html, re.S)[1]
        self.assertEqual(json.loads(blob)["rows"][0]["name"], payload["rows"][0]["name"])
        self.assertNotIn("records", json.loads(blob))

    def test_csv_formula_safety_and_no_phantom_provisional_stroke(self):
        payload = self.result()
        payload["rows"][0]["name"] = "=1+1"
        payload["rows"][0]["structure"]["strokes"] = []
        rows = list(csv.reader(io.StringIO(csv_text(payload).lstrip("\ufeff"))))
        self.assertEqual(rows[1][1], "'=1+1")
        self.assertEqual(rows[1][-1], "False")

    def test_near_ma_result_exports_its_type_and_gap_without_inventing_crossover(self):
        rows = demo_data()
        rows[0]["bars"][-1].update(open=20.0, high=20.4, low=19.6, close=20.0)
        rows[0]["bars"][-6].update(open=19, high=19.4, low=18.6, close=19)
        rows[0]["bars"][-7].update(open=21, high=21.4, low=20.6, close=21)
        provider = FileData(rows, demo=True)
        payload = run_scan(provider, symbols=provider.list_symbols())
        self.assertEqual(payload["hits"], 1)
        condition = payload["rows"][0]["condition"]
        self.assertFalse(condition["cross"]["occurred"])
        self.assertEqual(condition["match_type"], "near")
        records = list(csv.DictReader(io.StringIO(csv_text(payload).lstrip("\ufeff"))))
        self.assertEqual(records[0]["入选类型"], "均线接近")
        self.assertEqual(float(records[0]["两线绝对差(元)"]), 0.0)
        self.assertEqual(records[0]["金叉日期"], "")

    def test_publish_creates_immutable_runs_and_latest_pointer(self):
        payload = self.result()
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            first = save_result(payload, folder)
            second = save_result(payload, folder)
            self.assertNotEqual(first, second)
            latest = json.loads((Path(folder) / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["run"], second.name)
            for name in ("result.json", "matches.csv", "report.html", "structures.jsonl"):
                self.assertTrue((first / name).is_file())
            structure = json.loads((first / "structures.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertIn("structure", structure)
            self.assertNotIn("bars", structure)

    def test_cli_bad_input_preserves_previous_latest(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            root = Path(folder)
            (root / "latest.json").write_text('{"keep":true}', encoding="utf-8")
            self.assertEqual(main(["--codes", "bad", "--output-dir", folder]), 2)
            self.assertEqual(json.loads((root / "latest.json").read_text()), {"keep": True})


if __name__ == "__main__":
    unittest.main()
