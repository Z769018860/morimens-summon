"""Elevated, read-only capture of the local accelerated login connection.

The temporary pcap contains account login material. The parent updater removes
it after obtaining the handshake; this helper never decodes or prints it.
"""

from __future__ import annotations

import argparse
import ctypes
import struct
import time
from pathlib import Path

import pydivert


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--ready-file", type=Path, required=True)
    ap.add_argument("--stop-file", type=Path, required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--seconds", type=int, default=300)
    args = ap.parse_args()
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise SystemExit("被动采集需要管理员权限")
    if not 0 < args.port < 65536:
        ap.error("端口无效")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    filt = f"tcp and (tcp.SrcPort == {args.port} or tcp.DstPort == {args.port})"
    deadline = time.monotonic() + args.seconds
    with args.output.open("wb") as writer:
        writer.write(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 101))
        writer.flush()
        with pydivert.WinDivert(filt, layer=pydivert.Layer.NETWORK,
                               flags=pydivert.Flag.SNIFF) as handle:
            args.ready_file.write_text("READY\n", encoding="ascii")
            while time.monotonic() < deadline and not args.stop_file.exists():
                packet = handle.recv()
                raw = packet.raw
                now = time.time()
                seconds = int(now)
                micros = int((now - seconds) * 1_000_000)
                writer.write(struct.pack("<IIII", seconds, micros, len(raw), len(raw)))
                writer.write(raw)
                writer.flush()


if __name__ == "__main__":
    main()
