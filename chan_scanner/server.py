"""Loopback-only dashboard and background scan service (Python standard library)."""
from dataclasses import asdict, fields
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
from pathlib import Path
import re
import socket
import sys
import threading
import urllib.error
import urllib.request
from urllib.parse import unquote, urlsplit
import webbrowser

from .cli import FileData, demo_data
from .report import atomic_text, html_text, json_text, save_result
from .scanner import CHINA, ScanFailure, Settings, run_scan


MAX_BODY = 8192
RUN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\Z")
FILES = {"report.html": "text/html; charset=utf-8", "result.json": "application/json; charset=utf-8",
         "matches.csv": "text/csv; charset=utf-8", "structures.jsonl": "application/x-ndjson; charset=utf-8"}


def _empty_payload(demo=False):
    return {"schema_version": 1, "generated": datetime.now(CHINA).isoformat(timespec="seconds"),
            "scope": "specified_symbols" if demo else "all_a_shares", "frequency": "day",
            "market_date": None, "reference_date": None, "reference_method": "not_scanned",
            "universe_total": 0, "requested": 0, "success": 0, "errors": [], "complete": False,
            "success_ratio": 0, "insufficient": 0, "stale": 0, "hits": 0,
            "rows": [], "records": [], "settings": asdict(Settings()),
            "source": {"provider": "synthetic_demo" if demo else "not_scanned"},
            "warnings": ["尚无扫描结果，点击“重新扫描”开始。"], "elapsed_seconds": 0}


class DashboardState:
    def __init__(self, output_dir, runner=None, provider=None, demo=False):
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.runner = runner or run_scan
        self.provider = provider
        self.demo = bool(demo)
        self.lock = threading.Lock()
        self.status = {"state": "idle", "stage": "idle", "done": 0, "total": 0,
                       "message": "点击重新扫描开始", "report_url": None, "error": None}
        latest = self.latest_run()
        if latest:
            self.status["report_url"] = "/results/" + latest.name + "/report.html"

    def run_path(self, run_name):
        if not isinstance(run_name, str) or not RUN_NAME.fullmatch(run_name) or run_name in (".", ".."):
            raise ValueError("扫描批次名称无效")
        result = (self.output_dir / run_name).resolve()
        if result.parent != self.output_dir:
            raise ValueError("扫描批次不在结果文件夹内")
        return result

    def latest_run(self):
        pointer = self.output_dir / "latest.json"
        if not pointer.exists():
            return None
        value = json.loads(pointer.read_text(encoding="utf-8-sig"))
        run = self.run_path(value.get("run"))
        if not (run / "result.json").is_file():
            raise ValueError("最近扫描结果文件缺失")
        return run

    def read_payload(self, run=None):
        run = run if run is not None else self.latest_run()
        if run is None:
            return _empty_payload(self.demo)
        result = (run / "result.json").resolve()
        if result.parent != run or not result.is_relative_to(self.output_dir):
            raise ValueError("结果文件不在扫描文件夹内")
        value = json.loads(result.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("扫描结果须为JSON对象")
        return value

    def snapshot(self):
        with self.lock:
            return dict(self.status)

    def start(self):
        with self.lock:
            if self.status["state"] == "running":
                return {"started": False, **self.status}
            payload = self.read_payload()
            allowed = {field.name for field in fields(Settings)}
            values = {key: value for key, value in payload.get("settings", {}).items() if key in allowed}
            if "bullish_periods" in values:
                values["bullish_periods"] = tuple(values["bullish_periods"])
            # The dashboard button always requests the full universe; a prior
            # command-line trial limit must never silently constrain a rescan.
            values["limit"] = 0
            values["count"] = 120
            values.setdefault("near_ma_gap", 1.0)
            settings = Settings(**values).validate()
            self.status.update(state="running", stage="starting", done=0, total=0,
                               message="准备扫描日K行情…", error=None)
            worker = threading.Thread(target=self._work, args=(settings,), name="daily-ma-scan", daemon=True)
            worker.start()
            return {"started": True, **self.status}

    def _work(self, settings):
        pointer = self.output_dir / "latest.json"
        previous_latest, previous_read = None, False

        def progress(stage, done, total):
            message = "读取沪深北股票列表与交易日…" if stage == "list" else f"日K分析：{done}/{total}"
            with self.lock:
                self.status.update(stage=stage, done=done, total=total, message=message)

        try:
            previous_latest = pointer.read_text(encoding="utf-8-sig") if pointer.exists() else None
            previous_read = True
            symbols = None
            provider = self.provider
            if self.demo:
                provider = FileData(demo_data(), demo=True)
                symbols = provider.list_symbols()
            payload = self.runner(provider=provider, symbols=symbols, settings=settings, progress=progress)
            run = save_result(payload, self.output_dir)
            with self.lock:
                self.status.update(state="success", stage="done", done=payload["requested"],
                                   total=payload["requested"], message=f"扫描完成：命中 {payload['hits']} 只",
                                   report_url="/results/" + run.name + "/report.html", error=None,
                                   hits=payload["hits"], success=payload["success"],
                                   complete=payload["complete"], market_date=payload["market_date"])
        except Exception as exc:
            diagnostic = dict(exc.diagnostic) if isinstance(exc, ScanFailure) else {
                "generated": datetime.now(CHINA).isoformat(timespec="seconds"),
                "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            error = str(exc)
            try:
                # A publication error must also leave the old latest pointer intact.
                if previous_read and previous_latest is not None:
                    atomic_text(pointer, previous_latest)
                elif previous_read and pointer.exists():
                    pointer.unlink()
                atomic_text(self.output_dir / "attempt.json", json_text(diagnostic))
            except OSError as write_error:
                error += "; 扫描诊断保存失败: " + str(write_error)
            with self.lock:
                self.status.update(state="failed", stage="failed", message="扫描失败，保留上次结果",
                                   error=error)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False

    def __init__(self, address, app):
        self.app = app
        super().__init__(address, DashboardHandler)

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "ChanMAScanner/1.0"

    def log_message(self, *_args):
        pass

    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        raw = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        origin = getattr(self, "allowed_origin", None)
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Scanner-Request")
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, status, value):
        self._send(status, json_text(value))

    def _check(self):
        self.allowed_origin = None
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            hosts.update(("127.0.0.1", "localhost"))
        hosts_raw = self.headers.get_all("Host", [])
        if len(hosts_raw) != 1 or hosts_raw[0].strip().lower() not in hosts:
            self._json(403, {"error": "Host须为本机服务地址"})
            return False
        origins = self.headers.get_all("Origin", [])
        if len(origins) > 1:
            self._json(403, {"error": "Origin无效"})
            return False
        origin = origins[0] if origins else None
        if origin is not None:
            allowed = {"null", f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
            if origin not in allowed:
                self._json(403, {"error": "仅允许同源页面或本机文件页面"})
                return False
            self.allowed_origin = origin
        parsed = urlsplit(self.path)
        if parsed.scheme or parsed.netloc:
            self._json(400, {"error": "请求路径无效"})
            return False
        self.route = unquote(parsed.path)
        return True

    def do_OPTIONS(self):
        if not self._check():
            return
        requested = self.headers.get("Access-Control-Request-Method", "")
        headers = {h.strip().lower() for h in self.headers.get("Access-Control-Request-Headers", "").split(",") if h.strip()}
        if (not self.allowed_origin or self.route not in ("/api/scan", "/api/scan/status", "/api/health")
                or requested not in ("GET", "POST")
                or not headers.issubset({"content-type", "x-scanner-request"})):
            self._json(403, {"error": "预检请求不被允许"})
            return
        self._send(204, b"")

    def do_GET(self):
        if not self._check():
            return
        app = self.server.app
        try:
            if self.route == "/api/health":
                self._json(200, {"service": "chan-ma-scanner", "output_dir": str(app.output_dir),
                                 "demo": app.demo, "state": app.snapshot()["state"]})
            elif self.route == "/api/scan/status":
                self._json(200, app.snapshot())
            elif self.route == "/":
                self._send(200, html_text(app.read_payload()), FILES["report.html"])
            elif self.route.startswith("/results/"):
                parts = self.route.split("/")
                if len(parts) != 4 or parts[3] not in FILES:
                    self._json(404, {"error": "文件不存在"})
                    return
                run = app.run_path(parts[2])
                file = (run / parts[3]).resolve()
                if file.parent != run or not file.is_relative_to(app.output_dir):
                    self._json(403, {"error": "禁止访问结果目录以外的文件"})
                    return
                if parts[3] == "report.html":
                    self._send(200, html_text(app.read_payload(run)), FILES[parts[3]])
                else:
                    self._send(200, file.read_bytes(), FILES[parts[3]])
            else:
                self._json(404, {"error": "路径不存在"})
        except FileNotFoundError:
            self._json(404, {"error": "结果不存在"})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self._json(400, {"error": str(exc)})

    def do_POST(self):
        if not self._check():
            return
        if self.route != "/api/scan":
            self._json(404, {"error": "路径不存在"})
            return
        if (self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json"
                or self.headers.get("X-Scanner-Request") != "1"):
            self._json(415, {"error": "需要application/json及X-Scanner-Request: 1"})
            return
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
            self._json(400, {"error": "需要单一Content-Length"})
            return
        try:
            length = int(lengths[0])
            if not 0 <= length <= MAX_BODY:
                self._json(413, {"error": "请求体不能超过8KiB"})
                return
            self.connection.settimeout(10)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("请求体不完整")
            body = json.loads(raw.decode("utf-8"))
            if body != {} or not isinstance(body, dict):
                raise ValueError("扫描请求体须为空JSON对象{}")
            self._json(200, self.server.app.start())
        except (ValueError, OSError, KeyError, TypeError) as exc:
            self._json(400, {"error": str(exc)})


def create_server(output_dir, port=8765, runner=None, provider=None, demo=False, host="127.0.0.1"):
    """Return a server without starting it; port=0 selects a test-only free port."""
    if host != "127.0.0.1":
        raise ValueError("服务仅可绑定127.0.0.1")
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("端口必须为0到65535的整数")
    app = DashboardState(output_dir, runner=runner, provider=provider, demo=demo)
    return DashboardServer((host, port), app)


def main(argv=None):
    parser = argparse.ArgumentParser(description="启动日K均线与缠论结构本机看板；点击页面按钮重新扫描")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "results")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--demo", action="store_true", help="重新扫描使用合成行情，明确标记为演示")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port须在1到65535之间")
    url = f"http://127.0.0.1:{args.port}/"
    try:
        server = create_server(args.output_dir, port=args.port, demo=args.demo)
    except OSError as exc:
        try:
            with urllib.request.urlopen(url + "api/health", timeout=2) as response:
                info = json.load(response)
            same = (info.get("service") == "chan-ma-scanner"
                    and Path(info.get("output_dir", "")).resolve() == args.output_dir.resolve()
                    and info.get("demo", False) == args.demo)
            if same:
                print("本机看板已运行：" + url)
                if not args.no_browser:
                    webbrowser.open(url)
                return 0
            print(f"端口{args.port}已被其他服务或结果目录占用，请使用--port指定其他端口。", file=sys.stderr)
        except (OSError, ValueError, urllib.error.URLError):
            print(f"看板启动失败：{exc}；请使用--port指定其他端口。", file=sys.stderr)
        return 1
    except (ValueError, KeyError, TypeError) as exc:
        print("看板启动失败：" + str(exc), file=sys.stderr)
        return 1
    print("本机看板：" + url, flush=True)
    print("离线演示模式：重扫只使用合成行情。" if args.demo else "点击“重新扫描”扫描沪深北日K；启动服务不会自动重扫。", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\n看板已停止。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
