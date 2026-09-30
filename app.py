"""Local-only graphical Morimens summon analysis server."""

from __future__ import annotations

import argparse
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import history_store


ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
DB = ROOT / "data" / "local" / "history.sqlite3"
CATALOG = ROOT / "data" / "local" / "catalog.json"
MIME = {".html": "text/html", ".css": "text/css", ".js": "text/javascript",
        ".webp": "image/webp", ".svg": "image/svg+xml", ".png": "image/png"}


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
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/data":
            data = json.dumps(payload(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="忘却前夜抽卡分析：本地页面")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"打开 http://127.0.0.1:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
