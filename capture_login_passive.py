"""Elevated, read-only capture of the game's TCP login connection.

The temporary pcap contains account login material. The parent updater removes
it after obtaining the handshake; this helper never decodes or prints it.
"""

from __future__ import annotations

import argparse
import ctypes
import struct
import threading
import time
from pathlib import Path

import psutil
import pydivert


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--ready-file", type=Path, required=True)
    ap.add_argument("--stop-file", type=Path, required=True)
    ap.add_argument("--port", type=int, action="append", default=[],
                    help="额外指定端口；可重复。默认按游戏进程连接自动发现")
    ap.add_argument("--no-auto", action="store_true", help="只采集 --port 指定的端口")
    ap.add_argument("--seconds", type=int, default=300)
    args = ap.parse_args()
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise SystemExit("被动采集需要管理员权限")
    if any(not 0 < port < 65536 for port in args.port):
        ap.error("端口无效")
    if args.no_auto and not args.port:
        ap.error("--no-auto 需要至少一个 --port")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    ports = set(args.port)
    filt = "ip and tcp" if not args.no_auto else "ip and tcp and (" + " or ".join(
        f"tcp.SrcPort == {port} or tcp.DstPort == {port}" for port in sorted(ports)) + ")"
    active_flows: set[tuple[str, int, str, int]] = set()
    pending: dict[tuple[str, int, str, int], list[tuple[float, bytes]]] = {}
    lock = threading.Lock()
    stopped = threading.Event()

    def refresh_game_flows() -> None:
        while not stopped.is_set():
            try:
                current = set()
                for process in psutil.process_iter(["pid", "name"]):
                    if (process.info["name"] or "").lower() != "morimens.exe":
                        continue
                    for connection in process.connections(kind="tcp"):
                        if not connection.laddr or not connection.raddr:
                            continue
                        local = (str(connection.laddr.ip), connection.laddr.port)
                        remote = (str(connection.raddr.ip), connection.raddr.port)
                        current.add((*local, *remote))
                        current.add((*remote, *local))
                with lock:
                    active_flows.update(current)
            except (psutil.Error, OSError):
                pass
            stopped.wait(0.05)

    watcher = None
    if not args.no_auto:
        watcher = threading.Thread(target=refresh_game_flows, daemon=True)
        watcher.start()
    deadline = time.monotonic() + args.seconds
    try:
        with args.output.open("wb") as writer:
            writer.write(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 101))
            writer.flush()
            with pydivert.WinDivert(filt, layer=pydivert.Layer.NETWORK,
                                   flags=pydivert.Flag.SNIFF) as handle:
                args.ready_file.write_text("READY\n", encoding="ascii")
                def write_packet(at: float, raw: bytes) -> None:
                    seconds = int(at)
                    micros = int((at - seconds) * 1_000_000)
                    writer.write(struct.pack("<IIII", seconds, micros, len(raw), len(raw)))
                    writer.write(raw)
                    writer.flush()

                while time.monotonic() < deadline and not args.stop_file.exists():
                    packet = handle.recv()
                    flow = (packet.src_addr, packet.src_port,
                            packet.dst_addr, packet.dst_port)
                    reverse = (flow[2], flow[3], flow[0], flow[1])
                    now = time.time()
                    with lock:
                        game_flow = flow in active_flows
                    if game_flow or args.no_auto:
                        early = pending.pop(flow, []) + pending.pop(reverse, [])
                        for at, raw in sorted(early, key=lambda item: item[0]):
                            write_packet(at, raw)
                        write_packet(now, packet.raw)
                    elif packet.src_port in ports or packet.dst_port in ports:
                        # Hold a few first packets until the socket can be tied
                        # to Morimens; never write unrelated port traffic.
                        queue = pending.setdefault(flow, [])
                        if len(queue) < 16:
                            queue.append((now, packet.raw))
                        if len(pending) > 200:
                            pending.pop(next(iter(pending)))
    finally:
        stopped.set()
        if watcher:
            watcher.join(timeout=1)


if __name__ == "__main__":
    main()
