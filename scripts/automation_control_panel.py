from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
RUNTIME_CONFIG_DIR = ROOT / "config" / "runtime"

SCRIPTS = {
    "combat_mode": ("Combat Mode", "scripts/combat_mode.py", "config/combat_mode.example.json"),
    "template_click_sequence": (
        "Template Click Sequence", "scripts/template_click_sequence.py", "config/template_click_sequence.example.json"
    ),
    "woodcut_firemake": ("Woodcut + Firemake", "scripts/woodcut_firemake.py", "config/woodcut_firemake.example.json"),
    "gem_cutting": ("Gem Cutting", "scripts/gem_cutting.py", "config/gem_cutting.example.json"),
    "steel_cannonball": ("Steel Cannonball", "scripts/steel_cannonball.py", "config/steel_cannonball.example.json"),
    "fletching_logs": ("Fletching Logs", "scripts/fletching_logs.py", "config/fletching_logs.example.json"),
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


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Visual Automation</title>
<style>
:root{--bg:#101315;--panel:#191e21;--panel2:#22292d;--text:#edf2f4;--muted:#93a1a8;--line:#344047;--green:#4bd37b;--red:#ff625f;--gold:#f0b84b;--blue:#63a9ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;height:100vh;overflow:hidden}
header{height:64px;padding:14px 22px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;background:#14181b}
h1{font-size:20px;margin:0}.subtitle{color:var(--muted);font-size:12px}.status{padding:7px 12px;border-radius:18px;background:var(--panel2);color:var(--muted)}.status.running{color:var(--green)}
main{display:grid;grid-template-columns:240px minmax(390px,1fr) minmax(340px,.8fr);height:calc(100vh - 64px)}
nav{border-right:1px solid var(--line);padding:14px;overflow:auto}.script{width:100%;text-align:left;border:1px solid transparent;background:transparent;color:var(--text);padding:12px;border-radius:9px;margin-bottom:6px;cursor:pointer}.script:hover{background:var(--panel)}.script.selected{background:var(--panel2);border-color:#46545c}.script small{display:block;color:var(--muted);margin-top:3px}
.settings{padding:20px;overflow:auto;border-right:1px solid var(--line)}h2{margin:0 0 5px;font-size:19px}.hint{color:var(--muted);margin-bottom:18px}.fields{display:grid;grid-template-columns:repeat(2,minmax(160px,1fr));gap:12px}.field{background:var(--panel);border:1px solid var(--line);padding:10px;border-radius:8px}.field.wide{grid-column:1/-1}.field label{display:block;color:#c7d0d4;font-size:12px;margin-bottom:7px;word-break:break-word}input,textarea,select{width:100%;border:1px solid #445159;background:#0f1315;color:var(--text);border-radius:6px;padding:8px;font:13px ui-monospace,SFMono-Regular,Menlo,monospace}input[type=checkbox]{width:auto;transform:scale(1.25);margin:6px}textarea{min-height:84px;resize:vertical}
.actions{position:sticky;bottom:-20px;background:linear-gradient(transparent,#101315 22%);padding:28px 0 4px;display:flex;gap:9px}button.action{border:0;border-radius:8px;padding:10px 16px;font-weight:650;cursor:pointer}.start{background:var(--green);color:#092313}.stop{background:var(--red);color:#2d0808}.save{background:var(--blue);color:#071c33}.action:disabled{opacity:.4;cursor:not-allowed}
.console{display:flex;flex-direction:column;min-width:0}.console-head{padding:16px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between}.logs{margin:0;padding:14px;overflow:auto;flex:1;background:#0b0e10;color:#cbd6db;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap}.log-system{color:var(--gold)}
@media(max-width:1000px){body{height:auto;overflow:auto}main{grid-template-columns:190px minmax(0,1fr);height:auto;min-height:calc(100vh - 64px)}.settings{border-right:0}.console{display:flex;grid-column:1/-1;height:380px;border-top:1px solid var(--line)}.fields{grid-template-columns:1fr}}
</style></head>
<body><header><div><h1>Visual Automation</h1><div class="subtitle">Local control panel · one automation at a time</div></div><div id="status" class="status">Idle</div></header>
<main><nav id="scripts"></nav><section class="settings"><h2 id="title"></h2><div class="hint">Settings are saved under config/runtime. Example configs remain unchanged.</div><div id="fields" class="fields"></div><div class="actions"><button id="start" class="action start">Start</button><button id="stop" class="action stop">Stop</button><button id="save" class="action save">Save settings</button></div></section><section class="console"><div class="console-head"><strong>Live output</strong><span class="subtitle">Esc or Cmd+Shift+Q also stops automation</span></div><pre id="logs" class="logs"></pre></section></main>
<script>
let state=null, selected=null, lastLog=0;
const esc=s=>s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,options={}){const r=await fetch(path,{headers:{'Content-Type':'application/json'},...options});const j=await r.json();if(!r.ok)throw new Error(j.error||'Request failed');return j}
function renderScripts(){const n=document.getElementById('scripts');n.innerHTML='';for(const s of state.scripts){const b=document.createElement('button');b.className='script'+(s.id===selected?' selected':'');b.innerHTML=`${esc(s.label)}<small>${esc(s.id)}</small>`;b.onclick=()=>{selected=s.id;renderScripts();renderFields()};n.appendChild(b)}}
function renderFields(){const s=state.scripts.find(x=>x.id===selected);document.getElementById('title').textContent=s.label;const box=document.getElementById('fields');box.innerHTML='';for(const [key,value] of Object.entries(s.config)){const d=document.createElement('div');const nested=typeof value==='object'&&value!==null;d.className='field'+(nested?' wide':'');const l=document.createElement('label');l.textContent=key;d.appendChild(l);let el;if(key==='mouse_backend'){el=document.createElement('select');for(const option of ['standard','quartz']){const o=document.createElement('option');o.value=option;o.textContent=option==='standard'?'Standard — moves cursor':'Quartz — experimental background click';o.selected=value===option;el.appendChild(o)}}else if(key==='platform'){el=document.createElement('select');for(const option of ['auto','mac','windows']){const o=document.createElement('option');o.value=option;o.textContent=option==='auto'?'Auto detect':option;o.selected=value===option;el.appendChild(o)}}else if(typeof value==='boolean'){el=document.createElement('input');el.type='checkbox';el.checked=value}else if(nested){el=document.createElement('textarea');el.value=JSON.stringify(value,null,2)}else{el=document.createElement('input');el.type=typeof value==='number'?'number':'text';if(typeof value==='number')el.step='any';el.value=value}el.dataset.key=key;el.dataset.type=Array.isArray(value)?'array':typeof value;d.appendChild(el);box.appendChild(d)}updateButtons()}
function collect(){const out={};for(const el of document.querySelectorAll('[data-key]')){let v;if(el.dataset.type==='boolean')v=el.checked;else if(el.dataset.type==='number')v=Number(el.value);else if(el.dataset.type==='object'||el.dataset.type==='array')v=JSON.parse(el.value);else v=el.value;out[el.dataset.key]=v}return out}
async function save(){const config=collect();await api('/api/save',{method:'POST',body:JSON.stringify({id:selected,config})});state.scripts.find(x=>x.id===selected).config=config;flash('Settings saved')}
async function start(){const config=collect();if(config.dry_run===false){const detail=config.mouse_backend==='quartz'?'The experimental Quartz backend will send background click events. RuneLite compatibility is not yet confirmed.':'This automation will move and click the mouse.';if(!confirm('Live mode is enabled. '+detail+' Start it?'))return}await api('/api/start',{method:'POST',body:JSON.stringify({id:selected,config})});state.scripts.find(x=>x.id===selected).config=config;await refreshStatus()}
async function stop(){await api('/api/stop',{method:'POST',body:'{}'});setTimeout(refreshStatus,250)}
function flash(t){const e=document.getElementById('status');e.textContent=t;setTimeout(refreshStatus,900)}
function updateButtons(){const running=state?.process?.running;document.getElementById('start').disabled=running;document.getElementById('stop').disabled=!running}
async function refreshStatus(){const p=await api('/api/status');state.process=p;const e=document.getElementById('status');if(p.running){const s=state.scripts.find(x=>x.id===p.script_id);e.textContent='Running · '+(s?s.label:p.script_id);e.className='status running'}else{e.textContent='Idle';e.className='status'}updateButtons()}
async function pollLogs(){try{const data=await api('/api/logs?after='+lastLog);const e=document.getElementById('logs');for(const l of data.logs){lastLog=l.id;const span=document.createElement('span');span.className=l.kind==='system'?'log-system':'';span.textContent=l.text+'\n';e.appendChild(span)}if(data.logs.length)e.scrollTop=e.scrollHeight}catch{}setTimeout(pollLogs,700)}
async function init(){state=await api('/api/state');selected=state.scripts[0].id;renderScripts();renderFields();refreshStatus();pollLogs()}
document.getElementById('start').onclick=()=>start().catch(e=>alert(e.message));document.getElementById('stop').onclick=()=>stop().catch(e=>alert(e.message));document.getElementById('save').onclick=()=>save().catch(e=>alert('Invalid setting: '+e.message));init();
</script></body></html>"""


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
        if parsed.path == "/":
            body = HTML.encode("utf-8")
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
