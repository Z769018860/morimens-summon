"""Offline entry point for manually transcribed Morimens summon history."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "local" / "summons.sqlite3"
FIELDS = ("source_ref", "position", "pool", "pulled_at", "name", "tid", "rarity", "notes")
POOLS = {"character", "minglun", "other"}
RARITIES = {"", "R", "SR", "SSR"}


def parse_row(raw: dict[str, str], line: int) -> dict[str, object]:
    row = {field: (raw.get(field) or "").strip() for field in FIELDS}
    if not row["source_ref"]:
        raise ValueError(f"第 {line} 行缺少 source_ref（截图或页面的唯一标识）")
    if not row["name"]:
        raise ValueError(f"第 {line} 行缺少 name")
    if row["pool"] not in POOLS:
        raise ValueError(f"第 {line} 行 pool 应为 character、minglun 或 other")
    if row["rarity"].upper() not in RARITIES:
        raise ValueError(f"第 {line} 行 rarity 应为 R、SR、SSR 或留空")
    try:
        position = int(row["position"])
        if position < 1:
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"第 {line} 行 position 应为正整数") from exc
    if row["tid"]:
        try:
            tid = int(row["tid"])
            if tid < 1:
                raise ValueError
        except ValueError as exc:
            raise ValueError(f"第 {line} 行 tid 应为正整数或留空") from exc
    else:
        tid = None
    if row["pulled_at"]:
        try:
            datetime.strptime(row["pulled_at"], "%Y-%m-%d %H:%M:%S")
        except ValueError as exc:
            raise ValueError(f"第 {line} 行 pulled_at 应为 YYYY-MM-DD HH:MM:SS 或留空") from exc
    return {
        **row,
        "position": position,
        "tid": tid,
        "rarity": row["rarity"].upper(),
    }


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("""
        CREATE TABLE IF NOT EXISTS pulls (
            source_ref TEXT NOT NULL,
            position INTEGER NOT NULL,
            pool TEXT NOT NULL,
            pulled_at TEXT NOT NULL,
            name TEXT NOT NULL,
            tid INTEGER,
            rarity TEXT NOT NULL,
            notes TEXT NOT NULL,
            PRIMARY KEY (source_ref, position)
        )
    """)
    return db


def import_csv(path: Path, db_path: Path, replace: bool = False) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(FIELDS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV 缺少列：{', '.join(sorted(missing))}")
        rows = [parse_row(raw, i) for i, raw in enumerate(reader, 2)]
    keys = [(row["source_ref"], row["position"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("CSV 中存在重复的 source_ref + position，请先校对")
    with connect(db_path) as db:
        before = db.total_changes
        if replace:
            db.executemany("""
                INSERT INTO pulls VALUES (:source_ref, :position, :pool, :pulled_at, :name, :tid, :rarity, :notes)
                ON CONFLICT(source_ref, position) DO UPDATE SET
                    pool=excluded.pool, pulled_at=excluded.pulled_at,
                    name=excluded.name, tid=excluded.tid,
                    rarity=excluded.rarity, notes=excluded.notes
            """, rows)
        else:
            db.executemany(
                "INSERT OR IGNORE INTO pulls VALUES (:source_ref, :position, :pool, :pulled_at, :name, :tid, :rarity, :notes)",
                rows,
            )
        changed = db.total_changes - before
    if replace:
        print(f"读取 {len(rows)} 条；写入或更新 {changed} 条。")
    else:
        print(f"读取 {len(rows)} 条；新增 {changed} 条；已存在 {len(rows) - changed} 条。")


def fetch_rows(db_path: Path) -> list[dict[str, object]]:
    with connect(db_path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(
            "SELECT * FROM pulls ORDER BY pool, pulled_at DESC, source_ref, position"
        )]


def export_rows(db_path: Path, path: Path) -> None:
    rows = fetch_rows(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"已导出 {len(rows)} 条到 {path}")


def stats(db_path: Path) -> None:
    rows = fetch_rows(db_path)
    by_pool = Counter(str(row["pool"]) for row in rows)
    by_rarity = Counter(str(row["rarity"]) or "未标注" for row in rows)
    print(json.dumps({
        "已录入条数": len(rows),
        "按页面类别": dict(sorted(by_pool.items())),
        "按人工标注稀有度": dict(sorted(by_rarity.items())),
        "提示": "仅统计已录入记录；页面类别不等于已验证的服务端卡池 ID，不能据此计算保底。",
    }, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="忘却前夜抽卡历史：离线 CSV 录入与基础统计")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="本地数据库路径")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("stats", help="查看已录入条数")
    importer = commands.add_parser("import", help="导入人工核对后的 CSV")
    importer.add_argument("csv", type=Path)
    importer.add_argument("--replace", action="store_true", help="用 CSV 内容覆盖相同 source_ref + position 的旧条目")
    exporter = commands.add_parser("export", help="导出 CSV 备份")
    exporter.add_argument("csv", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "import":
            import_csv(args.csv, args.db, args.replace)
        elif args.command == "export":
            export_rows(args.db, args.csv)
        else:
            stats(args.db)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"错误：{exc}\n")


if __name__ == "__main__":
    main()
