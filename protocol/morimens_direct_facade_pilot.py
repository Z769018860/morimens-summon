"""Small standalone Morimens login/Facade pilot from a private local capture.

No game process access. Captured credential bytes stay in memory and are never
printed or exported. Run a single login or one Facade query, then close.
"""

import argparse
import socket
import time
from pathlib import Path

import lz4.frame
import msgpack
import sympy

import decode_morimens_passive as passive


ROOT = Path(__file__).resolve().parent
CAPTURE = ROOT.parent / "data" / "local" / "session.pcap"
TARGET = "operate-global-game-9.operate-global-game.z1-p11222"
HOST = "z1g-p11222-game-mainport.qookkagames.com"


def app_frames(stream):
    pos = 0
    while pos + 2 <= len(stream):
        n = int.from_bytes(stream[pos:pos + 2], "big")
        if n < 1 or pos + 2 + n > len(stream):
            break
        yield stream[pos:pos + 2 + n]
        pos += 2 + n


def packet_fields(app_frame):
    parsed = passive.protocol.parse_package_and_content(app_frame[2:])
    package = {f["tag"]: f.get("value") for f in parsed["package_fields"]
               if f["kind"] != "skip"}
    fields, _ = passive.protocol.parse_sproto_object_fields(parsed["content"])
    blobs = [f["value"] for f in fields if f["kind"] == "bytes"]
    return package, blobs


def captured_login_messages(path):
    for flow, segments in passive.read_flows(path, 3593).items():
        c2s, cgap = passive.reassemble(segments["c2s"])
        s2c, sgap = passive.reassemble(segments["s2c"])
        ch, sh = passive.route(c2s, True), passive.route(s2c, False)
        if cgap or sgap or not ch or not sh or ch["target"] != TARGET:
            continue
        private = sympy.discrete_log(passive.protocol.DH_P, ch["public"], passive.protocol.DH_G)
        secret = passive.protocol.u64le(pow(sh["public"], private, passive.protocol.DH_P))
        key = passive.protocol.derive_rc4_key(secret)
        plain = passive.decoded_client_stream(c2s[ch["consumed"]:], key)
        frames = list(app_frames(plain))
        if len(frames) < 2:
            continue
        first, second = frames[:2]
        p1, _ = packet_fields(first)
        p2, b2 = packet_fields(second)
        if p1.get(0) == 403 and p2.get(0) == 1 and b2[:1] == [b"Login.Login"]:
            return first, second
    raise RuntimeError("complete captured authentication and Login.Login pair not found")


def build_generic_app(session, method, args_value):
    if isinstance(method, str):
        method = method.encode("ascii")
    args = msgpack.packb(args_value, use_bin_type=False)
    enc = passive.protocol.encode_sproto_inline_int
    package = b"\x02\x00" + enc(1).to_bytes(2, "little") + enc(session).to_bytes(2, "little")
    request = b"\x02\x00\x00\x00\x00\x00" + len(method).to_bytes(4, "little") + method + len(args).to_bytes(4, "little") + args
    packed = passive.protocol.sproto_pack(package + request)
    return len(packed).to_bytes(2, "big") + packed


def build_facade_app(session, uid, stage_group, activity_tid=83315):
    return build_generic_app(session, b"Facade.QueryFacadeFields",
                             [uid, {"Type": "abyssChallenge", "param": {
                                 "activityTid": int(activity_tid), "stageGroupId": stage_group}}])


def build_rank_app(session, start_rank, end_rank, activity_tid=83315):
    return build_generic_app(session, b"Rank.QueryRank",
                             ["AbyssChallenge", int(start_rank), int(end_rank), int(activity_tid)])


def recv_messages(conn, timeout, pending):
    deadline = time.monotonic() + timeout
    result = []
    while time.monotonic() < deadline:
        conn.sock.settimeout(min(0.5, max(0.05, deadline - time.monotonic())))
        try:
            cipher = conn.sock.recv(65536)
        except socket.timeout:
            continue
        if not cipher:
            return result, "closed"
        pending.extend(conn.rc4_s2c.crypt(cipher))
        while len(pending) >= 2:
            n = int.from_bytes(pending[:2], "big")
            if n == 0 or len(pending) < n + 2:
                break
            msg = bytes(pending[2:n + 2])
            del pending[:n + 2]
            try:
                pkg, blobs = packet_fields(len(msg).to_bytes(2, "big") + msg)
                result.append((pkg, blobs))
            except Exception:
                result.append(({}, []))
    return result, "timeout"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", type=Path, default=CAPTURE)
    ap.add_argument("--live-login", action="store_true")
    ap.add_argument("--one-facade", action="store_true")
    ap.add_argument("--uid", type=int, default=100167859)
    ap.add_argument("--stage-group", type=int, default=83444)
    args = ap.parse_args()
    if args.one_facade and not args.live_login:
        ap.error("--one-facade requires --live-login")
    first, second = captured_login_messages(args.capture)
    print("CAPTURED_LOGIN_VALID", "auth_bytes", len(first), "login_bytes", len(second))
    test_facade = build_facade_app(4, args.uid, args.stage_group)
    package, blobs = packet_fields(test_facade)
    assert package == {0: 1, 1: 4} and blobs[0] == b"Facade.QueryFacadeFields"
    print("FACADE_BUILDER_VALID", "bytes", len(test_facade))
    if not args.live_login:
        return

    conn = passive.protocol.open_sconn(HOST, 8443, TARGET, 8)
    compressor = lz4.frame.LZ4FrameCompressor(block_size=lz4.frame.BLOCKSIZE_MAX64KB,
                                              block_linked=False, auto_flush=True)
    try:
        pending = bytearray()
        conn.sock.sendall(conn.rc4_c2s.crypt(compressor.begin() + compressor.compress(first)))
        auth_responses, auth_status = recv_messages(conn, 2, pending)
        auth_seen = sorted({pkg.get(1) for pkg, _ in auth_responses if isinstance(pkg.get(1), int)})
        print("AUTH_PROBE", "status", auth_status, "sessions", auth_seen,
              "session2_blob_lengths", [list(map(len, blobs)) for pkg, blobs in auth_responses if pkg.get(1) == 2])
        if auth_status == "closed" or 2 not in auth_seen:
            return
        conn.sock.sendall(conn.rc4_c2s.crypt(compressor.compress(second)))
        responses, status = recv_messages(conn, 8, pending)
        seen = sorted({pkg.get(1) for pkg, _ in responses if isinstance(pkg.get(1), int)})
        print("LOGIN_PROBE", "sconn", conn.connection_id, "status", status,
              "responses", len(responses), "sessions", seen[:30],
              "login_response", 3 in seen)
        if args.one_facade and 3 in seen:
            conn.sock.sendall(conn.rc4_c2s.crypt(compressor.compress(test_facade)))
            replies, status = recv_messages(conn, 8, pending)
            found = [(pkg, blobs) for pkg, blobs in replies if pkg.get(1) == 4]
            print("FACADE_PROBE", "status", status, "responses", len(replies),
                  "session4", bool(found))
    finally:
        conn.sock.close()


if __name__ == "__main__":
    main()
