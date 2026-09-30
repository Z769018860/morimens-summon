"""Decode a complete passive Morimens TCP/8443 capture for Summon RPC study.

Raw packets and all decoded data stay in data/local. This local research tool
uses the already verified protocol implementation in the sibling research
workspace; the user-facing importer remains independent of it.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from collections import Counter
from pathlib import Path

import lz4.block
import lz4.frame
import msgpack
import sympy


ROOT = Path(__file__).resolve().parent
RESEARCH = ROOT.parent / "waline-avatars" / "decode_morimens_passive.py"


def load_reference():
    if not RESEARCH.exists():
        raise FileNotFoundError(f"缺少本地协议研究模块：{RESEARCH}")
    spec = importlib.util.spec_from_file_location("morimens_passive_reference", RESEARCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def split_messages(raw: bytes) -> tuple[list[bytes], int]:
    messages = []
    pos = 0
    while pos + 2 <= len(raw):
        size = int.from_bytes(raw[pos:pos + 2], "big")
        if not 2 <= size <= 65535 or pos + 2 + size > len(raw):
            break
        messages.append(raw[pos + 2:pos + 2 + size])
        pos += 2 + size
    return messages, len(raw) - pos


def decode_block_stream(raw: bytes) -> bytes:
    result = bytearray()
    pos = 0
    while pos + 4 <= len(raw):
        word = int.from_bytes(raw[pos:pos + 4], "little")
        size = word & 0x7FFFFFFF
        if size == 0 or size > 16 * 1024 * 1024 or pos + 4 + size > len(raw):
            break
        payload = raw[pos + 4:pos + 4 + size]
        if word & 0x80000000:
            result.extend(payload)
        else:
            for capacity in (65536, 131072, 262144, 1048576):
                try:
                    result.extend(lz4.block.decompress(payload, uncompressed_size=capacity))
                    break
                except Exception:
                    continue
            else:
                break
        pos += 4 + size
    return bytes(result)


def decode_transport(cipher: bytes, key: bytes, protocol) -> tuple[list[bytes], str]:
    plain = protocol.RC4(key).crypt(cipher)
    candidates = [("direct", plain), ("lz4-block", decode_block_stream(plain))]
    try:
        candidates.append(("lz4-frame", lz4.frame.LZ4FrameDecompressor().decompress(plain)))
    except Exception:
        pass
    scored = []
    for name, raw in candidates:
        messages, rest = split_messages(raw)
        valid = 0
        for packed in messages[:10]:
            try:
                protocol.parse_package_and_content(packed)
                valid += 1
            except Exception:
                pass
        scored.append((valid, len(messages), -rest, name, messages))
    _, _, _, name, messages = max(scored, key=lambda row: row[:3])
    return messages, name


def session_id(fields: list[dict]) -> int | None:
    return next((f["value"] for f in fields if f.get("kind") == "inline" and f.get("tag") == 1), None)


def jsonable(value):
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return {"hex": value[:64].hex(), "length": len(value)}
    if isinstance(value, dict):
        return {str(jsonable(k)): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def unpack_blob(blob: bytes):
    if blob.startswith(b"\x04\x22\x4d\x18"):
        try:
            blob = lz4.frame.decompress(blob)
        except Exception:
            return None
    try:
        return jsonable(msgpack.unpackb(blob, raw=False, strict_map_key=False))
    except Exception:
        return None


def parse_message(packed: bytes, protocol) -> dict:
    parsed = protocol.parse_package_and_content(packed)
    fields, _ = protocol.parse_sproto_object_fields(parsed["content"])
    blobs = [f["value"] for f in fields if f.get("kind") == "bytes"]
    method = None
    if blobs:
        try:
            text = blobs[0].decode("ascii")
            if 2 <= len(text) <= 100 and "." in text:
                method = text
        except UnicodeDecodeError:
            pass
    return {
        "session": session_id(parsed["package_fields"]),
        "method": method,
        "packed_length": len(packed),
        "values": [value for value in (unpack_blob(blob) for blob in blobs) if value is not None][:4],
        "blob_lengths": [len(blob) for blob in blobs],
        "sha256": hashlib.sha256(packed).hexdigest(),
    }


def decode_capture(path: Path, port: int = 8443) -> dict:
    reference = load_reference()
    protocol = reference.protocol
    flows_out = []
    rpc_out = []
    for flow, segments in reference.read_flows(path, port=port).items():
        client, cgap = reference.reassemble(segments["c2s"])
        server, sgap = reference.reassemble(segments["s2c"])
        info = {"flow": f"{flow[0]}:{flow[1]} -> {flow[2]}:{flow[3]}",
                "tcp_gaps": cgap + sgap, "client_bytes": len(client), "server_bytes": len(server)}
        try:
            ch = reference.route(client, True)
            sh = reference.route(server, False)
            if not ch or not sh or cgap or sgap:
                info["status"] = "complete_handshake_and_contiguous_stream_required"
                flows_out.append(info)
                continue
            private = sympy.discrete_log(protocol.DH_P, ch["public"], protocol.DH_G)
            secret = protocol.u64le(pow(sh["public"], private, protocol.DH_P))
            key = protocol.derive_rc4_key(secret)
            requests, request_mode = decode_transport(client[ch["consumed"]:], key, protocol)
            responses, response_mode = decode_transport(server[sh["consumed"]:], key, protocol)
            req_parsed = []
            res_parsed = []
            for packed in requests:
                try:
                    req_parsed.append(parse_message(packed, protocol))
                except Exception:
                    pass
            for packed in responses:
                try:
                    res_parsed.append(parse_message(packed, protocol))
                except Exception:
                    pass
            by_session = {r["session"]: r for r in res_parsed if r["session"] is not None}
            for request in req_parsed:
                method = request["method"] or ""
                if "Login" in method or "Auth" in method:
                    continue
                response = by_session.get(request["session"])
                if ("Summon" in method or "History" in method
                        or request["packed_length"] + 6 in (44, 45)):
                    rpc_out.append({"flow": info["flow"], "request": request, "response": response})
            info.update({"status": "decoded", "request_mode": request_mode,
                         "response_mode": response_mode, "request_messages": len(requests),
                         "response_messages": len(responses),
                         "methods": dict(Counter(r["method"] or "<unknown>" for r in req_parsed))})
        except Exception as exc:
            info.update({"status": "decode_failed", "error": str(exc)[:180]})
        flows_out.append(info)
    return {"source": str(path), "flows": flows_out, "summon_rpc_candidates": rpc_out}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", type=Path)
    ap.add_argument("--port", type=int, default=8443)
    ap.add_argument("--output", type=Path, default=ROOT / "data" / "local" / "summon_decoded.json")
    args = ap.parse_args()
    result = decode_capture(args.capture, args.port)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("DECODE_DONE", "flows", len(result["flows"]),
          "candidates", len(result["summon_rpc_candidates"]),
          "output", args.output)


if __name__ == "__main__":
    main()
