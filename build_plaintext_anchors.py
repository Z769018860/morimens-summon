"""Extract *validation anchors* from saved UI images, not importable pull data.

The output is an unverified local aid for comparing decoded History responses.
Only a matching protocol response can establish the wire schema and values.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from rapidocr_onnxruntime import RapidOCR


ROOT = Path(__file__).resolve().parent
ROW_Y = (604, 681, 757, 833, 910)


def center(box):
    return sum(point[0] for point in box) / 4, sum(point[1] for point in box) / 4


def normalized_time(value: str) -> str | None:
    s = re.sub(r"\s+", "", value).replace("。", ".")
    match = re.search(r"(20\d\d)\.(\d\d)\.(\d\d)(\d\d):(\d\d):(\d\d)", s)
    if match:
        return f"{match[1]}-{match[2]}-{match[3]} {match[4]}:{match[5]}:{match[6]}"
    return None


def extract_page(path: Path, engine) -> dict:
    result, _ = engine(str(path))
    tokens = []
    for box, text, confidence in result or []:
        x, y = center(box)
        tokens.append({"x": x, "y": y, "text": text, "confidence": round(float(confidence), 3)})
    rows = []
    for position, y in enumerate(ROW_Y, 1):
        row = [t for t in tokens if abs(t["y"] - y) < 31]
        names = [t for t in row if 560 < t["x"] < 1000]
        kinds = [t for t in row if 280 < t["x"] < 560]
        times = [t for t in row if 1430 < t["x"] < 2010]
        name = max(names, key=lambda t: t["confidence"]) if names else None
        kind = max(kinds, key=lambda t: t["confidence"]) if kinds else None
        when = max(times, key=lambda t: t["confidence"]) if times else None
        rows.append({
            "position": position,
            "name": name["text"] if name else None,
            "item_kind": kind["text"] if kind else None,
            "time": normalized_time(when["text"]) if when else None,
            "confidence": min(t["confidence"] for t in (name, kind, when) if t) if name and kind and when else 0,
        })
    page_match = re.search(r"_(\d+)\.jpg$", path.name)
    page = int(page_match[1]) if page_match else None
    printed = [t["text"].strip() for t in tokens if 1085 < t["x"] < 1210 and 1090 < t["y"] < 1165]
    return {
        "source_image": str(path), "expected_page": page, "printed_page": printed,
        "rows": rows,
        "needs_review": (str(page) not in printed or any(not r["name"] or not r["time"] or r["confidence"] < 0.85 for r in rows)),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", type=Path, default=ROOT / "data" / "local" / "captures")
    ap.add_argument("--output", type=Path, default=ROOT / "data" / "local" / "known_plaintext_anchors.json")
    args = ap.parse_args()
    engine = RapidOCR()
    pages = [extract_page(path, engine) for path in sorted(args.images.glob("*_page_*.jpg"))]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"status": "unverified_ui_anchors", "pages": pages},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
    print("ANCHORS_READY", "pages", len(pages), "review", sum(p["needs_review"] for p in pages))


if __name__ == "__main__":
    main()
