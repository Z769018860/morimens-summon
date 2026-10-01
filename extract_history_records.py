"""Validate and structure real Summon.QuerySummonHistory RPC pairs."""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CHINA = timezone(timedelta(hours=8))
METHOD = "Summon.QuerySummonHistory"
POOL = {1: "minglun", 2: "character"}


def parse_pair(pair: dict) -> tuple[tuple[int, int], dict] | None:
    req = pair.get("request") or {}
    res = pair.get("response") or {}
    if req.get("method") != METHOD:
        return None
    args = req.get("values") or []
    if len(args) != 1 or not isinstance(args[0], list) or len(args[0]) != 2:
        raise ValueError("History 请求参数不符合已验证的 [type, page] 结构")
    history_type, page = args[0]
    if not isinstance(history_type, int) or history_type < 0 or not isinstance(page, int) or page < 1:
        raise ValueError(f"未知 History 类别或页码：{args[0]}")
    values = res.get("values") or []
    if len(values) != 1 or not isinstance(values[0], list) or not values[0]:
        raise ValueError(f"History {history_type}/{page} 响应缺少结果对象")
    body = values[0][0]
    if not isinstance(body, dict) or not isinstance(body.get("count"), int):
        raise ValueError(f"History {history_type}/{page} 响应字段不符合预期")
    if body["count"] == 0 and "records" not in body:
        body["records"] = []
    if not isinstance(body.get("records"), list):
        raise ValueError(f"History {history_type}/{page} 响应 records 字段不符合预期")
    records = []
    for position, raw in enumerate(body["records"], 1):
        if not isinstance(raw, dict) or raw.get("type") != history_type:
            raise ValueError(f"History {history_type}/{page} 第 {position} 条类型不一致")
        tid = raw.get("itemTid")
        timestamp = raw.get("timestamp")
        name = raw.get("name")
        if not isinstance(tid, int) or not isinstance(timestamp, int) or not isinstance(name, str):
            raise ValueError(f"History {history_type}/{page} 第 {position} 条缺少 tid/时间/名称")
        match = re.fullmatch(r"Item_(\d+)_Name\|(.*)", name)
        if not match or int(match[1]) != tid:
            raise ValueError(f"History {history_type}/{page} 第 {position} 条名称与 tid 不一致")
        records.append({
            "position": position,
            "history_type": history_type,
            "pool": POOL.get(history_type, f"type_{history_type}"),
            "page": page,
            "item_tid": tid,
            "name": match[2],
            "timestamp": timestamp,
            "pulled_at": datetime.fromtimestamp(timestamp, CHINA).strftime("%Y-%m-%d %H:%M:%S"),
            "item_type": raw.get("type"),
        })
    return (history_type, page), {"count": body["count"], "records": records}


def parse_direct_page(key: str, body: dict):
    history_type, page = map(int, key.split("/"))
    return parse_pair({"request": {"method": METHOD, "values": [[history_type, page]]},
                       "response": {"values": [[body, 1]]}})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("decoded", type=Path)
    ap.add_argument("--output", type=Path, default=ROOT / "data" / "local" / "history_records.json")
    args = ap.parse_args()
    source = json.loads(args.decoded.read_text(encoding="utf-8"))
    pages = {}
    conflicts = []
    for pair in source.get("summon_rpc_candidates", []):
        parsed = parse_pair(pair)
        if not parsed:
            continue
        key, value = parsed
        if key in pages and pages[key] != value:
            conflicts.append(key)
        else:
            pages[key] = value
    for raw_key, body in source.get("pages", {}).items():
        key, value = parse_direct_page(raw_key, body)
        if key in pages and pages[key] != value:
            conflicts.append(key)
        else:
            pages[key] = value
    if conflicts:
        raise SystemExit(f"重复请求返回不一致：{conflicts}")
    totals = {str(t): sorted({v["count"] for (kind, _), v in pages.items() if kind == t})
              for t in sorted({kind for kind, _ in pages})}
    if any(len(values) > 1 for values in totals.values()):
        raise SystemExit(f"同一类别的总数不一致：{totals}")
    result = {
        "source": str(args.decoded),
        "method": METHOD,
        "totals_by_type": totals,
        "pages": [{"history_type": t, "page": p, **value} for (t, p), value in sorted(pages.items())],
        "record_count": sum(len(value["records"]) for value in pages.values()),
        "complete": bool(pages) and all(
            {p for kind, p in pages if kind == t} == set(range(1, (totals[str(t)][0] + 4) // 5 + 1))
            and sum(len(value["records"]) for (kind, _), value in pages.items() if kind == t) == totals[str(t)][0]
            for t in {kind for kind, _ in pages}),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = args.output.with_suffix(".csv")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["history_type", "pool", "page", "position", "item_tid", "name", "timestamp", "pulled_at", "item_type"])
        writer.writeheader()
        for page in result["pages"]:
            writer.writerows(page["records"])
    print("HISTORY_READY", "pages", len(pages), "records", result["record_count"],
          "totals", totals, "complete", result["complete"])


if __name__ == "__main__":
    main()
