"""Exercise the actual loopback HTTP service, including background publication."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from chan_scanner.cli import FileData, demo_data
from chan_scanner.report import save_result
from chan_scanner.scanner import ScanFailure, Settings, run_scan
from chan_scanner.server import create_server, main


def payload(settings=None):
    provider = FileData(demo_data(), demo=True)
    return run_scan(provider=provider, symbols=provider.list_symbols(), settings=settings)


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.server = None
        self.thread = None
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        if self.server:
            deadline = time.monotonic() + 5
            while self.server.app.snapshot()["state"] == "running" and time.monotonic() < deadline:
                time.sleep(0.01)
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(2)
        self.directory.cleanup()

    def start(self, **kwargs):
        self.server = create_server(self.root, port=0, **kwargs)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def request(self, method="GET", path="/", body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        actual = dict(headers or {})
        if method == "POST":
            actual.setdefault("Content-Type", "application/json")
            actual.setdefault("X-Scanner-Request", "1")
            body = "{}" if body is None else body
        connection.request(method, path, body=body, headers=actual)
        response = connection.getresponse()
        status, response_headers, data = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return status, response_headers, data

    def status(self):
        status, _, data = self.request(path="/api/scan/status?fresh=1")
        self.assertEqual(status, 200)
        return json.loads(data)

    def wait_done(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = self.status()
            if status["state"] != "running":
                return status
            time.sleep(0.01)
        self.fail("后台扫描未在测试期限内完成")

    def test_empty_dashboard_health_and_no_automatic_scan(self):
        self.start(demo=True)
        status, headers, html = self.request()
        self.assertEqual(status, 200)
        self.assertIn("尚无扫描结果".encode(), html)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.status()["state"], "idle")
        self.assertFalse((self.root / "latest.json").exists())
        _, _, data = self.request(path="/api/health")
        self.assertEqual(json.loads(data)["service"], "chan-ma-scanner")

    def test_background_start_duplicate_progress_and_success(self):
        result = payload()
        entered = threading.Event()

        def runner(**kwargs):
            kwargs["progress"]("scan", 1, 2)
            entered.set()
            self.release.wait(3)
            return result

        self.start(runner=runner)
        _, _, data = self.request("POST", "/api/scan?request=1")
        self.assertTrue(json.loads(data)["started"])
        self.assertTrue(entered.wait(2))
        self.assertEqual(self.status()["done"], 1)
        _, _, data = self.request("POST", "/api/scan")
        self.assertFalse(json.loads(data)["started"])
        self.release.set()
        status = self.wait_done()
        self.assertEqual(status["state"], "success")
        self.assertTrue(status["report_url"].startswith("/results/"))
        code, _, html = self.request(path=status["report_url"])
        self.assertEqual(code, 200)
        self.assertIn("synthetic_demo", html.decode())
        self.assertTrue((self.root / "latest.json").is_file())

    def test_rescan_uses_last_successful_settings(self):
        save_result(payload(Settings(trend_bars=2, workers=1)), self.root)
        captured = []

        def runner(**kwargs):
            captured.append(kwargs["settings"])
            return payload(kwargs["settings"])

        self.start(runner=runner)
        self.request("POST", "/api/scan")
        self.assertEqual(self.wait_done()["state"], "success")
        self.assertEqual(captured[0].trend_bars, 2)
        self.assertEqual(captured[0].workers, 1)
        self.assertIsInstance(captured[0].bullish_periods, tuple)

    def test_failure_preserves_old_latest_and_writes_diagnostic(self):
        save_result(payload(), self.root)
        before = (self.root / "latest.json").read_bytes()

        def runner(**_kwargs):
            raise ScanFailure("模拟列表失败", {"status": "failed", "stage": "list", "error": "offline"})

        self.start(runner=runner)
        self.request("POST", "/api/scan")
        status = self.wait_done()
        self.assertEqual(status["state"], "failed")
        self.assertIn("模拟列表失败", status["error"])
        self.assertEqual((self.root / "latest.json").read_bytes(), before)
        diagnostic = json.loads((self.root / "attempt.json").read_text(encoding="utf-8"))
        self.assertEqual(diagnostic["status"], "failed")
        self.assertEqual(diagnostic["stage"], "list")
        self.assertEqual(self.request()[0], 200)

    def test_rescan_removes_previous_trial_limit_but_keeps_ma_rules(self):
        settings = Settings(limit=10, count=300, workers=2, cross_lookback=3,
                            trend_bars=2, bullish_periods=(5, 10, 60))
        save_result(payload(settings), self.root)
        captured = []

        def runner(**kwargs):
            captured.append(kwargs["settings"])
            return payload(kwargs["settings"])

        self.start(runner=runner)
        self.request("POST", "/api/scan")
        self.assertEqual(self.wait_done()["state"], "success")
        actual = captured[0]
        self.assertEqual(actual.limit, 0)
        self.assertEqual(actual.count, 120)
        self.assertEqual(actual.workers, 2)
        self.assertEqual(actual.cross_lookback, 3)
        self.assertEqual(actual.trend_bars, 2)
        self.assertEqual(actual.bullish_periods, (5, 10, 60))

    def test_legacy_800_bar_report_rescans_only_120_with_no_trial_limit(self):
        legacy = payload(Settings(count=800, limit=10))
        legacy["settings"].pop("near_ma_gap", None)
        save_result(legacy, self.root)
        captured = []

        def runner(**kwargs):
            captured.append(kwargs["settings"])
            return payload(kwargs["settings"])

        self.start(runner=runner)
        self.request("POST", "/api/scan")
        self.assertEqual(self.wait_done()["state"], "success")
        self.assertEqual(captured[0].count, 120)
        self.assertEqual(captured[0].limit, 0)
        self.assertEqual(captured[0].near_ma_gap, 1.0)

    def test_rescan_preserves_explicit_strict_cross_setting(self):
        save_result(payload(Settings(count=800, limit=10, near_ma_gap=None)), self.root)
        captured = []

        def runner(**kwargs):
            captured.append(kwargs["settings"])
            return payload(kwargs["settings"])

        self.start(runner=runner)
        self.request("POST", "/api/scan")
        self.assertEqual(self.wait_done()["state"], "success")
        self.assertIsNone(captured[0].near_ma_gap)
        self.assertEqual(captured[0].count, 120)
        self.assertEqual(captured[0].limit, 0)

    def test_provider_oversupply_is_trimmed_before_actual_analysis(self):
        source = demo_data()[0]

        class OversupplyingProvider:
            provenance = {"provider": "synthetic_demo", "adjustment": "fixture"}

            def list_symbols(self):
                return [{"symbol": source["symbol"], "name": source["name"]}]

            def daily_bars(self, symbol, count=120):
                # Deliberately ignore the requested count, as some providers do.
                return source["bars"]

        self.assertGreater(len(source["bars"]), 120)
        self.start(provider=OversupplyingProvider())
        self.request("POST", "/api/scan")
        self.assertEqual(self.wait_done()["state"], "success")
        run = json.loads((self.root / "latest.json").read_text(encoding="utf-8"))["run"]
        result = json.loads((self.root / run / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["settings"]["count"], 120)
        self.assertEqual(result["rows"][0]["bar_count"], 120)
        self.assertEqual(len(result["rows"][0]["bars"]), 120)

    def test_reports_are_regenerated_from_json_and_downloads_are_available(self):
        run = save_result(payload(), self.root)
        (run / "report.html").write_text("OUTDATED_THEME", encoding="utf-8")
        self.start()
        for route in ("/", f"/results/{run.name}/report.html"):
            status, _, html = self.request(path=route)
            self.assertEqual(status, 200)
            self.assertNotIn(b"OUTDATED_THEME", html)
        for file in ("result.json", "matches.csv", "structures.jsonl"):
            status, headers, data = self.request(path=f"/results/{run.name}/{file}")
            self.assertEqual(status, 200)
            self.assertTrue(data)
            self.assertEqual(headers["Cache-Control"], "no-store")

    def test_origin_host_and_file_preflight_policy(self):
        self.start(demo=True)
        port = self.server.server_port
        for origin in ("null", f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            status, headers, _ = self.request(path="/api/scan/status", headers={"Origin": origin})
            self.assertEqual(status, 200)
            self.assertEqual(headers["Access-Control-Allow-Origin"], origin)
        for headers in ({"Origin": "https://evil.example"}, {"Host": f"evil.example:{port}"},
                        {"Origin": "http://127.0.0.1:1"}):
            self.assertEqual(self.request("POST", "/api/scan", headers=headers)[0], 403)
        status, headers, _ = self.request("OPTIONS", "/api/scan", headers={
            "Origin": "null", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-scanner-request"})
        self.assertEqual(status, 204)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "null")
        self.assertIn("X-Scanner-Request", headers["Access-Control-Allow-Headers"])
        self.assertEqual(self.status()["state"], "idle")

    def test_post_requires_marker_json_empty_object_and_bounded_body(self):
        self.start()
        self.assertEqual(self.request("POST", "/api/scan", headers={"X-Scanner-Request": "0"})[0], 415)
        self.assertEqual(self.request("POST", "/api/scan", headers={"Content-Type": "text/plain"})[0], 415)
        for body in ("[]", "null", '{"workers":32}', "bad"):
            self.assertEqual(self.request("POST", "/api/scan", body=body)[0], 400)
        self.assertEqual(self.request("POST", "/api/scan", body=" " * 8193)[0], 413)
        self.assertEqual(self.status()["state"], "idle")

    def test_results_path_cannot_escape_output_directory(self):
        self.start()
        for route in ("/results/../result.json", "/results/%2e%2e/result.json",
                      "/results/foo/../../secret.json", "/results/foo/other.py", "/latest.json"):
            self.assertIn(self.request(path=route)[0], (400, 403, 404))

    def test_demo_scan_runs_real_pipeline_and_marks_synthetic_data(self):
        self.start(demo=True)
        self.request("POST", "/api/scan", headers={"Origin": "null"})
        status = self.wait_done()
        self.assertEqual(status["state"], "success")
        run = json.loads((self.root / "latest.json").read_text(encoding="utf-8"))["run"]
        result = json.loads((self.root / run / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["source"]["provider"], "synthetic_demo")
        self.assertEqual(result["hits"], 1)

    def test_main_reuses_same_healthy_service_and_rejects_different_directory(self):
        self.start(demo=True)
        arguments = ["--port", str(self.server.server_port), "--output-dir", str(self.root),
                     "--no-browser", "--demo"]
        self.assertEqual(main(arguments), 0)
        with tempfile.TemporaryDirectory() as other:
            self.assertEqual(main(["--port", str(self.server.server_port), "--output-dir", other,
                                   "--no-browser", "--demo"]), 1)


if __name__ == "__main__":
    unittest.main()
