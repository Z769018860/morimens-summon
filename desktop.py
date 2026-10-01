"""Windows executable entry point for the local dashboard and worker tasks."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import app
import capture_login_passive
import fetch_all_history
import sync_skeydb_assets


def main() -> None:
    if len(sys.argv) > 2 and sys.argv[1] == "--worker":
        worker = sys.argv[2]
        sys.argv = [worker, *sys.argv[3:]]
        if worker == "fetch-history":
            fetch_all_history.main()
        elif worker == "capture-login":
            capture_login_passive.main()
        elif worker == "self-test":
            for name in ("morimens_direct_facade_pilot", "morimens_fresh_memory_login_pilot",
                         "morimens_fresh_pcap_login_pilot", "inspect_morimens_auth_memory_readonly"):
                fetch_all_history.load_research(name)
            print("PROTOCOL_READY", flush=True)
        else:
            raise SystemExit(f"未知后台任务：{worker}")
        return
    catalog = app.CATALOG
    if not catalog.exists():
        try:
            sync_skeydb_assets.main()
        except Exception as exc:
            seed = Path(__file__).resolve().parent / "resources" / "catalog.public.json"
            if getattr(sys, "frozen", False):
                seed = Path(sys.executable).resolve().parent / "resources" / "catalog.public.json"
            catalog.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(seed, catalog)
            print(f"图片准备暂未完成，已加载卡池目录：{exc}", flush=True)
    sys.argv = [*sys.argv, "--open"]
    app.main()


if __name__ == "__main__":
    main()
