"""Local append-only store for verified Summon.QuerySummonHistory pages.

The server returns newest-first pages and an all-time count. An old record's
ordinal counted from the oldest end remains stable when newer pulls are added.
This is more reliable than using timestamps alone: several pulls share a second.
"""

from __future__ import annotations

import csv
import json
import math
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


PAGE_SIZE = 5
CHINA = timezone(timedelta(hours=8))
NAME = re.compile(r"Item_(\d+)_Name\|(.*)")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS history_types (
        history_type INTEGER PRIMARY KEY, latest_count INTEGER NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS history_records (
        history_type INTEGER NOT NULL, ordinal INTEGER NOT NULL,
        item_tid INTEGER NOT NULL, name TEXT NOT NULL, timestamp INTEGER NOT NULL,
        PRIMARY KEY (history_type, ordinal)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS account_metadata (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
    )""")
    return db


def account_uid(db: sqlite3.Connection) -> str | None:
    row = db.execute("SELECT value FROM account_metadata WHERE key='uid'").fetchone()
    return row[0] if row else None


def save_account_uid(db: sqlite3.Connection, uid: str) -> None:
    if not uid.isdecimal() or int(uid) <= 0:
        raise ValueError("History playerId 无效")
    existing = account_uid(db)
    if existing and existing != uid:
        raise ValueError("History playerId 与已保存账号不一致；已停止写入")
    db.execute("INSERT OR IGNORE INTO account_metadata(key,value) VALUES ('uid',?)", (uid,))


def recover_uid_from_raw(db: sqlite3.Connection, path: Path) -> str | None:
    """Backfill UID from older saved pages only when several rows match the DB."""
    if account_uid(db) or not path.is_file():
        return account_uid(db)
    source = json.loads(path.read_text(encoding="utf-8"))
    if source.get("method") != "Summon.QuerySummonHistory":
        return None
    matches: dict[str, int] = {}
    for key, body in source.get("pages", {}).items():
        history_type, page = map(int, key.split("/"))
        for ordinal, raw in zip(page_ordinals(body["count"], page), body.get("records", [])):
            uid = raw.get("playerId")
            if type(uid) is not int or uid <= 0:
                continue
            row = db.execute("""SELECT item_tid,name,timestamp FROM history_records
                WHERE history_type=? AND ordinal=?""", (history_type, ordinal)).fetchone()
            if row and tuple(row) == normalize(raw, history_type):
                matches[str(uid)] = matches.get(str(uid), 0) + 1
    if len(matches) == 1 and next(iter(matches.values())) >= 3:
        uid = next(iter(matches))
        with db:
            save_account_uid(db, uid)
        return uid
    return None


def normalize(raw: dict, history_type: int) -> tuple[int, str, int]:
    tid, name, stamp = raw.get("itemTid"), raw.get("name"), raw.get("timestamp")
    if raw.get("type") != history_type or type(tid) is not int or type(stamp) is not int or not isinstance(name, str):
        raise ValueError("History 记录缺少有效的 type/itemTid/name/timestamp")
    match = NAME.fullmatch(name)
    if not match or int(match[1]) != tid:
        raise ValueError("History itemTid 与 name 中的编号不一致")
    return tid, match[2], stamp


def page_ordinals(count: int, page: int) -> list[int]:
    if count < 0 or page < 1:
        raise ValueError("无效的总数或页码")
    return [count - 1 - i for i in range((page - 1) * PAGE_SIZE, min(page * PAGE_SIZE, count))]


def ingest_page(db: sqlite3.Connection, history_type: int, page: int, body: dict) -> int:
    count = body.get("count")
    if type(count) is not int or count < 0:
        raise ValueError("History 响应缺少有效 count")
    raw_records = body.get("records", [])
    if not isinstance(raw_records, list):
        raise ValueError("History records 不是列表")
    ordinals = page_ordinals(count, page)
    if len(raw_records) != len(ordinals):
        raise ValueError(f"History {history_type}/{page} 返回 {len(raw_records)} 条，预期 {len(ordinals)} 条")
    records = [normalize(raw, history_type) for raw in raw_records]
    uids = {str(raw["playerId"]) for raw in raw_records
            if type(raw.get("playerId")) is int and raw["playerId"] > 0}
    if len(uids) > 1:
        raise ValueError("History 页面包含多个 playerId；已停止写入")
    prior = db.execute("SELECT latest_count FROM history_types WHERE history_type=?", (history_type,)).fetchone()
    if prior and count < prior[0]:
        raise ValueError(f"History 类别 {history_type} 总数从 {prior[0]} 降到 {count}；停止同步以保留旧记录")
    with db:
        if uids:
            save_account_uid(db, next(iter(uids)))
        for ordinal, record in zip(ordinals, records):
            existing = db.execute("""SELECT item_tid,name,timestamp FROM history_records
                WHERE history_type=? AND ordinal=?""", (history_type, ordinal)).fetchone()
            if existing and tuple(existing) != record:
                raise ValueError(f"History 类别 {history_type} 第 {ordinal} 条已变化；停止同步以免覆盖数据")
        db.executemany("""INSERT OR IGNORE INTO history_records
            (history_type, ordinal, item_tid, name, timestamp) VALUES (?,?,?,?,?)""",
            [(history_type, ordinal, *record) for ordinal, record in zip(ordinals, records)])
        db.execute("""INSERT INTO history_types VALUES (?,?) ON CONFLICT(history_type)
            DO UPDATE SET latest_count=excluded.latest_count""", (history_type, count))
    return len(records)


def missing_pages(db: sqlite3.Connection, history_type: int, count: int) -> list[int]:
    present = {row[0] for row in db.execute(
        "SELECT ordinal FROM history_records WHERE history_type=? AND ordinal<?", (history_type, count))}
    return [page for page in range(1, math.ceil(count / PAGE_SIZE) + 1)
            if any(ordinal not in present for ordinal in page_ordinals(count, page))]


def newest_timestamp(db: sqlite3.Connection, history_type: int) -> int | None:
    row = db.execute("""SELECT timestamp FROM history_records WHERE history_type=?
        ORDER BY ordinal DESC LIMIT 1""", (history_type,)).fetchone()
    return row[0] if row else None


def import_raw_pages(db: sqlite3.Connection, path: Path) -> int:
    if not path.exists():
        return 0
    source = json.loads(path.read_text(encoding="utf-8"))
    if source.get("method") != "Summon.QuerySummonHistory":
        raise ValueError("旧数据文件不是已验证的 History 响应")
    added = 0
    for key, body in source.get("pages", {}).items():
        history_type, page = map(int, key.split("/"))
        added += ingest_page(db, history_type, page, body)
    return added


def import_decoded_capture(db: sqlite3.Connection, path: Path) -> int:
    if not path.exists():
        return 0
    source = json.loads(path.read_text(encoding="utf-8"))
    added = 0
    for pair in source.get("summon_rpc_candidates", []):
        request, response = pair.get("request") or {}, pair.get("response") or {}
        if request.get("method") != "Summon.QuerySummonHistory":
            continue
        args, values = request.get("values") or [], response.get("values") or []
        if len(args) != 1 or len(args[0]) != 2 or len(values) != 1 or not values[0]:
            continue
        history_type, page = args[0]
        body = values[0][0]
        added += ingest_page(db, history_type, page, body)
    return added


def export_csv(db: sqlite3.Connection, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = db.execute("""SELECT history_type,ordinal,item_tid,name,timestamp FROM history_records
        ORDER BY history_type, ordinal DESC""").fetchall()
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["history_type", "ordinal", "item_tid", "name", "timestamp", "pulled_at"])
        for row in rows:
            writer.writerow([*row, datetime.fromtimestamp(row[4], CHINA).strftime("%Y-%m-%d %H:%M:%S")])
    return len(rows)


def coverage(db: sqlite3.Connection) -> list[dict]:
    result = []
    for history_type, count in db.execute("SELECT history_type,latest_count FROM history_types ORDER BY history_type"):
        have = db.execute("SELECT COUNT(*) FROM history_records WHERE history_type=? AND ordinal<?",
                          (history_type, count)).fetchone()[0]
        result.append({"history_type": history_type, "known": have, "reported_total": count,
                       "complete": have == count, "missing_pages": len(missing_pages(db, history_type, count))})
    return result
