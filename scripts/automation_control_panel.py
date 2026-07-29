from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)
RUNTIME_CONFIG_DIR = ROOT / "config" / "runtime"

SCRIPTS = {
    "wc_fossil": ("WC Fossil", "scripts/wc_fossil.py", "config/wc_fossil.example.json"),
    "woodcutting": ("Woodcutting", "scripts/woodcutting.py", "config/woodcutting.example.json"),
    "combat_mode": ("Combat Mode", "scripts/combat_mode.py", "config/combat_mode.example.json"),
    "template_click_sequence": (
        "Template Click Sequence", "scripts/template_click_sequence.py", "config/template_click_sequence.example.json"
    ),
    "woodcut_firemake": ("Woodcut + Firemake", "scripts/woodcut_firemake.py", "config/woodcut_firemake.example.json"),
    "gem_cutting": ("Gem Cutting", "scripts/gem_cutting.py", "config/gem_cutting.example.json"),
    "steel_cannonball": ("Steel Cannonball", "scripts/steel_cannonball.py", "config/steel_cannonball.example.json"),
    "fletching_logs": ("Fletching Logs", "scripts/fletching_logs.py", "config/fletching_logs.example.json"),
    "powermining": ("Powermining", "scripts/powermining.py", "config/powermining.example.json"),
    "motherlode_mine": ("Motherlode Mine", "scripts/motherlode_mine.py", "config/motherlode_mine.example.json"),
    "herblore": ("Herblore", "scripts/herblore.py", "config/herblore.example.json"),
    "potion_fill": ("Potion Fill", "scripts/potion_fill.py", "config/potion_fill.example.json"),
    "cleaning_herbs": ("Cleaning Herbs", "scripts/cleaning_herbs.py", "config/cleaning_herbs.example.json"),
}


class ProcessManager:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.process: subprocess.Popen[str] | None = None
        self.script_id: str | None = None
        self.logs: list[dict[str, Any]] = []
        self.next_log_id = 1

    def add_log(self, text: str, kind: str = "output") -> None:
        with self.lock:
            self.logs.append({"id": self.next_log_id, "text": text.rstrip(), "kind": kind})
            self.next_log_id += 1
            if len(self.logs) > 4000:
                self.logs = self.logs[-3000:]

    def status(self) -> dict[str, Any]:
        with self.lock:
            running = self.process is not None and self.process.poll() is None
            return {"running": running, "script_id": self.script_id if running else None}

    def start(self, script_id: str, config: dict[str, Any]) -> None:
        if script_id not in SCRIPTS:
            raise ValueError("Unknown script")
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError("Another automation is already running")

        runtime_path = runtime_config_path(script_id)
        runtime_path.parent.mkdir(parents=True, exist_ok=True)
        runtime_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        script_path = ROOT / SCRIPTS[script_id][1]
        environment = os.environ.copy()
        environment["VISUAL_AUTOMATION_MOUSE_BACKEND"] = str(config.get("mouse_backend", "standard"))
        process = subprocess.Popen(
            [str(PYTHON), "-u", str(script_path), "--config", str(runtime_path)],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=os.name != "nt",
            env=environment,
        )
        with self.lock:
            self.process = process
            self.script_id = script_id
        self.add_log(f"Started {SCRIPTS[script_id][0]} (PID {process.pid})", "system")
        threading.Thread(target=self._read_output, args=(process, script_id), daemon=True).start()

    def _read_output(self, process: subprocess.Popen[str], script_id: str) -> None:
        if process.stdout is not None:
            for line in process.stdout:
                self.add_log(line)
        code = process.wait()
        self.add_log(f"{SCRIPTS[script_id][0]} exited with code {code}", "system")
        with self.lock:
            if self.process is process:
                self.process = None
                self.script_id = None

    def stop(self) -> None:
        with self.lock:
            process = self.process
        if process is None or process.poll() is not None:
            return
        self.add_log("Stopping automation...", "system")
        try:
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGINT)
            process.wait(timeout=2.5)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    def logs_after(self, after: int) -> list[dict[str, Any]]:
        with self.lock:
            return [entry for entry in self.logs if entry["id"] > after]


MANAGER = ProcessManager()


def runtime_config_path(script_id: str) -> Path:
    return RUNTIME_CONFIG_DIR / f"{script_id}.json"


def load_config(script_id: str) -> dict[str, Any]:
    runtime = runtime_config_path(script_id)
    source = runtime if runtime.exists() else ROOT / SCRIPTS[script_id][2]
    return json.loads(source.read_text(encoding="utf-8"))


def save_config(script_id: str, config: dict[str, Any]) -> None:
    if script_id not in SCRIPTS:
        raise ValueError("Unknown script")
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    path = runtime_config_path(script_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


HTML_PATH = ROOT / "web" / "control_panel.html"
STATIC_FILES = {
    "/static/control_panel.css": (ROOT / "web" / "control_panel.css", "text/css; charset=utf-8"),
    "/static/control_panel.js": (ROOT / "web" / "control_panel.js", "text/javascript; charset=utf-8"),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return

    def send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in STATIC_FILES:
            path, content_type = STATIC_FILES[parsed.path]
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/":
            body = HTML_PATH.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/state":
            scripts = [
                {"id": script_id, "label": values[0], "config": load_config(script_id)}
                for script_id, values in SCRIPTS.items()
            ]
            self.send_json({"scripts": scripts, "process": MANAGER.status()})
            return
        if parsed.path == "/api/status":
            self.send_json(MANAGER.status())
            return
        if parsed.path == "/api/logs":
            query = parsed.query.partition("after=")[2]
            after = int(query) if query.isdigit() else 0
            self.send_json({"logs": MANAGER.logs_after(after)})
            return
        self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        try:
            data = self.read_json()
            if self.path == "/api/save":
                save_config(str(data.get("id", "")), data.get("config"))
                self.send_json({"ok": True})
                return
            if self.path == "/api/start":
                config = data.get("config")
                if not isinstance(config, dict):
                    raise ValueError("config must be an object")
                MANAGER.start(str(data.get("id", "")), config)
                self.send_json({"ok": True})
                return
            if self.path == "/api/stop":
                MANAGER.stop()
                self.send_json({"ok": True})
                return
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except RuntimeError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.CONFLICT)


def main() -> int:
    if not PYTHON.exists():
        print(f"Project Python not found: {PYTHON}")
        return 1
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Visual Automation control panel: {url}")
    threading.Timer(0.4, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        MANAGER.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
