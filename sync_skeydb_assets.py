"""Build a private local art/catalog cache from SKeyDB and Chinese name maps.

SKeyDB's game art is not licensed under its source-code MIT license. Downloaded
art stays in data/local and web/assets, both excluded from Git.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
UPSTREAM = ROOT / "data" / "local" / "SKeyDB"
LABELS = ROOT / "resources" / "labels.zh-CN.json"
ASSETS = ROOT / "web" / "assets"
CATALOG = ROOT / "data" / "local" / "catalog.json"
SOURCE = "https://github.com/dansa/SKeyDB"
BANNER_TITLES = {
    "Triune Verdant": "三相衡生",
    "Sylvan Omen": "因果苗圃",
    "Sin-Bound Glory / Sullied White": "罪缚的荣光 / 秽染雏白",
}
BANNER_KIND = {"awaken": "限时唤醒", "rerun": "复刻唤醒", "combo": "组合唤醒",
               "premium": "精选唤醒", "selector": "自选唤醒", "daily": "每日唤醒"}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def slug(name: str) -> str:
    value = name.strip().lower()
    if value == '"24"':
        return "mason"
    if value == "jenkins":
        return "jenkin"
    import re
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", re.sub(r"['\"]", "", value.replace(":", " ")))).strip("-")


def ensure_upstream() -> None:
    if UPSTREAM.exists():
        return
    UPSTREAM.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", SOURCE, str(UPSTREAM)], check=True)


def main() -> None:
    ensure_upstream()
    root = UPSTREAM / "src" / "data" / "public-v3"
    awakeners = load(root / "catalogs" / "awakeners.json")["records"]
    wheels = load(root / "catalogs" / "wheels.json")["records"]
    asset_index = load(root / "indexes" / "assets.json")
    if "assets" in asset_index:
        asset_index = asset_index["assets"]
    names = load(LABELS)
    character_names = names["characters"]
    wheel_names = names["wheels"]
    characters = []
    retained = {"characters": set(), "wheels": set()}
    for item in awakeners:
        art = UPSTREAM / "src" / "assets" / "awk-portraits" / f"{slug(item['name'])}.webp"
        icon = None
        if item.get("rarity") == "SSR" and art.is_file():
            destination = ASSETS / "characters" / f"{item['id']}.webp"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(art, destination)
            icon = f"/assets/characters/{destination.name}"
            retained["characters"].add(destination.name)
        characters.append({"id": item["id"], "name": character_names.get(item["id"], item["name"]),
                           "englishName": item["name"], "rarity": item.get("rarity"), "icon": icon})
    wheel_items = []
    for item in wheels:
        asset_id = (item.get("assets") or {}).get("icon")
        asset = asset_index.get(asset_id, {}) if asset_id else {}
        art_name = asset.get("assetId")
        art = UPSTREAM / "src" / "assets" / "wheels" / f"{art_name}.webp" if art_name else None
        icon = None
        if item.get("rarity") == "SSR" and art and art.is_file():
            destination = ASSETS / "wheels" / f"{item['id']}.webp"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(art, destination)
            icon = f"/assets/wheels/{destination.name}"
            retained["wheels"].add(destination.name)
        wheel_items.append({"id": item["id"], "name": wheel_names.get(item["id"], item["name"]),
                            "englishName": item["name"], "rarity": item.get("rarity"), "icon": icon})
    for category, keep in retained.items():
        folder = (ASSETS / category).resolve()
        if not folder.is_relative_to(ASSETS.resolve()):
            raise RuntimeError("图片缓存路径不安全")
        for cached in folder.glob("*.webp"):
            if cached.name not in keep:
                cached.unlink()
    banners = load(UPSTREAM / "src" / "data" / "timeline" / "banners.json")
    english_to_zh = {x["englishName"].casefold(): x["name"] for x in characters + wheel_items}
    for banner in banners:
        featured = banner.get("featured") or []
        banner["featuredZh"] = [english_to_zh.get((x.get("name") if isinstance(x, dict) else x).casefold(),
                                                   x.get("name") if isinstance(x, dict) else x)
                                for x in featured]
        title = banner["title"].replace(" rerun", "")
        banner["titleZh"] = BANNER_TITLES.get(title) or (
            f"{BANNER_KIND.get(banner.get('type'), '活动唤醒')} · {' / '.join(banner['featuredZh'])}"
            if banner["featuredZh"] else BANNER_KIND.get(banner.get("type"), "活动唤醒"))
    revision = subprocess.check_output(["git", "-C", str(UPSTREAM), "rev-parse", "HEAD"], text=True).strip()
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    CATALOG.write_text(json.dumps({"source": SOURCE, "revision": revision,
                                   "characters": characters, "wheels": wheel_items,
                                   "banners": banners}, ensure_ascii=False), encoding="utf-8")
    print("CATALOG_READY", len(characters), "characters", sum(bool(x["icon"]) for x in characters), "portraits",
          len(wheel_items), "wheels", sum(bool(x["icon"]) for x in wheel_items), "wheel art")


if __name__ == "__main__":
    main()
