#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""昔涟 Bot 控制面板 — HTTP + 浏览器"""

import json, os, signal, subprocess, sys, time, threading
from collections import deque
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

BOT_DIR = Path(r"C:\Users\misak\Desktop\zlb-scraper\qq_bot")
NAPCAT_DIR = Path(r"C:\Users\misak\Downloads\NapCat.Shell.Windows.OneKey\NapCat.44498.Shell")
NAPCAT_VERSION_DIR = NAPCAT_DIR / "versions" / "9.9.26-44498"
NAPCAT_CONFIG_DIR = NAPCAT_VERSION_DIR / "resources" / "app" / "napcat" / "config"
NAPCAT_LOG_DIR = NAPCAT_VERSION_DIR / "resources" / "app" / "napcat" / "logs"
PYTHON_EXE = r"D:\Python\python.exe"
CONFIG_PATH = BOT_DIR / "config.json"
PORT = 9999

logs = deque(maxlen=500)
logs_lock = threading.Lock()

def add_log(source, text):
    entry = {"source": source, "text": text, "time": time.strftime("%H:%M:%S")}
    with logs_lock:
        logs.append(entry)
    return entry

# ---- Process Managers ----
class BotProcess:
    def __init__(self):
        self.proc = None
        self._reader_t = None
        self.state = "stopped"

    def start(self):
        if self.state == "running":
            return
        self.state = "starting"
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        try:
            self.proc = subprocess.Popen(
                [PYTHON_EXE, str(BOT_DIR / "run.py")],
                cwd=str(BOT_DIR), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            self.state = "running"
            add_log("BOT", "Bot 已启动 (PID: %d)" % self.proc.pid)
            self._reader_t = threading.Thread(target=self._read_output, daemon=True)
            self._reader_t.start()
        except Exception as e:
            self.state = "error"
            add_log("SYSTEM", "启动 Bot 失败: %s" % e)

    def stop(self):
        if self.state != "running" or not self.proc:
            return
        self.state = "stopping"
        pid = self.proc.pid
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except subprocess.TimeoutError:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
        except Exception:
            pass
        self.proc = None
        self.state = "stopped"
        add_log("BOT", "Bot 已停止")

    def _read_output(self):
        try:
            for line in iter(self.proc.stdout.readline, b""):
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    add_log("BOT", text)
        except Exception:
            pass

class NapCatProcess:
    def __init__(self):
        self.qq_pid = None
        self._tail_t = None
        self._stop_flag = threading.Event()
        self.state = "stopped"

    def _find_qq_exe(self):
        bundled = NAPCAT_DIR / "QQ.exe"
        if bundled.exists():
            return str(bundled)
        try:
            out = subprocess.check_output(
                ["reg.exe", "query", r"HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\QQ",
                 "/v", "UninstallString"],
                text=True, errors="replace"
            )
            for line in out.splitlines():
                if "UninstallString" in line:
                    parts = line.strip().split("    ")
                    if len(parts) >= 3:
                        pth = parts[-1].strip('"')
                        pth = os.path.dirname(pth) + "\\QQ.exe"
                        if os.path.exists(pth):
                            return pth
        except Exception:
            pass
        return None

    def start(self):
        if self.state in ("starting", "running"):
            return
        self.state = "starting"
        self._stop_flag.clear()
        try:
            qq_path = self._find_qq_exe()
            if not qq_path:
                self.state = "error"
                add_log("SYSTEM", "找不到 QQ.exe——NapCat.Shell 需要先安装 QQ 桌面版")
                return

            napcat_app = NAPCAT_DIR / "versions" / "9.9.26-44498" / "resources" / "app" / "napcat"
            inject_dll = str(napcat_app / "NapCatWinBootHook.dll")
            launcher = str(napcat_app / "NapCatWinBootMain.exe")
            main_mjs = str(napcat_app / "napcat.mjs").replace("\\", "/")
            load_js = str(napcat_app / "loadNapCat.js")

            with open(load_js, "w", encoding="utf-8") as f:
                f.write('(async () => {await import("file:///%s")})()' % main_mjs)

            env = os.environ.copy()
            env["NAPCAT_PATCH_PACKAGE"] = str(napcat_app / "qqnt.json")
            env["NAPCAT_LOAD_PATH"] = load_js
            env["NAPCAT_INJECT_PATH"] = inject_dll
            env["NAPCAT_LAUNCHER_PATH"] = launcher
            env["NAPCAT_MAIN_PATH"] = main_mjs.replace("/", "\\")

            before = set(self._get_qq_pids())
            bat_path = napcat_app / "_start.bat"
            with open(bat_path, "w", encoding="utf-8") as f:
                f.write('@chcp 65001>nul\n"%s" "%s" "%s"\npause\n' % (launcher, qq_path, inject_dll))
            p = subprocess.Popen(
                [str(bat_path)],
                cwd=str(napcat_app), env=env,
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
            add_log("NAPCAT", "NapCat 终端窗口已打开，请扫码登录 QQ")
            for _ in range(24):
                time.sleep(5)
                after = set(self._get_qq_pids())
                new = after - before
                self.qq_pid = new.pop() if new else None
                if self.qq_pid:
                    self.state = "running"
                    add_log("NAPCAT", "NapCat 已启动 (QQ PID: %d)" % self.qq_pid)
                    self._tail_t = threading.Thread(target=self._tail_log, daemon=True)
                    self._tail_t.start()
                    return
            self.state = "error"
            add_log("SYSTEM", "NapCat 启动超时——QQ 窗口没有出现，请检查后重试")
        except Exception as e:
            self.state = "error"
            add_log("SYSTEM", "启动 NapCat 失败: %s" % e)

    def stop(self):
        if self.state != "running":
            return
        self._stop_flag.set()
        if self.qq_pid:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.qq_pid)], capture_output=True)
            add_log("NAPCAT", "NapCat 已停止")
        self.qq_pid = None
        self.state = "stopped"

    def _get_qq_pids(self):
        pids = []
        try:
            out = subprocess.check_output(["tasklist", "/FI", "IMAGENAME eq QQ.exe", "/FO", "CSV", "/NH"],
                                          text=True, errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
            for line in out.strip().split("\n"):
                parts = line.replace('"', "").split(",")
                if len(parts) >= 2:
                    try:
                        pids.append(int(parts[1].strip()))
                    except ValueError:
                        pass
        except Exception:
            pass
        return pids

    def _tail_log(self):
        log_dirs = [NAPCAT_LOG_DIR, NAPCAT_DIR / "logs"]
        log_file = None
        for d in log_dirs:
            if d.exists():
                files = sorted(d.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
                if files:
                    log_file = files[0]
                    break
        if not log_file:
            return
        add_log("SYSTEM", "NapCat 日志: " + str(log_file.name))
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)
                while not self._stop_flag.is_set():
                    line = f.readline()
                    if line:
                        text = line.rstrip()
                        if text:
                            add_log("NAPCAT", text)
                    else:
                        time.sleep(0.5)
        except Exception:
            pass

# ---- Config ----
def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"error": str(e)}

def save_config(data):
    try:
        cfg = load_config()
        if "error" in cfg:
            return {"ok": False, "error": "无法读取配置"}
        cfg.setdefault("deepseek", {})
        cfg.setdefault("bot", {})
        m = {"api_key": ("deepseek", "api_key"), "model": ("deepseek", "model"),
             "temperature": ("deepseek", "temperature"), "max_tokens": ("deepseek", "max_tokens"),
             "port": ("bot", "port"), "host": ("bot", "host"),
             "napcat_url": ("bot", "napcat_url"), "access_token": ("bot", "access_token"),
             "character": (None, "character"), "db_path": (None, "db_path")}
        for k, v in data.items():
            if k in m:
                sec, key = m[k]
                if sec:
                    cfg[sec][key] = v
                else:
                    cfg[key] = v
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ---- HTTP Handler ----
bot = BotProcess()
napcat = NapCatProcess()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
    def _send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def _send_html(self):
        html = HTML_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self._send_html()
        elif path == "/api/status":
            self._send_json({"bot": bot.state, "napcat": napcat.state})
        elif path == "/api/logs":
            after = 0
            if "?" in self.path:
                for p in self.path.split("?")[1].split("&"):
                    if p.startswith("after="):
                        try: after = int(p.split("=")[1])
                        except ValueError: pass
            with logs_lock:
                entries = list(logs)[after:]
            self._send_json({"entries": entries, "total": len(logs)})
        elif path == "/api/config":
            raw = load_config()
            if "error" in raw:
                self._send_json(raw)
            else:
                # Flatten deepseek.* and bot.* for the frontend
                flat = {}
                for sec in ("deepseek", "bot"):
                    for k, v in raw.get(sec, {}).items():
                        flat[k] = v
                flat["character"] = raw.get("character", "")
                flat["db_path"] = raw.get("db_path", "")
                self._send_json(flat)
        else:
            self._send_json({"error": "not found"}, 404)
    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len) if content_len > 0 else b""
        data = json.loads(body.decode("utf-8")) if body else {}
        path = self.path.split("?")[0]
        if path == "/api/start_bot":
            threading.Thread(target=bot.start, daemon=True).start()
            self._send_json({"ok": True})
        elif path == "/api/stop_bot":
            threading.Thread(target=bot.stop, daemon=True).start()
            self._send_json({"ok": True})
        elif path == "/api/restart_bot":
            def _restart_bot(): bot.stop(); time.sleep(1); bot.start()
            threading.Thread(target=_restart_bot, daemon=True).start()
            self._send_json({"ok": True})
        elif path == "/api/start_napcat":
            threading.Thread(target=napcat.start, daemon=True).start()
            self._send_json({"ok": True})
        elif path == "/api/stop_napcat":
            threading.Thread(target=napcat.stop, daemon=True).start()
            self._send_json({"ok": True})
        elif path == "/api/restart_napcat":
            def _restart_napcat(): napcat.stop(); time.sleep(2); napcat.start()
            threading.Thread(target=_restart_napcat, daemon=True).start()
            self._send_json({"ok": True})
        elif path == "/api/config":
            self._send_json(save_config(data))
        else:
            self._send_json({"error": "not found"}, 404)

# ---- HTML Page ----
HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>昔涟 Bot 控制面板</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#0f0f0f; color:#e0e0e0; font-family:"Microsoft YaHei",sans-serif; padding:20px; }
.header { display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; }
h1 { font-size:22px; color:#f0f0f0; }
.status-bar { display:flex; gap:30px; }
.status-item { display:flex; align-items:center; gap:8px; font-size:14px; }
.dot { width:10px; height:10px; border-radius:50%; background:#ef4444; }
.controls { display:flex; gap:10px; margin-bottom:20px; flex-wrap:wrap; }
.btn { background:#1a1a2e; color:#c0c0ff; border:1px solid #303060; padding:8px 18px; border-radius:6px; cursor:pointer; font-size:14px; transition:all .2s; }
.btn:hover { background:#252545; border-color:#5050a0; }
.btn-symbol { margin-right:4px; }
.main { display:flex; gap:20px; height:calc(100vh - 180px); }
.log-panel { flex:1; background:#111; border:1px solid #222; border-radius:6px; padding:12px; overflow-y:auto; font-family:Consolas,monospace; font-size:13px; }
.log-line { padding:2px 0; border-bottom:1px solid #1a1a1a; }
.config-panel { width:300px; background:#111; border:1px solid #222; border-radius:6px; padding:12px; overflow-y:auto; }
.config-panel h3 { font-size:16px; margin-bottom:12px; }
.form-field { margin-bottom:12px; }
.form-label { display:block; font-size:12px; color:#888; margin-bottom:4px; }
.form-input { width:100%; background:#1a1a1a; color:#e0e0e0; border:1px solid #333; padding:6px 8px; border-radius:4px; font-size:13px; }
.form-input:focus { outline:none; border-color:#5050a0; }
.btn-save { background:#2a2a4a; color:#c0c0ff; border:1px solid #4040a0; padding:8px 20px; border-radius:6px; cursor:pointer; width:100%; font-size:14px; }
.btn-save:hover { background:#35355a; }
.advanced-toggle { background:none; border:none; color:#666; cursor:pointer; font-size:12px; margin-bottom:10px; padding:0; }
.advanced-toggle:hover { color:#999; }
.advanced { display:none; }
.advanced.open { display:block; }
.banner { background:#2a2a1a; border:1px solid #665500; color:#cca800; padding:6px 10px; border-radius:4px; font-size:12px; margin-top:10px; display:none; }
</style>
</head>
<body>
<div class="header">
  <h1>昔涟 Bot 控制面板</h1>
  <div class="status-bar">
    <div class="status-item"><div class="dot" id="bot-dot"></div> <span id="bot-text">已停止</span></div>
    <div class="status-item"><div class="dot" id="napcat-dot"></div> <span id="napcat-text">已停止</span></div>
  </div>
</div>
<div class="controls">
  <button class="btn" onclick="startBot()"><span class="btn-symbol">▶</span> 启动 Bot</button>
  <button class="btn" onclick="stopBot()"><span class="btn-symbol">⏹</span> 停止 Bot</button>
  <button class="btn" onclick="restartBot()"><span class="btn-symbol">🔄</span> 重启 Bot</button>
  <button class="btn" onclick="startNapcat()"><span class="btn-symbol">▶</span> 启动 NapCat</button>
  <button class="btn" onclick="stopNapcat()"><span class="btn-symbol">⏹</span> 停止 NapCat</button>
  <button class="btn" onclick="restartNapcat()"><span class="btn-symbol">🔄</span> 重启 NapCat</button>
</div>
<div class="main">
  <div class="log-panel" id="log-panel"></div>
  <div class="config-panel">
    <h3>配置</h3>
    <div class="form-field"><label class="form-label">API Key</label><input class="form-input" id="cfg-api-key" type="password"></div>
    <div class="form-field"><label class="form-label">Model</label><input class="form-input" id="cfg-model"></div>
    <div class="form-field"><label class="form-label">Temperature</label><input class="form-input" id="cfg-temperature" type="number" step="0.1"></div>
    <div class="form-field"><label class="form-label">Port</label><input class="form-input" id="cfg-port" type="number"></div>
    <button class="advanced-toggle" onclick="toggleAdvanced()">▼ 高级设置</button>
    <div class="advanced" id="advanced-panel">
      <div class="form-field"><label class="form-label">Host</label><input class="form-input" id="cfg-host"></div>
      <div class="form-field"><label class="form-label">NapCat URL</label><input class="form-input" id="cfg-napcat-url"></div>
      <div class="form-field"><label class="form-label">Access Token</label><input class="form-input" id="cfg-access-token"></div>
      <div class="form-field"><label class="form-label">Max Tokens</label><input class="form-input" id="cfg-max-tokens" type="number"></div>
      <div class="form-field"><label class="form-label">Character</label><input class="form-input" id="cfg-character"></div>
      <div class="form-field"><label class="form-label">DB Path</label><input class="form-input" id="cfg-db-path"></div>
    </div>
    <button class="btn-save" onclick="saveConfig()">保存配置</button>
    <div class="banner" id="banner"></div>
  </div>
</div>
<script>
var logOffset = 0;
function appendLog(entry) {
  var p = document.getElementById("log-panel");
  var d = document.createElement("div");
  d.className = "log-line";
  d.textContent = "[" + entry.time + "] [" + entry.source + "] " + entry.text;
  p.appendChild(d);
  if (p.children.length > 500) p.removeChild(p.firstChild);
  p.scrollTop = p.scrollHeight;
}
function updateStatus(s) {
  var map = {running:{c:"#22c55e",t:"运行中"},starting:{c:"#f59e0b",t:"启动中"},stopping:{c:"#f59e0b",t:"停止中"},stopped:{c:"#ef4444",t:"已停止"},error:{c:"#ef4444",t:"错误"}};
  var bi = map[s.bot]||map.stopped, ni = map[s.napcat]||map.stopped;
  document.getElementById("bot-dot").style.background = bi.c;
  document.getElementById("bot-text").textContent = bi.t;
  document.getElementById("napcat-dot").style.background = ni.c;
  document.getElementById("napcat-text").textContent = ni.t;
}
function pollLogs() {
  fetch("/api/logs?after=" + logOffset).then(function(r){return r.json()}).then(function(d){
    if (d.entries) { d.entries.forEach(function(e){appendLog(e)}); logOffset = d.total; }
  }).catch(function(){});
}
function pollStatus() {
  fetch("/api/status").then(function(r){return r.json()}).then(updateStatus).catch(function(){});
}
function loadConfig() {
  fetch("/api/config").then(function(r){return r.json()}).then(function(cfg){
    if (cfg.error) return;
    var m = [["cfg-api-key","api_key"],["cfg-model","model"],["cfg-temperature","temperature"],
             ["cfg-port","port"],["cfg-host","host"],["cfg-napcat-url","napcat_url"],
             ["cfg-access-token","access_token"],["cfg-max-tokens","max_tokens"],
             ["cfg-character","character"],["cfg-db-path","db_path"]];
    m.forEach(function(p){var el=document.getElementById(p[0]);if(el&&cfg[p[1]]!==undefined)el.value=cfg[p[1]]});
  }).catch(function(){});
}
function saveConfig() {
  var keys = ["api_key","model","temperature","port","host","napcat_url","access_token","max_tokens","character","db_path"];
  var ids = ["cfg-api-key","cfg-model","cfg-temperature","cfg-port","cfg-host","cfg-napcat-url","cfg-access-token","cfg-max-tokens","cfg-character","cfg-db-path"];
  var data = {};
  for (var i=0;i<keys.length;i++) data[keys[i]] = document.getElementById(ids[i]).value;
  fetch("/api/config",{method:"POST",body:JSON.stringify(data)}).then(function(r){return r.json()}).then(function(res){
    var b = document.getElementById("banner");
    if (res.ok) { b.style.background="#2a3a2a"; b.style.borderColor="#335533"; b.style.color="#88cc88"; b.textContent="保存成功，需要重启 Bot 才能生效"; }
    else { b.style.background="#2a2a1a"; b.style.borderColor="#665500"; b.style.color="#cca800"; b.textContent="保存失败: "+ (res.error||""); }
    b.style.display="block";
    setTimeout(function(){b.style.display="none"}, 5000);
  }).catch(function(e){
    var b = document.getElementById("banner");
    b.style.background="#3a1a1a"; b.style.borderColor="#663333"; b.style.color="#cc8888"; b.textContent="保存失败: "+e; b.style.display="block";
  });
}
function toggleAdvanced() {
  var p = document.getElementById("advanced-panel");
  p.classList.toggle("open");
  document.querySelector(".advanced-toggle").textContent = p.classList.contains("open") ? "▲ 高级设置" : "▼ 高级设置";
}
function startBot()      { fetch("/api/start_bot",{method:"POST"}); appendLog({source:"SYSTEM",text:"正在启动 Bot...",time:new Date().toLocaleTimeString("en-GB")}); }
function stopBot()       { fetch("/api/stop_bot",{method:"POST"}); appendLog({source:"SYSTEM",text:"正在停止 Bot...",time:new Date().toLocaleTimeString("en-GB")}); }
function restartBot()    { fetch("/api/restart_bot",{method:"POST"}); appendLog({source:"SYSTEM",text:"正在重启 Bot...",time:new Date().toLocaleTimeString("en-GB")}); }
function startNapcat()   { fetch("/api/start_napcat",{method:"POST"}); appendLog({source:"SYSTEM",text:"正在启动 NapCat...",time:new Date().toLocaleTimeString("en-GB")}); }
function stopNapcat()    { fetch("/api/stop_napcat",{method:"POST"}); appendLog({source:"SYSTEM",text:"正在停止 NapCat...",time:new Date().toLocaleTimeString("en-GB")}); }
function restartNapcat() { fetch("/api/restart_napcat",{method:"POST"}); appendLog({source:"SYSTEM",text:"正在重启 NapCat...",time:new Date().toLocaleTimeString("en-GB")}); }
appendLog({source:"SYSTEM",text:"控制面板已连接 (http://localhost:""" + str(PORT) + """)",time:new Date().toLocaleTimeString("en-GB")});
loadConfig();
setInterval(pollLogs, 500);
setInterval(pollStatus, 2000);
</script>
</body>
</html>"""

# ---- Main ----
if __name__ == "__main__":
    import socket

    # Test if port is actually available
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", PORT))
        s.close()
        print("[OK] 端口 %d 可用" % PORT)
    except OSError as e:
        print("[ERR] 端口 %d 不可用: %s" % (PORT, e))
        input("按 Enter 退出...")
        sys.exit(1)

    # Start server
    try:
        server = HTTPServer(("127.0.0.1", PORT), Handler)
        print("[OK] 服务器已启动在 http://127.0.0.1:%d" % PORT)
    except OSError as e:
        print("[ERR] 创建服务器失败: %s" % e)
        input("按 Enter 退出...")
        sys.exit(1)

    # Verify it's actually listening (netstat style check)
    test = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        test.connect(("127.0.0.1", PORT))
        test.close()
        print("[OK] 端口 %d 确认在监听" % PORT)
    except Exception as e:
        print("[WARN] 端口 %d 不可连接: %s" % (PORT, e))

    # Open browser after a short delay so the server is ready
    import webbrowser
    def _open_browser():
        import time
        time.sleep(0.5)
        webbrowser.open("http://127.0.0.1:%d" % PORT)
    threading.Thread(target=_open_browser, daemon=True).start()

    def cleanup():
        if bot.state == "running": bot.stop()
        if napcat.state == "running": napcat.stop()
        server.shutdown()

    if sys.platform == "win32":
        import ctypes
        try:
            _PH = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)(lambda e: (cleanup(), 1)[1])
            ctypes.windll.kernel32.SetConsoleCtrlHandler(_PH, True)
        except Exception:
            pass  # Optional console handler, not critical

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        cleanup()
        print("\nBye")
