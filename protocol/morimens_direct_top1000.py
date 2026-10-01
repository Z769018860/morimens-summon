#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Morimens direct Top1000 collector v7 (transport/RPC research build).

Scope:
- Implements the public Sconn transport handshake observed in Morimens.
- Uses the recovered generic RPC envelope for Rank.QueryRank.
- Does not read/modify/inject into Morimens.exe.
- Does not attempt to bypass account authentication. If the game server requires
  an application bootstrap/login before Rank.QueryRank, the script stops and
  leaves diagnostics instead of forging credentials.

Dependencies:
    pip install msgpack lz4
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import socket
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    import msgpack  # type: ignore
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: msgpack. Run: py -m pip install msgpack lz4") from exc

try:
    import lz4.block  # type: ignore
except Exception as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: lz4. Run: py -m pip install msgpack lz4") from exc


HOST = "z1g-p11222-game-mainport.qookkagames.com"
PORT = 8443
TARGET_SERVER = "operate-global-game-6.operate-global-game.z1-p11222"
DEFAULT_ACTIVITY_TID = 83315

# sconn DH constants from the open-source sconn/skynet crypt implementation.
DH_P = 0xFFFFFFFFFFFFFFC5
DH_G = 5

# Verified against the user's captured Rank.QueryRank plaintext.
CAPTURED_TEMPLATE_HEX = (
    "350000800033550204c402100eff0452616e6b2e517565727952616e6b17000000"
    "94ae41627973734368616c6c656e6765010ace0001450173"
)


class ProtocolError(RuntimeError):
    pass


class RC4:
    """Small stateful RC4 implementation matching the client's stream behavior."""

    def __init__(self, key: bytes):
        if not key:
            raise ValueError("empty RC4 key")
        s = list(range(256))
        j = 0
        for i in range(256):
            j = (j + s[i] + key[i % len(key)]) & 0xFF
            s[i], s[j] = s[j], s[i]
        self.s = s
        self.i = 0
        self.j = 0

    def crypt(self, data: bytes) -> bytes:
        s = self.s
        i = self.i
        j = self.j
        out = bytearray(len(data))
        for p, value in enumerate(data):
            i = (i + 1) & 0xFF
            j = (j + s[i]) & 0xFF
            s[i], s[j] = s[j], s[i]
            k = s[(s[i] + s[j]) & 0xFF]
            out[p] = value ^ k
        self.i = i
        self.j = j
        return bytes(out)


def u64le(value: int) -> bytes:
    return int(value & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")


def hmac64_md5(a: bytes, b: bytes) -> bytes:
    """Equivalent to crypt.hmac64_md5 for two 8-byte inputs.

    The upstream source documents this as:
      md5((a .. b):rep(3)), then XOR the two 8-byte halves.
    """
    if len(a) != 8 or len(b) != 8:
        raise ValueError("hmac64_md5 inputs must be 8 bytes")
    digest = hashlib.md5((a + b) * 3).digest()
    return bytes(x ^ y for x, y in zip(digest[:8], digest[8:16]))


def derive_rc4_key(secret: bytes) -> bytes:
    if len(secret) != 8:
        raise ValueError("secret must be 8 bytes")
    return b"".join(hmac64_md5(secret, bytes([n]) + b"\0" * 7) for n in range(4))


def sproto_pack(src: bytes) -> bytes:
    """Exact implementation of cloudwu/sproto zero-pack format."""
    out = bytearray()
    i = 0
    ff_start = None
    ff_n = 0

    def flush_ff() -> None:
        nonlocal ff_start, ff_n
        if ff_n <= 0 or ff_start is None:
            return
        raw = src[ff_start: ff_start + ff_n * 8]
        raw = raw.ljust(ff_n * 8, b"\0")
        out.append(0xFF)
        out.append(ff_n - 1)
        out.extend(raw)
        ff_start = None
        ff_n = 0

    while i < len(src):
        seg = src[i:i + 8]
        if len(seg) < 8:
            seg = seg + b"\0" * (8 - len(seg))
        nz = sum(1 for x in seg if x != 0)
        effective_nz = 8 if ff_n > 0 and nz in (6, 7) else nz

        if effective_nz == 8:
            if ff_n == 0:
                ff_start = i
                ff_n = 1
            else:
                ff_n += 1
            i += 8
            if ff_n == 256:
                flush_ff()
            continue

        flush_ff()
        mask = 0
        vals = bytearray()
        for bit, x in enumerate(seg):
            if x:
                mask |= 1 << bit
                vals.append(x)
        out.append(mask)
        out.extend(vals)
        i += 8

    flush_ff()
    return bytes(out)


def sproto_unpack(src: bytes) -> bytes:
    out = bytearray()
    pos = 0
    while pos < len(src):
        header = src[pos]
        pos += 1
        if header == 0xFF:
            if pos >= len(src):
                raise ProtocolError("truncated sproto FF run")
            n = (src[pos] + 1) * 8
            pos += 1
            if pos + n > len(src):
                raise ProtocolError("truncated sproto FF data")
            out.extend(src[pos:pos + n])
            pos += n
        else:
            for bit in range(8):
                if (header >> bit) & 1:
                    if pos >= len(src):
                        raise ProtocolError("truncated sproto packed byte")
                    out.append(src[pos])
                    pos += 1
                else:
                    out.append(0)
    return bytes(out)


def encode_sproto_inline_int(value: int) -> int:
    if value < 0 or value >= 0x7FFF:
        raise ValueError("this helper only supports small positive inline ints")
    return (value + 1) * 2


def build_rank_rpc(session: int, start_rank: int, end_rank: int, activity_tid: int) -> bytes:
    """Build the exact plaintext byte stream that is passed to RC4.

    Layers:
      LZ4 block header (raw/uncompressed flag)
        -> 2-byte BE application message length
          -> sproto packed generic RPC package
             package(type=1, session=N)
             generic request(method, MessagePack args)
    """
    method = b"Rank.QueryRank"
    args = msgpack.packb(
        ["AbyssChallenge", int(start_rank), int(end_rank), int(activity_tid)],
        use_bin_type=False,
    )

    # sproto .package: fields type=1 and session=<session>, both inline.
    package = (
        struct.pack("<H", 2)
        + struct.pack("<H", encode_sproto_inline_int(1))
        + struct.pack("<H", encode_sproto_inline_int(session))
    )

    # Recovered generic request struct: two string/binary fields: method and args.
    request = (
        struct.pack("<H", 2)
        + b"\0\0\0\0"
        + struct.pack("<I", len(method)) + method
        + struct.pack("<I", len(args)) + args
    )

    packed = sproto_pack(package + request)
    app_message = len(packed).to_bytes(2, "big") + packed

    # The observed LZ4 transport uses the standard block length high bit to
    # mark an incompressible/raw block. Small Rank.QueryRank requests are raw.
    block_header = struct.pack("<I", len(app_message) | 0x80000000)
    return block_header + app_message


def verify_builder() -> None:
    built = build_rank_rpc(97, 1, 10, DEFAULT_ACTIVITY_TID)
    expected = bytes.fromhex(CAPTURED_TEMPLATE_HEX)
    if built != expected:
        raise AssertionError(
            "request builder mismatch\n"
            f"built   ={built.hex()}\n"
            f"expected={expected.hex()}"
        )


def recv_exact(sock: socket.socket, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise EOFError(f"connection closed while reading {n} bytes")
        out.extend(chunk)
    return bytes(out)


@dataclass
class SconnSession:
    sock: socket.socket
    rc4_c2s: RC4
    rc4_s2c: RC4
    connection_id: int
    target_server: str


def open_sconn(host: str, port: int, target_server: str, timeout: float) -> SconnSession:
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)

    # Generate a fresh legitimate ephemeral DH private value for this new session.
    while True:
        private = int.from_bytes(os.urandom(8), "little") % DH_P
        if private != 0:
            break
    public = pow(DH_G, private, DH_P)
    # IMPORTANT: Morimens does not send the compression marker as a second
    # standalone write. The observed route frame contains it as the fifth
    # newline-delimited field inside the same 2-byte-BE-length frame:
    #   0\n<base64(DH public)>\n<targetServer>\n0\n>lz4
    # The server reply mirrors that shape:
    #   <connectionId>\n<base64(server DH public)>\n>lz4
    hello_body = b"\n".join([
        b"0",
        base64.b64encode(u64le(public)),
        target_server.encode("ascii"),
        b"0",
        b">lz4",
    ])
    sock.sendall(len(hello_body).to_bytes(2, "big") + hello_body)

    reply_len = int.from_bytes(recv_exact(sock, 2), "big")
    if reply_len <= 0 or reply_len > 4096:
        raise ProtocolError(f"invalid sconn reply length: {reply_len}")
    reply = recv_exact(sock, reply_len)
    lines = reply.splitlines()
    if len(lines) < 3:
        raise ProtocolError(f"invalid sconn reply: {reply!r}")
    try:
        connection_id = int(lines[0])
        server_public_raw = base64.b64decode(lines[1], validate=True)
    except Exception as exc:
        raise ProtocolError(f"invalid sconn reply fields: {reply!r}") from exc
    if len(server_public_raw) != 8:
        raise ProtocolError(f"server DH public key has {len(server_public_raw)} bytes")
    if lines[2] != b">lz4":
        raise ProtocolError(f"unexpected compression marker in sconn reply: {lines[2]!r}")

    server_public = int.from_bytes(server_public_raw, "little")
    secret_int = pow(server_public, private, DH_P)
    secret = u64le(secret_int)
    rc4_key = derive_rc4_key(secret)

    return SconnSession(
        sock=sock,
        rc4_c2s=RC4(rc4_key),
        rc4_s2c=RC4(rc4_key),
        connection_id=connection_id,
        target_server=target_server,
    )


class Lz4MessageStream:
    """RC4 -> LZ4 block stream -> 2-byte BE message stream parser."""

    def __init__(self, session: SconnSession):
        self.session = session
        self.block_buf = bytearray()
        self.message_buf = bytearray()
        self.messages: list[bytes] = []
        self.blocks_seen = 0

    def _decode_block(self, payload: bytes, raw: bool) -> bytes:
        if raw:
            return payload
        # Observed transport uses 64 KiB LZ4 block framing. Python's block API
        # accepts a destination capacity and returns the actual decompressed size.
        last_error = None
        for capacity in (65536, 131072, 262144, 1048576):
            try:
                return lz4.block.decompress(payload, uncompressed_size=capacity)
            except Exception as exc:  # try a larger safety cap
                last_error = exc
        raise ProtocolError(f"LZ4 block decompression failed: {last_error}")

    def feed_ciphertext(self, cipher: bytes) -> None:
        plain = self.session.rc4_s2c.crypt(cipher)
        self.block_buf.extend(plain)

        while True:
            if len(self.block_buf) < 4:
                break
            word = struct.unpack_from("<I", self.block_buf, 0)[0]
            raw = bool(word & 0x80000000)
            size = word & 0x7FFFFFFF
            if size > 16 * 1024 * 1024:
                raise ProtocolError(f"implausible LZ4 block size: {size}")
            if len(self.block_buf) < 4 + size:
                break
            payload = bytes(self.block_buf[4:4 + size])
            del self.block_buf[:4 + size]
            self.blocks_seen += 1
            decoded = self._decode_block(payload, raw)
            self.message_buf.extend(decoded)

            while True:
                if len(self.message_buf) < 2:
                    break
                msg_len = int.from_bytes(self.message_buf[:2], "big")
                if msg_len > 8 * 1024 * 1024:
                    raise ProtocolError(f"implausible application message length: {msg_len}")
                if len(self.message_buf) < 2 + msg_len:
                    break
                message = bytes(self.message_buf[2:2 + msg_len])
                del self.message_buf[:2 + msg_len]
                self.messages.append(message)

    def recv_until_message(self, timeout: float) -> bytes:
        if self.messages:
            return self.messages.pop(0)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            self.session.sock.settimeout(remaining)
            try:
                cipher = self.session.sock.recv(65536)
            except socket.timeout:
                continue
            if not cipher:
                raise EOFError("server closed the connection")
            self.feed_ciphertext(cipher)
            if self.messages:
                return self.messages.pop(0)
        raise TimeoutError("timed out waiting for an application response")


def parse_sproto_object_fields(raw: bytes, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """Schema-less parse of one sproto object.

    Returns field records and number of bytes consumed. It preserves unknown tag
    semantics but is enough to recover out-of-line string/binary fields.
    """
    if offset + 2 > len(raw):
        raise ProtocolError("truncated sproto object")
    n = struct.unpack_from("<H", raw, offset)[0]
    header_end = offset + 2 + n * 2
    if n > 1024 or header_end > len(raw):
        raise ProtocolError("invalid sproto object header")
    data_pos = header_end
    fields: list[dict[str, Any]] = []
    logical_tag = -1

    for idx in range(n):
        value = struct.unpack_from("<H", raw, offset + 2 + idx * 2)[0]
        if value & 1:
            skip = (value - 1) // 2 + 1
            logical_tag += skip
            fields.append({"kind": "skip", "skip": skip, "tag": logical_tag})
            continue

        logical_tag += 1
        if value != 0:
            fields.append({
                "kind": "inline",
                "tag": logical_tag,
                "value": value // 2 - 1,
                "encoded": value,
            })
            continue

        if data_pos + 4 > len(raw):
            raise ProtocolError("truncated sproto field length")
        size = struct.unpack_from("<I", raw, data_pos)[0]
        data_pos += 4
        if data_pos + size > len(raw):
            raise ProtocolError("truncated sproto field data")
        value_bytes = raw[data_pos:data_pos + size]
        data_pos += size
        fields.append({"kind": "bytes", "tag": logical_tag, "value": value_bytes})

    return fields, data_pos - offset


def parse_package_and_content(packed: bytes) -> dict[str, Any]:
    unpacked = sproto_unpack(packed)
    package_fields, consumed = parse_sproto_object_fields(unpacked, 0)
    return {
        "unpacked": unpacked,
        "package_fields": package_fields,
        "content": unpacked[consumed:],
        "package_consumed": consumed,
    }


def jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except Exception:
            return {"__bytes_hex__": value.hex()}
    if isinstance(value, dict):
        return {str(jsonable(k)): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def normalize_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, bytes):
                try:
                    k2 = k.decode("utf-8")
                except Exception:
                    k2 = k.hex()
            else:
                k2 = str(k)
            out[k2] = normalize_keys(v)
        return out
    if isinstance(obj, list):
        return [normalize_keys(v) for v in obj]
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8")
        except Exception:
            return obj
    return obj


def unpack_msgpack_exact(blob: bytes) -> list[Any]:
    results: list[Any] = []
    # First try exact whole-buffer decoding.
    for raw in (False, True):
        try:
            obj = msgpack.unpackb(blob, raw=raw, strict_map_key=False)
            results.append(normalize_keys(obj))
            return results
        except Exception:
            pass

    # Then scan plausible starts. This is intentionally bounded.
    max_scan = min(len(blob), 512)
    seen = set()
    for start in range(max_scan):
        first = blob[start]
        plausible = (
            first <= 0xBF
            or 0xC0 <= first <= 0xDF
            or first >= 0xE0
        )
        if not plausible:
            continue
        try:
            unpacker = msgpack.Unpacker(raw=False, strict_map_key=False)
            unpacker.feed(blob[start:])
            obj = next(unpacker)
            consumed = unpacker.tell()
            if consumed <= 0:
                continue
            norm = normalize_keys(obj)
            key = repr(norm)[:1000]
            if key not in seen:
                seen.add(key)
                results.append(norm)
        except Exception:
            continue
    return results[:20]


def extract_msgpack_candidates(content: bytes) -> list[Any]:
    candidates: list[Any] = []
    # Generic response is likely another sproto object whose byte fields carry
    # MessagePack. Parse it first, then fall back to scanning the whole content.
    try:
        fields, _ = parse_sproto_object_fields(content, 0)
        for field in fields:
            if field.get("kind") == "bytes":
                candidates.extend(unpack_msgpack_exact(field["value"]))
    except Exception:
        pass
    candidates.extend(unpack_msgpack_exact(content))

    # Deduplicate in a JSON-ish representation.
    out = []
    seen = set()
    for c in candidates:
        key = repr(c)[:4000]
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out[:30]


def score_rank_rows(obj: Any) -> tuple[int, list[dict[str, Any]]]:
    """Find the most leaderboard-like list inside a decoded object."""
    best_score = 0
    best_rows: list[dict[str, Any]] = []

    def visit(x: Any) -> None:
        nonlocal best_score, best_rows
        if isinstance(x, list):
            dict_rows = [r for r in x if isinstance(r, dict)]
            if dict_rows:
                score = 0
                for row in dict_rows[:50]:
                    keys = {str(k).lower() for k in row.keys()}
                    score += 4 if "rank" in keys else 0
                    score += 4 if "uid" in keys else 0
                    score += 2 if "name" in keys else 0
                    score += 2 if ("score" in keys or "maxscore" in keys) else 0
                if score > best_score:
                    best_score = score
                    best_rows = dict_rows
            for v in x:
                visit(v)
        elif isinstance(x, dict):
            for v in x.values():
                visit(v)

    visit(obj)
    return best_score, best_rows


def choose_rank_rows(candidates: Iterable[Any]) -> tuple[int, list[dict[str, Any]], Any | None]:
    best_score = 0
    best_rows: list[dict[str, Any]] = []
    best_obj = None
    for obj in candidates:
        score, rows = score_rank_rows(obj)
        if score > best_score:
            best_score = score
            best_rows = rows
            best_obj = obj
    return best_score, best_rows, best_obj


def coerce_rank_row(row: dict[str, Any]) -> dict[str, Any]:
    lower = {str(k).lower(): v for k, v in row.items()}
    score = lower.get("score", lower.get("maxscore"))
    return {
        "rank": lower.get("rank"),
        "uid": lower.get("uid"),
        "name": lower.get("name"),
        "score": score,
        "raw": jsonable(row),
    }


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(jsonable(value), ensure_ascii=False, indent=2), encoding="utf-8")


def run_probe_or_collect(args: argparse.Namespace) -> int:
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    diagnostic: dict[str, Any] = {
        "host": args.host,
        "port": args.port,
        "targetServer": args.target_server,
        "activityTid": args.activity_tid,
        "requested": [args.start, args.end],
        "pageSize": args.page_size,
        "verifiedRequestBuilder": True,
        "collectorVersion": "v7",
        "handshakeShape": "2-byte-BE frame: 0\n<dh>\n<target>\n0\n>lz4",
        "events": [],
    }

    print(f"CONNECT {args.host}:{args.port}")
    print(f"TARGET_SERVER {args.target_server}")
    session = open_sconn(args.host, args.port, args.target_server, args.timeout)
    stream = Lz4MessageStream(session)
    print(f"SCONN_OK id={session.connection_id}")
    print("SCONN_ROUTE_AND_LZ4_OK")

    raw_log = (output / "raw_responses.jsonl").open("w", encoding="utf-8")
    all_rows: dict[int, dict[str, Any]] = {}
    session_id = args.session_start
    consecutive_failures = 0

    try:
        page_start = args.start
        while page_start <= args.end:
            page_end = min(args.end, page_start + args.page_size - 1)
            plain = build_rank_rpc(session_id, page_start, page_end, args.activity_tid)
            cipher = session.rc4_c2s.crypt(plain)
            session.sock.sendall(cipher)
            print(f"QUERY session={session_id} ranks={page_start}-{page_end} bytes={len(plain)}")

            try:
                packed_response = stream.recv_until_message(args.response_timeout)
                parsed = parse_package_and_content(packed_response)
                candidates = extract_msgpack_candidates(parsed["content"])
                score, rows, best_obj = choose_rank_rows(candidates)

                record = {
                    "session": session_id,
                    "rankRange": [page_start, page_end],
                    "packedHex": packed_response.hex(),
                    "unpackedHex": parsed["unpacked"].hex(),
                    "packageFields": [
                        {**{k: jsonable(v) for k, v in f.items() if k != "value"},
                         **({"value": jsonable(f["value"])} if "value" in f else {})}
                        for f in parsed["package_fields"]
                    ],
                    "contentHex": parsed["content"].hex(),
                    "msgpackCandidates": jsonable(candidates),
                    "rankCandidateScore": score,
                }
                raw_log.write(json.dumps(record, ensure_ascii=False) + "\n")
                raw_log.flush()

                if rows and score > 0:
                    new_count = 0
                    for row in rows:
                        normalized = coerce_rank_row(row)
                        try:
                            rank = int(normalized["rank"])
                        except Exception:
                            continue
                        if args.start <= rank <= args.end:
                            all_rows[rank] = normalized
                            new_count += 1
                    print(f"RESPONSE rows={len(rows)} accepted={new_count} candidateScore={score}")
                    consecutive_failures = 0
                else:
                    print(f"RESPONSE decoded but no leaderboard rows candidateScore={score}")
                    diagnostic["events"].append({
                        "rankRange": [page_start, page_end],
                        "status": "decoded_no_rank_rows",
                        "candidateScore": score,
                        "bestCandidate": jsonable(best_obj),
                    })
                    consecutive_failures += 1
            except (TimeoutError, EOFError, ProtocolError, OSError) as exc:
                print(f"QUERY_FAILED ranks={page_start}-{page_end}: {type(exc).__name__}: {exc}")
                diagnostic["events"].append({
                    "rankRange": [page_start, page_end],
                    "status": "query_failed",
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                })
                consecutive_failures += 1

            if consecutive_failures >= args.max_consecutive_failures:
                print("STOP after consecutive undecodable/failed responses")
                break

            page_start = page_end + 1
            session_id += 1
            if args.delay > 0:
                time.sleep(args.delay)
    finally:
        raw_log.close()
        try:
            session.sock.close()
        except Exception:
            pass

    rows_sorted = [all_rows[k] for k in sorted(all_rows)]
    write_json(output / "top1000.json", {
        "activityTid": args.activity_tid,
        "source": "Morimens Rank.QueryRank direct protocol query",
        "count": len(rows_sorted),
        "rows": rows_sorted,
    })

    with (output / "top1000.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["rank", "uid", "name", "score"])
        writer.writeheader()
        for row in rows_sorted:
            writer.writerow({k: row.get(k) for k in writer.fieldnames})

    diagnostic["resultCount"] = len(rows_sorted)
    diagnostic["blocksSeen"] = stream.blocks_seen
    diagnostic["complete"] = len(rows_sorted) >= (args.end - args.start + 1)
    if diagnostic["complete"]:
        diagnostic["status"] = "COMPLETE"
    elif rows_sorted:
        diagnostic["status"] = "PARTIAL"
    else:
        diagnostic["status"] = "BOOTSTRAP_OR_RESPONSE_SCHEMA_REQUIRED"
    write_json(output / "diagnostic.json", diagnostic)

    print(f"DONE rows={len(rows_sorted)} output={output.resolve()}")
    if diagnostic["status"] == "BOOTSTRAP_OR_RESPONSE_SCHEMA_REQUIRED":
        print(
            "No rank rows were recovered. This build does not forge/bypass login. "
            "Send diagnostic.json + raw_responses.jsonl for the next parser/bootstrap step."
        )
    return 0 if rows_sorted else 2


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Direct Morimens Top1000 protocol collector")
    p.add_argument("--host", default=HOST)
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--target-server", default=TARGET_SERVER)
    p.add_argument("--activity-tid", type=int, default=DEFAULT_ACTIVITY_TID)
    p.add_argument("--start", type=int, default=1)
    p.add_argument("--end", type=int, default=1000)
    p.add_argument("--page-size", type=int, default=10)
    p.add_argument("--session-start", type=int, default=1)
    p.add_argument("--timeout", type=float, default=8.0)
    p.add_argument("--response-timeout", type=float, default=8.0)
    p.add_argument("--delay", type=float, default=0.20)
    p.add_argument("--max-consecutive-failures", type=int, default=2)
    p.add_argument("--output", default="morimens_top1000_output")
    p.add_argument("--verify-only", action="store_true")
    return p


def main() -> int:
    verify_builder()
    args = build_arg_parser().parse_args()
    if args.verify_only:
        print("REQUEST_BUILDER_OK")
        print(build_rank_rpc(97, 1, 10, DEFAULT_ACTIVITY_TID).hex())
        return 0
    if args.start < 1 or args.end < args.start:
        raise SystemExit("invalid rank range")
    if args.page_size < 1 or args.page_size > 100:
        raise SystemExit("page size must be 1..100")
    return run_probe_or_collect(args)


if __name__ == "__main__":
    raise SystemExit(main())
