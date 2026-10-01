"""Create a static dashboard that imports a user's local JSON export."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "web"
DEST = ROOT / "dist" / "site"
CATALOG = ROOT / "resources" / "catalog.public.json"


def main() -> None:
    if not CATALOG.is_file():
        raise SystemExit("缺少可发布的卡池目录")
    DEST.mkdir(parents=True, exist_ok=True)
    for name in ("app.js", "style.css"):
        shutil.copy2(SOURCE / name, DEST / name)
    html = (SOURCE / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="/style.css"', 'href="./style.css"')
    html = html.replace('<script src="/app.js" defer></script>',
                        '<script>window.MORIMENS_STATIC=true;</script><script src="./app.js" defer></script>')
    (DEST / "index.html").write_text(html, encoding="utf-8")
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    (DEST / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    (DEST / ".nojekyll").write_text("", encoding="utf-8")
    print(f"STATIC_SITE_READY {DEST}")


if __name__ == "__main__":
    main()
