"""Fetch the signed-in user's Summon history through a separate read-only RPC session.

Requires a complete local capture of the current game's gateway/main handshake.
The current auth blob is read from Morimens memory via PROCESS_VM_READ and kept
only in RAM. No ticket, token, key, or raw login frame is written by this tool.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import socket
import sys
import time
from pathlib import Path

import lz4.frame
import msgpack
import sympy

import decode_summon_capture as decode
import history_store as store


ROOT = Path(__file__).resolve().parent
RESEARCH = ROOT.parent / "waline-avatars"


def load_research(name: str):
    sys.path.insert(0, str(RESEARCH))
    spec = importlib.util.spec_from_file_location(name, RESEARCH / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def captured_logins(path: Path, reference, pilot):
    found = {}
    for _, segments in reference.read_flows(path, port=12887).items():
        client, cgap = reference.reassemble(segments["c2s"])
        server, sgap = reference.reassemble(segments["s2c"])
        ch, sh = reference.route(client, True), reference.route(server, False)
        if cgap or sgap or not ch or not sh:
            continue
        target = ch["target"]
        if target != "operate-global-game" and not target.startswith("operate-global-game-"):
            continue
        private = sympy.discrete_log(reference.protocol.DH_P, ch["public"], reference.protocol.DH_G)
        secret = reference.protocol.u64le(pow(sh["public"], private, reference.protocol.DH_P))
        key = reference.protocol.derive_rc4_key(secret)
        plain = reference.decoded_client_stream(client[ch["consumed"]:], key)
        frames = list(pilot.app_frames(plain))
        if len(frames) < 2:
            continue
        first, second = frames[:2]
        auth, _ = pilot.packet_fields(first)
        login, blobs = pilot.packet_fields(second)
        if auth.get(0) == 403 and login.get(0) == 1 and blobs[:1] == [b"Login.Login"]:
            found[target] = (first, second, auth.get(1), login.get(1))
    gateway = found.get("operate-global-game")
    mains = [(target, messages) for target, messages in found.items() if target != "operate-global-game"]
    if gateway is None or not mains:
        raise RuntimeError("当前抓包缺少完整的 gateway/main 登录序列")
    return gateway, mains[-1]


def recv_for_session(conn, session: int, pending: bytearray, pilot, timeout: float = 8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while len(pending) >= 2:
            size = int.from_bytes(pending[:2], "big")
            if size < 1 or len(pending) < size + 2:
                break
            frame = bytes(pending[:size + 2])
            del pending[:size + 2]
            try:
                package, blobs = pilot.packet_fields(frame)
            except Exception:
                continue
            if package.get(1) == session:
                return blobs
        conn.sock.settimeout(min(0.5, max(0.05, deadline - time.monotonic())))
        try:
            cipher = conn.sock.recv(65536)
        except socket.timeout:
            continue
        if not cipher:
            raise RuntimeError("查询期间服务器关闭连接")
        pending.extend(conn.rc4_s2c.crypt(cipher))
    raise TimeoutError(f"History session {session} 超时")


def decode_history(blobs, expected_type: int, page: int):
    objects = []
    for blob in blobs:
        if blob.startswith(b"\x04\x22\x4d\x18"):
            blob = lz4.frame.decompress(blob)
        try:
            objects.append(msgpack.unpackb(blob, raw=False, strict_map_key=False))
        except Exception:
            continue
    if len(objects) != 1 or not isinstance(objects[0], list) or not objects[0]:
        raise ValueError(f"History {expected_type}/{page} 响应结构未知")
    body = objects[0][0]
    if not isinstance(body, dict) or not isinstance(body.get("count"), int):
        raise ValueError(f"History {expected_type}/{page} 缺少 count")
    if body["count"] == 0 and "records" not in body:
        body["records"] = []
    if not isinstance(body.get("records"), list):
        raise ValueError(f"History {expected_type}/{page} 缺少 records")
    for record in body["records"]:
        if record.get("type") != expected_type:
            raise ValueError(f"History {expected_type}/{page} 记录类型不一致")
    return body


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", type=Path, default=ROOT / "data" / "local" / "loopback.pcap")
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "local" / "history.sqlite3")
    ap.add_argument("--output", type=Path, default=ROOT / "data" / "local" / "history_all.csv")
    ap.add_argument("--import-raw", type=Path, default=ROOT / "data" / "local" / "full_history_raw.json")
    ap.add_argument("--import-decoded", type=Path, default=ROOT / "data" / "local" / "loopback_decoded_snapshot.json")
    ap.add_argument("--local-only", action="store_true", help="只整理已保存数据，不登录或查询游戏")
    ap.add_argument("--wait-auth", type=int, default=0, help="等待新登录材料的最长秒数")
    ap.add_argument("--types", type=int, nargs="+", default=[1, 2, 16, 17, 10])
    ap.add_argument("--max-pages", type=int, default=1000)
    ap.add_argument("--request-delay", type=float, default=0.05)
    args = ap.parse_args()
    if args.request_delay < 0 or args.max_pages < 1 or any(t < 0 for t in args.types):
        ap.error("类别、最大页数和请求间隔必须有效")
    db = store.connect(args.db)
    imported = 0
    if db.execute("SELECT COUNT(*) FROM history_types").fetchone()[0] == 0:
        imported = store.import_raw_pages(db, args.import_raw)
        imported += store.import_decoded_capture(db, args.import_decoded)
    if imported:
        print("LOCAL_PAGES_LOADED", imported, "records", flush=True)
    store.export_csv(db, args.output)
    if args.local_only:
        print("HISTORY_COVERAGE", json.dumps(store.coverage(db), ensure_ascii=False), flush=True)
        return
    reference = decode.load_reference()
    pilot = load_research("morimens_direct_facade_pilot")
    fresh = load_research("morimens_fresh_memory_login_pilot")
    memory = load_research("inspect_morimens_auth_memory_readonly")
    deadline = time.monotonic() + args.wait_auth
    if args.wait_auth:
        print("WAITING_AUTH", args.wait_auth, flush=True)
    found = None
    while True:
        try:
            found = memory.find_current_auth_blob(
                max_seconds=30 if not args.wait_auth else min(8, max(1, deadline-time.monotonic())))
        except RuntimeError as exc:
            if "expected one Morimens process" in str(exc) and not args.wait_auth:
                print("GAME_NOT_RUNNING", flush=True)
                raise SystemExit(2)
            if not args.wait_auth or "expected one Morimens process" not in str(exc):
                raise
        if found or not args.wait_auth or time.monotonic() >= deadline:
            break
        time.sleep(1)
    if found:
        pid, auth_blob, _ = found
        print("CURRENT_AUTH_IN_RAM", "pid", pid, flush=True)
    else:
        print("NO_CURRENT_AUTH", flush=True)
        raise SystemExit(2)
    gateway, (target, main) = captured_logins(args.capture, reference, pilot)
    gateway_auth, gateway_login, gateway_auth_session, gateway_login_session = gateway
    main_auth, main_login, main_auth_session, main_login_session = main
    gw = fresh.connect_and_login("operate-global-game",
        fresh.replace_auth_blob(gateway_auth, auth_blob), gateway_login,
        gateway_auth_session, gateway_login_session)
    if gw is None:
        raise SystemExit("LOGIN_REJECTED gateway")
    gw[0].sock.close()
    session = fresh.connect_and_login(target,
        fresh.replace_auth_blob(main_auth, auth_blob), main_login,
        main_auth_session, main_login_session)
    if session is None:
        raise SystemExit("LOGIN_REJECTED main")
    conn, compressor, pending = session
    next_session = max(main_auth_session, main_login_session) + 1
    request_count = 0
    interrupted = False
    try:
        for history_type in dict.fromkeys(args.types):
            # Always refresh page one. Count and oldest-based ordinals identify
            # new rows; identical timestamps are checked against row contents.
            pages = [1]
            category_count = None
            while pages:
                page = pages.pop(0)
                if page > args.max_pages:
                    raise ValueError(f"类别 {history_type} 超过 --max-pages")
                rpc = pilot.build_generic_app(next_session, "Summon.QuerySummonHistory", [history_type, page])
                fresh.send_app(conn, compressor, rpc)
                try:
                    blobs = recv_for_session(conn, next_session, pending, pilot)
                except (OSError, TimeoutError, RuntimeError) as exc:
                    print("CONNECTION_PAUSED", type(exc).__name__, "after", request_count,
                          "pages; saved data remains available", flush=True)
                    interrupted = True
                    break
                body = decode_history(blobs, history_type, page)
                if category_count is None:
                    category_count = body["count"]
                elif body["count"] != category_count:
                    raise ValueError(f"类别 {history_type} 查询期间总数变化，请稍后重新同步")
                prior_stamp = store.newest_timestamp(db, history_type) if page == 1 else None
                store.ingest_page(db, history_type, page, body)
                store.export_csv(db, args.output)
                print("HISTORY_PAGE", history_type, page, len(body["records"]), "of", body["count"], flush=True)
                if page == 1:
                    if prior_stamp is not None and body["records"]:
                        reached_old = body["records"][-1]["timestamp"] <= prior_stamp
                        print("INCREMENTAL_BOUNDARY", history_type, "reached" if reached_old else "not_yet", flush=True)
                    pages = [p for p in store.missing_pages(db, history_type, body["count"])
                             if p != 1]
                request_count += 1
                next_session += 1
                if pages and args.request_delay:
                    time.sleep(args.request_delay)
            if interrupted:
                break
    finally:
        conn.sock.close()
        store.export_csv(db, args.output)
    print("HISTORY_COVERAGE", json.dumps(store.coverage(db), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
