"""Local-only graphical Morimens summon analysis server."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import psutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import history_store


ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
WEB = ROOT / "web"
DB = ROOT / "data" / "local" / "history.sqlite3"
CATALOG = ROOT / "data" / "local" / "catalog.json"
MIME = {".html": "text/html", ".css": "text/css", ".js": "text/javascript",
        ".webp": "image/webp", ".svg": "image/svg+xml", ".png": "image/png"}
UPDATE_LOCK = threading.Lock()
UPDATE = {"running": False, "phase": "idle", "message": "尚未开始更新", "log": [], "result": None}


def update_snapshot() -> dict:
    with UPDATE_LOCK:
        result = dict(UPDATE)
        result["log"] = list(UPDATE["log"][-12:])
    with history_store.connect(DB) as db:
        result["coverage"] = history_store.coverage(db)
    return result


def capture_port() -> int:
    game_pids = {p.pid for p in psutil.process_iter(["name"])
                 if (p.info.get("name") or "").lower() == "morimens.exe"}
    ports = Counter(conn.raddr.port for conn in psutil.net_connections(kind="tcp")
                    if conn.pid in game_pids and conn.raddr and conn.status == "ESTABLISHED")
    return ports.most_common(1)[0][0] if ports else 12887


def start_passive_capture(capture: Path, ready: Path, stop: Path, port: int) -> None:
    worker = (["--worker", "capture-login"] if getattr(sys, "frozen", False)
              else [str(ROOT / "capture_login_passive.py")])
    args = subprocess.list2cmdline(worker + ["--output", str(capture), "--ready-file", str(ready),
                                   "--stop-file", str(stop), "--port", str(port),
                                   "--seconds", "300"])
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable,
                                                  args, str(ROOT), 0)
    if result <= 32:
        raise RuntimeError(f"管理员被动采集未启动（系统代码 {result}）")
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if ready.exists():
            return
        time.sleep(0.25)
    raise RuntimeError("被动采集未就绪；请检查管理员权限提示")


def cleanup_capture(capture: Path, ready: Path, stop: Path) -> None:
    stop.write_text("STOP\n", encoding="ascii")
    for _ in range(20):
        try:
            for path in (capture, ready, stop):
                path.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.5)


def update_worker(mode: str = "now") -> None:
    command = ([sys.executable, "--worker", "fetch-history"] if getattr(sys, "frozen", False)
               else [sys.executable, "-u", str(ROOT / "fetch_all_history.py")])
    capture_files = None
    if mode == "wait":
        command.extend(["--wait-auth", "180"])
    partial = False
    failure = None
    try:
        if mode == "capture":
            suffix = uuid.uuid4().hex[:12]
            base = ROOT / "data" / "local" / f"login-{suffix}"
            capture, ready, stop = (base.with_suffix(ext) for ext in (".pcap", ".ready", ".stop"))
            capture_files = (capture, ready, stop)
            port = capture_port()
            with UPDATE_LOCK:
                UPDATE.update(phase="prepare", message=f"正在准备读取本机 TCP/{port} 通讯；请在权限提示中允许被动采集。")
            start_passive_capture(capture, ready, stop, port)
            with UPDATE_LOCK:
                UPDATE.update(phase="waiting", message=f"TCP/{port} 被动采集已就绪。现在请重启游戏并登录一次；检测到登录通讯后自动补页。")
                UPDATE["log"].append(UPDATE["message"])
            command.extend(["--fresh-capture", str(capture), "--capture-stop", str(stop),
                            "--capture-port", str(port), "--wait-capture", "240"])
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                   errors="replace", bufsize=1, env=env)
        assert process.stdout is not None
        for line in process.stdout:
            line = line.strip()
            if not line or line.startswith("WARNING: No libpcap"):
                continue
            if line.startswith("CURRENT_AUTH_IN_RAM"):
                phase, message = "login", "已读取当前游戏会话，正在建立查询连接…"
            elif line.startswith("CURRENT_MAIN_TARGET"):
                phase, message = "login", "已识别当前游戏主连接路由，正在连接对应服务器…"
            elif line.startswith("MAIN_TARGET_REJECTED"):
                phase, message = "login", "一个旧路由未返回抽卡历史，正在验证其他当前路由…"
            elif line.startswith("NO_VALID_MAIN_TARGET"):
                failure = "当前候选路由均未返回有效抽卡历史；请确认游戏已完成登录，稍后重试。"
                phase, message = "failed", failure
            elif line.startswith("LOCAL_PAGES_LOADED"):
                phase, message = "prepare", "已读取本地历史断点…"
            elif line.startswith(("AUTH_RESULT", "LOGIN_RESULT")):
                phase, message = "login", "正在验证查询连接…"
            elif line.startswith("HISTORY_PAGE"):
                parts = line.split()
                phase, message = "fetch", f"正在读取类别 {parts[1]} 第 {parts[2]} 页，取得 {parts[3]} 条…"
            elif line.startswith("CONNECTION_PAUSED"):
                partial = True
                phase, message = "partial", "连接中断，已获取的页面均已保存。"
            elif line.startswith("NO_CURRENT_AUTH"):
                game_online = any((p.info.get("name") or "").lower() == "morimens.exe"
                                  for p in psutil.process_iter(["name"]))
                failure = "游戏正在运行，但当前登录材料已从内存释放。请点击“等待重新登录”，再重启游戏一次。" if game_online else "未检测到游戏进程。请先启动游戏并登录。"
                phase, message = "failed", failure
            elif line.startswith("WAITING_AUTH"):
                phase, message = "waiting", "已开始等待新登录连接。现在请重启游戏一次并登录；检测到会话后将自动查询。"
            elif line.startswith("WAITING_CAPTURE"):
                phase, message = "waiting", "正在等待新登录通讯；请在采集就绪后重启并登录游戏一次。"
            elif line.startswith("FRESH_CAPTURE_READY"):
                phase, message = "login", "已捕获新登录通讯，正在验证查询连接…"
            elif line.startswith("NO_FRESH_CAPTURE"):
                failure = "等待期间未捕获完整登录通讯；请确认在显示“采集就绪”后重启并登录游戏。"
                phase, message = "failed", failure
            elif line.startswith("GAME_NOT_RUNNING"):
                failure = "未检测到游戏进程。请启动游戏并登录，再尝试更新。"
                phase, message = "failed", failure
            elif line.startswith("LOGIN_REJECTED"):
                failure = "当前会话未通过游戏服务器验证。请重新登录游戏后再更新。"
                phase, message = "failed", failure
            elif line.startswith("RuntimeError: 游戏服务器未返回历史记录"):
                failure = line.split(": ", 1)[1]
                phase, message = "failed", failure
            elif line.startswith("HISTORY_COVERAGE"):
                phase, message = "finish", "正在核对各类别的采集范围…"
            elif line.startswith(("INCREMENTAL_BOUNDARY", "WARNING:")):
                continue
            else:
                phase, message = "prepare", line[:180]
            with UPDATE_LOCK:
                UPDATE["phase"], UPDATE["message"] = phase, message
                UPDATE["log"].append(message)
                UPDATE["log"] = UPDATE["log"][-30:]
        code = process.wait()
        with UPDATE_LOCK:
            UPDATE["result"] = "partial" if partial else "complete" if code == 0 else "failed"
            UPDATE["phase"] = UPDATE["result"]
            UPDATE["reason"] = "auth_unavailable" if failure and "登录材料" in failure else None
            UPDATE["message"] = ("更新完成，已保存所有获取的记录。" if code == 0 and not partial else
                                  "连接中断，已保存进度；请查看各类别覆盖情况。" if partial else
                                  failure or "本次更新未完成；请查看上方提示后重试。")
            UPDATE["running"] = False
    except Exception as exc:
        with UPDATE_LOCK:
            UPDATE.update(running=False, phase="failed", result="failed",
                          message=f"更新启动失败：{str(exc)[:140]}")
    finally:
        if capture_files:
            cleanup_capture(*capture_files)


def payload(db_path: Path = DB, catalog_path: Path = CATALOG) -> dict:
    with history_store.connect(db_path) as db:
        records = [dict(row) for row in db.execute("""SELECT history_type,ordinal,item_tid,name,timestamp
            FROM history_records ORDER BY history_type,ordinal DESC""")]
        coverage = history_store.coverage(db)
    catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else {
        "characters": [], "wheels": [], "banners": [], "source": None}
    return {"records": records, "coverage": coverage, "catalog": catalog,
            "dataSource": "Summon.QuerySummonHistory", "timeZone": "Asia/Shanghai"}


class Handler(BaseHTTPRequestHandler):
    def send_json(self, value: dict, status: int = 200) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/data":
            self.send_json(payload())
            return
        if path == "/api/update-status":
            self.send_json(update_snapshot())
            return
        if path == "/":
            path = "/index.html"
        target = (WEB / path.lstrip("/")).resolve()
        if not target.is_relative_to(WEB.resolve()) or not target.is_file():
            self.send_error(404)
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/update":
            self.send_error(404)
            return
        if self.headers.get("X-Morimens-Action") != "update":
            self.send_json({"error": "缺少本地操作标记"}, 403)
            return
        with UPDATE_LOCK:
            if UPDATE["running"]:
                self.send_json({"error": "已有更新正在进行"}, 409)
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}") if length < 1024 else {}
            mode = body.get("mode") if body.get("mode") in ("now", "wait", "capture") else "now"
            UPDATE.update(running=True, phase="prepare", message="正在准备读取当前游戏会话…",
                          log=["开始更新；本次只进行一轮网关和历史查询登录。"], result=None,
                          reason=None)
        threading.Thread(target=update_worker, args=(mode,), daemon=True).start()
        self.send_json({"started": True}, 202)


def main() -> None:
    parser = argparse.ArgumentParser(description="忘却前夜抽卡分析：本地页面")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="服务启动后在默认浏览器打开")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"打开 {url}", flush=True)
    if args.open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
