"""Single fresh-token login and one Facade query, no UI or process writes.

Requires the user to have normally restarted the game recently. Reads its
current auth blob via PROCESS_VM_READ and keeps it in RAM only.
"""

import json
import time

import lz4.frame
import msgpack
import sympy

import decode_morimens_passive as passive
import inspect_morimens_auth_memory_readonly as memory
import morimens_direct_facade_pilot as pilot


def captured_gateway_messages():
    for _, segments in passive.read_flows(pilot.CAPTURE, 3593).items():
        c, cg = passive.reassemble(segments["c2s"])
        s, sg = passive.reassemble(segments["s2c"])
        ch, sh = passive.route(c, True), passive.route(s, False)
        if cg or sg or not ch or not sh or ch["target"] != "operate-global-game":
            continue
        private = sympy.discrete_log(passive.protocol.DH_P, ch["public"], passive.protocol.DH_G)
        secret = passive.protocol.u64le(pow(sh["public"], private, passive.protocol.DH_P))
        key = passive.protocol.derive_rc4_key(secret)
        plain = passive.decoded_client_stream(c[ch["consumed"]:], key)
        return list(pilot.app_frames(plain))[:2]
    raise RuntimeError("gateway transcript missing")


def replace_auth_blob(frame, new_blob):
    parsed = passive.protocol.parse_package_and_content(frame[2:])
    pkg = {f["tag"]: f.get("value") for f in parsed["package_fields"]
           if f["kind"] != "skip"}
    fields, consumed = passive.protocol.parse_sproto_object_fields(parsed["content"])
    assert pkg.get(0) == 403 and fields[0]["kind"] == "bytes"
    blobs = [new_blob] + [f["value"] for f in fields[1:] if f["kind"] == "bytes"]
    enc = passive.protocol.encode_sproto_inline_int
    package = b"\x02\x00" + enc(403).to_bytes(2, "little") + enc(pkg[1]).to_bytes(2, "little")
    content = len(blobs).to_bytes(2, "little") + b"\0\0" * len(blobs)
    content += b"".join(len(b).to_bytes(4, "little") + b for b in blobs)
    content += parsed["content"][consumed:]
    packed = passive.protocol.sproto_pack(package + content)
    out = len(packed).to_bytes(2, "big") + packed
    return out


def send_app(conn, compressor, message, first=False):
    plain = (compressor.begin() if first else b"") + compressor.compress(message)
    conn.sock.sendall(conn.rc4_c2s.crypt(plain))


def connect_and_login(target, auth_frame, login_frame, auth_session, login_session):
    conn = passive.protocol.open_sconn(pilot.HOST, 8443, target, 8)
    compressor = lz4.frame.LZ4FrameCompressor(block_size=lz4.frame.BLOCKSIZE_MAX64KB,
                                              block_linked=False, auto_flush=True)
    pending = bytearray()
    send_app(conn, compressor, auth_frame, first=True)
    auth_replies, auth_status = pilot.recv_messages(conn, 3, pending)
    auth = [(pkg, blobs) for pkg, blobs in auth_replies if pkg.get(1) == auth_session]
    accepted = any(any(len(b) >= 16 for b in blobs) for _, blobs in auth)
    print("AUTH_RESULT", target, "status", auth_status, "reply_count", len(auth),
          "accepted_shape", accepted)
    if not accepted:
        conn.sock.close()
        return None
    send_app(conn, compressor, login_frame)
    login_replies, login_status = pilot.recv_messages(conn, 6, pending)
    login = [(pkg, blobs) for pkg, blobs in login_replies if pkg.get(1) == login_session]
    print("LOGIN_RESULT", target, "status", login_status, "reply_count", len(login),
          "blob_lengths", [list(map(len, b)) for _, b in login])
    if not login:
        conn.sock.close()
        return None
    return conn, compressor, pending


def main():
    old_hash = memory.captured_hash()
    found = memory.find_current_auth_blob(old_hash)
    if not found or found[2][1] == old_hash:
        print("FRESH_TOKEN_NOT_FOUND; no network requests sent")
        return
    pid, token_blob, detail = found
    print("FRESH_TOKEN_IN_MEMORY", "pid", pid, "token_chars", detail[0])
    gateway_auth, gateway_login = captured_gateway_messages()
    main_auth, main_login = pilot.captured_login_messages(pilot.CAPTURE)
    assert replace_auth_blob(gateway_auth, pilot.packet_fields(gateway_auth)[1][0]) == gateway_auth
    assert replace_auth_blob(main_auth, pilot.packet_fields(main_auth)[1][0]) == main_auth

    gateway = connect_and_login("operate-global-game", replace_auth_blob(gateway_auth, token_blob),
                                gateway_login, 0, 1)
    if not gateway:
        return
    gateway[0].sock.close()
    main = connect_and_login(pilot.TARGET, replace_auth_blob(main_auth, token_blob),
                             main_login, 2, 3)
    if not main:
        return
    conn, compressor, pending = main
    try:
        query = pilot.build_facade_app(4, 100167859, 83444)
        send_app(conn, compressor, query)
        replies, status = pilot.recv_messages(conn, 8, pending)
        found = [(pkg, blobs) for pkg, blobs in replies if pkg.get(1) == 4]
        good = False
        for _, blobs in found:
            for blob in blobs:
                if blob.startswith(b"\x04\x22\x4d\x18"):
                    try:
                        obj = msgpack.unpackb(lz4.frame.decompress(blob), raw=False,
                                              strict_map_key=False)
                        detail = obj[0]["abyssChallenge"]["activityId2Data"]["83315"]["stageGroups"]["83444"]
                        good = detail.get("stageTid") == 153180 and isinstance(detail.get("team"), dict)
                    except Exception:
                        pass
        print("ONE_FACADE_RESULT", "status", status, "reply_count", len(found),
              "verified_team", good)
    finally:
        conn.sock.close()


if __name__ == "__main__":
    main()
