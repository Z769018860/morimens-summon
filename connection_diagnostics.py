"""Show non-secret Morimens connection and launch diagnostics."""

from __future__ import annotations

import json

import psutil


def snapshot() -> dict:
    games = []
    for process in psutil.process_iter(["pid", "name", "ppid"]):
        if (process.info["name"] or "").lower() != "morimens.exe":
            continue
        try:
            parent = psutil.Process(process.info["ppid"]).name() if process.info["ppid"] else None
        except psutil.Error:
            parent = None
        try:
            connections = [
                {"remote_address": conn.raddr.ip, "remote_port": conn.raddr.port,
                 "state": conn.status}
                for conn in process.connections(kind="tcp") if conn.raddr
            ]
        except psutil.Error:
            connections = []
        games.append({"pid": process.pid, "parent_process": parent,
                      "steam_parent": (parent or "").lower() == "steam.exe",
                      "connections": connections})
    return {"game_processes": games,
            "note": "父进程仅用于诊断；认证方式须以实际登录握手为准。"}


if __name__ == "__main__":
    print(json.dumps(snapshot(), ensure_ascii=False, indent=2))
