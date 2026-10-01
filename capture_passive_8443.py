"""Capture TCP/8443 traffic outside the game process with WinDivert SNIFF.

Start before launching the game so the route/DH handshake is present. The raw
pcap may include encrypted authentication traffic; keep it in data/local.
Requires an elevated shell because WinDivert installs a packet filter driver.
"""

from __future__ import annotations

import argparse
import ctypes
import struct
import time
from pathlib import Path

import pydivert


ROOT = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, default=ROOT / "data" / "local" / "session.pcap")
    ap.add_argument("--ready-file", type=Path, default=ROOT / "data" / "local" / "capture.ready")
    ap.add_argument("--seconds", type=int, default=600)
    ap.add_argument("--port", type=int, default=8443)
    args = ap.parse_args()
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise SystemExit("被动抓包需要管理员权限。")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with args.output.open("wb") as output:
        output.write(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 101))
        with pydivert.WinDivert(
            f"tcp and (tcp.SrcPort == {args.port} or tcp.DstPort == {args.port})",
            layer=pydivert.Layer.NETWORK,
            flags=pydivert.Flag.SNIFF,
        ) as handle:
            args.ready_file.write_text("READY\n", encoding="ascii")
            print("PASSIVE_CAPTURE_READY", flush=True)
            end = time.monotonic() + args.seconds
            while time.monotonic() < end:
                try:
                    packet = handle.recv()
                except KeyboardInterrupt:
                    break
                now = time.time()
                sec = int(now)
                usec = int((now - sec) * 1_000_000)
                raw = packet.raw
                output.write(struct.pack("<IIII", sec, usec, len(raw), len(raw)))
                output.write(raw)
                output.flush()
                count += 1
    print("CAPTURE_DONE", count, flush=True)


if __name__ == "__main__":
    main()
