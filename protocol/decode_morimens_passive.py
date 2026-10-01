"""Offline DH/RC4 decoder for complete Morimens TCP/8443 captures.

Reads only a local pcap/pcapng. Writes leaderboard rows and diagnostics; never
writes decrypted login payloads, tickets, shared secrets, or RC4 keys.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

import lz4.frame
import msgpack
import sympy
from scapy.all import IP, TCP, PcapReader


ROOT = Path(__file__).resolve().parent
V7 = ROOT / "morimens_direct_top1000.py"
spec = importlib.util.spec_from_file_location("morimens_v7_protocol", V7)
protocol = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = protocol
spec.loader.exec_module(protocol)


def ip_packet(packet):
    if IP in packet and TCP in packet:
        return packet[IP]
    # Older WinDivert pcapng files carry raw IPv4 with an Ethernet linktype.
    try:
        candidate = IP(bytes(packet))
        if TCP in candidate:
            return candidate
    except Exception:
        pass
    return None


def read_flows(path: Path, port: int = 8443):
    flows = defaultdict(lambda: {"c2s": {}, "s2c": {}})
    with PcapReader(str(path)) as reader:
        for packet in reader:
            ip = ip_packet(packet)
            if ip is None:
                continue
            tcp = ip[TCP]
            payload = bytes(tcp.payload)
            if not payload:
                continue
            if tcp.dport == port:
                key = (ip.src, tcp.sport, ip.dst, tcp.dport)
                direction = "c2s"
            elif tcp.sport == port:
                key = (ip.dst, tcp.dport, ip.src, tcp.sport)
                direction = "s2c"
            else:
                continue
            flows[key][direction].setdefault(tcp.seq, payload)
    return flows


def reassemble(segments):
    if not segments:
        return b"", 0
    out = bytearray()
    next_seq = None
    gaps = 0
    for seq, payload in sorted(segments.items()):
        if next_seq is None:
            next_seq = seq
        if seq > next_seq:
            gaps += 1
            break
        offset = max(0, next_seq - seq)
        if offset < len(payload):
            out.extend(payload[offset:])
            next_seq = seq + len(payload)
    return bytes(out), gaps


def route(stream: bytes, client: bool):
    if len(stream) < 2:
        return None
    size = int.from_bytes(stream[:2], "big")
    if not 8 <= size <= 512 or len(stream) < size + 2:
        return None
    lines = stream[2:2 + size].splitlines()
    if client:
        if len(lines) < 5 or lines[0] != b"0" or lines[-1] != b">lz4":
            return None
        public = base64.b64decode(lines[1], validate=True)
        target = lines[2].decode("ascii", "replace")
    else:
        if len(lines) < 3 or lines[-1] != b">lz4":
            return None
        public = base64.b64decode(lines[1], validate=True)
        target = None
    if len(public) != 8:
        return None
    return {"public": int.from_bytes(public, "little"), "consumed": size + 2,
            "target": target}


def request_ranges(plain: bytes):
    results = []
    pos = 0
    while pos + 2 <= len(plain):
        size = int.from_bytes(plain[pos:pos + 2], "big")
        if size <= 0 or pos + 2 + size > len(plain):
            break
        packed = plain[pos + 2:pos + 2 + size]
        pos += 2 + size
        try:
            parsed = protocol.parse_package_and_content(packed)
            fields, _ = protocol.parse_sproto_object_fields(parsed["content"])
            blobs = [field["value"] for field in fields if field["kind"] == "bytes"]
            if len(blobs) < 2 or blobs[0] != b"Rank.QueryRank":
                continue
            args = msgpack.unpackb(blobs[1], raw=False, strict_map_key=False)
            if isinstance(args, list) and len(args) == 4 and args[0] == "AbyssChallenge":
                results.append({"start": args[1], "end": args[2],
                                "activityTid": args[3]})
        except Exception:
            continue
    return results


def decoded_client_stream(cipher: bytes, key: bytes):
    plain = protocol.RC4(key).crypt(cipher)
    decoder = lz4.frame.LZ4FrameDecompressor()
    try:
        # Incomplete terminal frame is acceptable: all complete blocks still decode.
        return decoder.decompress(plain)
    except Exception:
        return b""


def rank_rows_from_server(cipher: bytes, key: bytes):
    plain = protocol.RC4(key).crypt(cipher)
    offset = 0
    messages = 0
    rows = []
    errors = 0
    while offset + 2 <= len(plain):
        size = int.from_bytes(plain[offset:offset + 2], "big")
        if size <= 0 or size > 65535 or offset + 2 + size > len(plain):
            break
        packed = plain[offset + 2:offset + 2 + size]
        offset += 2 + size
        messages += 1
        try:
            parsed = protocol.parse_package_and_content(packed)
            candidates = []
            # Generic response: an inner Sproto object carries a binary field.
            # Its leaderboard payload is a complete LZ4 frame of MessagePack.
            fields, _ = protocol.parse_sproto_object_fields(parsed["content"])
            for field in fields:
                if field.get("kind") != "bytes":
                    continue
                blob = field["value"]
                if blob.startswith(b"\x04\x22\x4d\x18"):
                    try:
                        decoded = lz4.frame.decompress(blob)
                        candidates.append(msgpack.unpackb(decoded, raw=False,
                                                          strict_map_key=False))
                    except Exception:
                        pass
                else:
                    try:
                        candidates.append(msgpack.unpackb(blob, raw=False,
                                                          strict_map_key=False))
                    except Exception:
                        pass
            score, raw_rows, _ = protocol.choose_rank_rows(candidates)
            if score >= 8:
                for row in raw_rows:
                    rank = row.get("rank")
                    uid = row.get("uid")
                    if isinstance(rank, int) and isinstance(uid, int):
                        rows.append(protocol.coerce_rank_row(row))
        except Exception:
            errors += 1
    return rows, {"appMessages": messages, "parseErrors": errors,
                  "unconsumedBytes": len(plain) - offset}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    flows = read_flows(args.capture)
    records = {}
    flow_report = []
    for flow, segments in flows.items():
        c2s, cgap = reassemble(segments["c2s"])
        s2c, sgap = reassemble(segments["s2c"])
        item = {"flow": f"{flow[0]}:{flow[1]} -> {flow[2]}:{flow[3]}",
                "clientBytes": len(c2s), "serverBytes": len(s2c),
                "tcpGaps": cgap + sgap}
        try:
            ch = route(c2s, True)
            sh = route(s2c, False)
            if not ch or not sh or cgap or sgap:
                item["status"] = "complete_handshake_and_contiguous_stream_required"
                flow_report.append(item)
                continue
            private = sympy.discrete_log(protocol.DH_P, ch["public"], protocol.DH_G)
            if pow(protocol.DH_G, private, protocol.DH_P) != ch["public"]:
                raise ValueError("DH verification failed")
            secret = protocol.u64le(pow(sh["public"], private, protocol.DH_P))
            key = protocol.derive_rc4_key(secret)
            cplain = decoded_client_stream(c2s[ch["consumed"]:], key)
            ranges = request_ranges(cplain)
            rows, summary = rank_rows_from_server(s2c[sh["consumed"]:], key)
            item.update({"status": "decoded", "target": ch["target"],
                         "rankRequests": ranges, "rankRows": len(rows), **summary})
            for row in rows:
                rank = row["rank"]
                if rank in records and records[rank]["uid"] != row["uid"]:
                    item.setdefault("rankConflicts", []).append(rank)
                records[rank] = row
        except Exception as exc:
            item.update({"status": "decode_failed", "error": str(exc)[:180]})
        flow_report.append(item)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = {"source": str(args.capture), "activityTid": 83315,
              "recordCount": len(records), "completeTop1000":
              all(rank in records for rank in range(1, 1001)),
              "rows": [records[k] for k in sorted(records) if k >= 1],
              "flows": flow_report}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("DECODE_DONE", "rows", len(records), "flows", len(flows),
          "completeTop1000", result["completeTop1000"])


if __name__ == "__main__":
    main()
