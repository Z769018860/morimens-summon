"""Wait for a fresh passive capture, then test one independent Facade query.

The capture contains a live credential. Keep it private and delete it when it is
no longer needed. This program never prints or exports credential bytes.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import time
from pathlib import Path

import lz4.frame
import msgpack
import sympy

import decode_morimens_passive as passive
import morimens_direct_facade_pilot as pilot
from morimens_fresh_memory_login_pilot import connect_and_login, send_app


def login_frames(path: Path, target: str, port: int = 3593):
    for _, segments in passive.read_flows(path, port).items():
        c2s, cgap = passive.reassemble(segments["c2s"])
        s2c, sgap = passive.reassemble(segments["s2c"])
        ch, sh = passive.route(c2s, True), passive.route(s2c, False)
        if cgap or sgap or not ch or not sh or ch["target"] != target:
            continue
        private = sympy.discrete_log(passive.protocol.DH_P, ch["public"], passive.protocol.DH_G)
        secret = passive.protocol.u64le(pow(sh["public"], private, passive.protocol.DH_P))
        key = passive.protocol.derive_rc4_key(secret)
        plain = passive.decoded_client_stream(c2s[ch["consumed"]:], key)
        frames = list(pilot.app_frames(plain))
        if len(frames) < 2:
            continue
        auth_pkg, auth_blobs = pilot.packet_fields(frames[0])
        login_pkg, login_blobs = pilot.packet_fields(frames[1])
        if (auth_pkg.get(0) == 403 and login_pkg.get(0) == 1
                and login_blobs[:1] == [b"Login.Login"] and auth_blobs):
            return frames[0], frames[1], auth_blobs[0]
    return None


def main_login_frames(path: Path, port: int, expected_blob: bytes | None = None):
    """Find the live main route; its numeric shard changes between logins."""
    targets = []
    for _, segments in passive.read_flows(path, port).items():
        c2s, gap = passive.reassemble(segments["c2s"])
        route = passive.route(c2s, True)
        if gap or not route:
            continue
        target = route["target"]
        if (target.startswith("operate-global-game-")
                and ".operate-global-game." in target
                and target not in targets):
            targets.append(target)
    for target in targets:
        frames = login_frames(path, target, port)
        if frames and (expected_blob is None or frames[2] == expected_blob):
            return target, frames
    return None


def credential_fingerprint(blob: bytes) -> str:
    value = json.loads(base64.b64decode(blob, validate=True))
    assert isinstance(value.get("token"), str) and isinstance(value.get("acc"), str)
    return hashlib.sha256(blob).hexdigest()


def wait_for_pair(path: Path, old_fingerprint: str, seconds: int, port: int = 3593):
    deadline = time.monotonic() + seconds
    last_size = -1
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size > 1000:
            size = path.stat().st_size
            if size != last_size:
                last_size = size
                try:
                    gateway = login_frames(path, "operate-global-game", port)
                    matched = main_login_frames(path, port, gateway[2]) if gateway else None
                    if gateway and matched:
                        _, main = matched
                        gateway_fp = credential_fingerprint(gateway[2])
                        main_fp = credential_fingerprint(main[2])
                        if gateway_fp != main_fp:
                            raise RuntimeError("gateway/main credentials differ")
                        if main_fp == old_fingerprint:
                            raise RuntimeError("capture contains the old credential")
                        return gateway, main
                except (EOFError, ValueError, IndexError, KeyError, AssertionError):
                    pass  # Capture may end mid-packet while the writer is active.
        time.sleep(1)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", type=Path, required=True)
    ap.add_argument("--wait", type=int, default=240)
    ap.add_argument("--response-out", type=Path,
                    default=Path("morimens_independent_pilot_response_20260930.json"))
    args = ap.parse_args()
    old = login_frames(pilot.CAPTURE, pilot.TARGET)
    if old is None:
        raise RuntimeError("reference login capture is incomplete")
    old_fingerprint = credential_fingerprint(old[2])
    print("WAITING_FOR_FRESH_LOGIN", flush=True)
    pair = wait_for_pair(args.capture, old_fingerprint, args.wait)
    if pair is None:
        print("NO_FRESH_LOGIN_CAPTURED; no independent requests sent", flush=True)
        return
    gateway, main = pair
    print("FRESH_LOGIN_CAPTURED", "credential_bytes", len(main[2]), flush=True)

    gateway_conn = connect_and_login("operate-global-game", gateway[0], gateway[1], 0, 1)
    if gateway_conn is None:
        print("GATEWAY_LOGIN_REJECTED; no Facade request sent", flush=True)
        return
    gateway_conn[0].sock.close()
    main_conn = connect_and_login(pilot.TARGET, main[0], main[1], 2, 3)
    if main_conn is None:
        print("MAIN_LOGIN_REJECTED; no Facade request sent", flush=True)
        return
    conn, compressor, pending = main_conn
    try:
        query = pilot.build_facade_app(4, 100167859, 83444)
        send_app(conn, compressor, query)
        replies, status = pilot.recv_messages(conn, 8, pending)
        matched = [(pkg, blobs) for pkg, blobs in replies if pkg.get(1) == 4]
        verified = False
        shapes = []
        for _, blobs in matched:
            for blob in blobs:
                try:
                    payload = (lz4.frame.decompress(blob) if blob.startswith(b"\x04\x22\x4d\x18")
                               else blob)
                    obj = msgpack.unpackb(payload, raw=False, strict_map_key=False)
                    shapes.append({"bytes": len(blob), "compression": payload is not blob,
                                   "root": type(obj).__name__, "items": len(obj) if hasattr(obj, "__len__") else 0,
                                   "first_keys": list(obj[0])[:5] if isinstance(obj, list) and obj and isinstance(obj[0], dict) else []})
                    # MessagePack keeps numeric map keys as integers; JSON export
                    # normalizes those keys to strings for the existing validators.
                    normalized = json.loads(json.dumps(obj, ensure_ascii=False))
                    args.response_out.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
                    stage = normalized[0]["abyssChallenge"]["activityId2Data"]["83315"]["stageGroups"]["83444"]
                    verified = stage.get("stageTid") == 153180 and isinstance(stage.get("team"), dict)
                except (ValueError, KeyError, TypeError, IndexError):
                    shapes.append({"bytes": len(blob), "decode": "failed_or_different_shape"})
        print("ONE_FACADE_RESULT", "status", status, "responses", len(matched),
              "verified_team", verified, "shapes", shapes, flush=True)
    finally:
        conn.sock.close()


if __name__ == "__main__":
    main()
