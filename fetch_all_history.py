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
    if not isinstance(body, dict) or not isinstance(body.get("count"), int) or not isinstance(body.get("records"), list):
        raise ValueError(f"History {expected_type}/{page} 缺少 count/records")
    for record in body["records"]:
        if record.get("type") != expected_type:
            raise ValueError(f"History {expected_type}/{page} 记录类型不一致")
    return body


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", type=Path, default=ROOT / "data" / "local" / "loopback.pcap")
    ap.add_argument("--output", type=Path, default=ROOT / "data" / "local" / "full_history_raw.json")
    ap.add_argument("--max-pages", type=int, default=100)
    ap.add_argument("--batch-pages", type=int, default=10)
    args = ap.parse_args()
    reference = decode.load_reference()
    pilot = load_research("morimens_direct_facade_pilot")
    fresh = load_research("morimens_fresh_memory_login_pilot")
    memory = load_research("inspect_morimens_auth_memory_readonly")
    gateway, (target, main) = captured_logins(args.capture, reference, pilot)
    gateway_auth, gateway_login, gateway_auth_session, gateway_login_session = gateway
    main_auth, main_login, main_auth_session, main_login_session = main
    found = memory.find_current_auth_blob(max_seconds=30)
    if found:
        pid, auth_blob, _ = found
        print("CURRENT_AUTH_IN_RAM", "pid", pid, "target", target, flush=True)
    else:
        auth_blob = pilot.packet_fields(main_auth)[1][0]
        if not memory.inspect_candidate(auth_blob):
            raise SystemExit("抓包中的登录材料无法验证；未发送任何请求")
        print("CAPTURED_AUTH_IN_RAM", "target", target, flush=True)
    gw = fresh.connect_and_login("operate-global-game",
        fresh.replace_auth_blob(gateway_auth, auth_blob), gateway_login,
        gateway_auth_session, gateway_login_session)
    if gw is None:
        raise SystemExit("gateway 登录未通过；未查询历史")
    gw[0].sock.close()
    results = {}
    if args.output.exists():
        saved = json.loads(args.output.read_text(encoding="utf-8"))
        if saved.get("method") == "Summon.QuerySummonHistory":
            results = saved.get("pages", {})
    def save_results():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"method": "Summon.QuerySummonHistory", "pages": results},
                                          ensure_ascii=False, indent=2), encoding="utf-8")
    def open_main():
        opened = fresh.connect_and_login(target,
            fresh.replace_auth_blob(main_auth, auth_blob), main_login,
            main_auth_session, main_login_session)
        if opened is None:
            raise RuntimeError("main 登录未通过")
        return opened
    conn, compressor, pending = open_main()
    next_session = max(main_auth_session, main_login_session) + 1
    batch_count = 0
    try:
        for history_type in (2, 1):
            prior = [value["count"] for key, value in results.items() if key.startswith(f"{history_type}/")]
            total = prior[0] if prior else None
            page = 1
            while page <= args.max_pages:
                key = f"{history_type}/{page}"
                if key in results:
                    if page >= math.ceil(total / 5):
                        break
                    page += 1
                    continue
                if batch_count >= args.batch_pages:
                    save_results()
                    print("BATCH_PAUSED", len(results), "pages saved; resume in a later session", flush=True)
                    return
                rpc = pilot.build_generic_app(next_session, "Summon.QuerySummonHistory", [history_type, page])
                fresh.send_app(conn, compressor, rpc)
                blobs = recv_for_session(conn, next_session, pending, pilot)
                body = decode_history(blobs, history_type, page)
                if total is None:
                    total = body["count"]
                elif total != body["count"]:
                    raise ValueError(f"History 类别 {history_type} 的 count 在翻页中改变")
                results[key] = {"count": total, "records": body["records"]}
                save_results()
                print("HISTORY_PAGE", history_type, page, len(body["records"]), "of", total, flush=True)
                batch_count += 1
                if page >= math.ceil(total / 5):
                    break
                page += 1
                next_session += 1
                time.sleep(0.4)
            next_session += 1
            if page >= args.max_pages and total and page < math.ceil(total / 5):
                raise RuntimeError("达到 max-pages，历史尚未取完")
    finally:
        conn.sock.close()
    save_results()
    print("FULL_HISTORY_READY", len(results), "pages", sum(len(x["records"]) for x in results.values()), "records")


if __name__ == "__main__":
    main()
