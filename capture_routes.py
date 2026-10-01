"""Find game Sconn flows in a capture without assuming a TCP port."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from scapy.all import TCP, PcapReader


def read_game_flows(path: Path, reference) -> dict:
    pairs = defaultdict(lambda: [{}, {}])
    with PcapReader(str(path)) as reader:
        for packet in reader:
            ip = reference.ip_packet(packet)
            if ip is None:
                continue
            tcp = ip[TCP]
            payload = bytes(tcp.payload)
            if not payload:
                continue
            source = (ip.src, tcp.sport)
            dest = (ip.dst, tcp.dport)
            pair = tuple(sorted((source, dest)))
            direction = 0 if source == pair[0] else 1
            pairs[pair][direction].setdefault(tcp.seq, payload)

    flows = {}
    for pair, directions in pairs.items():
        for direction in (0, 1):
            client, gaps = reference.reassemble(directions[direction])
            if gaps or reference.route(client, True) is None:
                continue
            source, dest = pair[direction], pair[1 - direction]
            flows[(source[0], source[1], dest[0], dest[1])] = {
                "c2s": directions[direction], "s2c": directions[1 - direction]}
            break
    return flows


def transport_summary(path: Path, reference) -> dict:
    """Count Sconn and TLS starts without exposing payload or account fields."""
    pairs = defaultdict(lambda: [{}, {}])
    with PcapReader(str(path)) as reader:
        for packet in reader:
            ip = reference.ip_packet(packet)
            if ip is None:
                continue
            tcp = ip[TCP]
            payload = bytes(tcp.payload)
            if not payload:
                continue
            source = (ip.src, tcp.sport)
            dest = (ip.dst, tcp.dport)
            pair = tuple(sorted((source, dest)))
            direction = 0 if source == pair[0] else 1
            pairs[pair][direction].setdefault(tcp.seq, payload)
    counts = {"sconn": 0, "tls": 0, "other": 0}
    ports = set()
    for pair, directions in pairs.items():
        starts = [reference.reassemble(segments)[0][:8] for segments in directions]
        if any(reference.route(reference.reassemble(segments)[0], True)
               for segments in directions):
            counts["sconn"] += 1
        elif any(start[:2] in (b"\x16\x03", b"\x17\x03") for start in starts):
            counts["tls"] += 1
        else:
            counts["other"] += 1
        ports.update(endpoint[1] for endpoint in pair)
    return {"flows": counts, "ports": sorted(ports)}
