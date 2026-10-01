"""Read-only scan for the game's current auth blob; reports hashes/lengths only.

Uses PROCESS_VM_READ and PROCESS_QUERY_INFORMATION. Never modifies the game.
"""

import base64
import ctypes
import hashlib
import json
import re
import time
from ctypes import wintypes

import psutil

import morimens_direct_facade_pilot as pilot


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p),
                ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD),
                ("PartitionId", wintypes.WORD),
                ("RegionSize", ctypes.c_size_t),
                ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD),
                ("Type", wintypes.DWORD)]


K = ctypes.WinDLL("kernel32", use_last_error=True)
K.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
K.OpenProcess.restype = wintypes.HANDLE
K.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                             ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t]
K.VirtualQueryEx.restype = ctypes.c_size_t
K.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
K.ReadProcessMemory.restype = wintypes.BOOL
K.CloseHandle.argtypes = [wintypes.HANDLE]


def captured_hash():
    first, _ = pilot.captured_login_messages(pilot.CAPTURE)
    _, blobs = pilot.packet_fields(first)
    value = json.loads(base64.b64decode(blobs[0], validate=True))
    return hashlib.sha256(value["token"].encode()).hexdigest()


def inspect_candidate(blob):
    # Only decode to validate the format; never return the credential.
    try:
        value = json.loads(base64.b64decode(blob, validate=True))
        token = value.get("token", "")
        if isinstance(token, str) and len(token) >= 100 and "acc" in value:
            return len(token), hashlib.sha256(token.encode()).hexdigest()
    except Exception:
        pass
    return None


def find_current_auth_blob(expected_old_hash=None, max_seconds=30):
    """Return a base64 auth blob in memory, preferring one newer than capture.

    The caller must keep it private. This function never writes process memory.
    """
    targets = [p for p in psutil.process_iter(["pid", "name"])
               if (p.info["name"] or "").lower() == "morimens.exe"]
    if len(targets) != 1:
        raise RuntimeError(f"expected one Morimens process, found {len(targets)}")
    handle = K.OpenProcess(0x0410, False, targets[0].pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    ascii_sig = b"eyJ0b2tlbiI6"
    signatures = (ascii_sig, ascii_sig.decode().encode("utf-16le"))
    fallback = None
    start = time.monotonic()
    try:
        address = 0
        mbi = MEMORY_BASIC_INFORMATION()
        while K.VirtualQueryEx(handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            base = mbi.BaseAddress or 0
            size = mbi.RegionSize
            next_address = base + size
            if next_address <= address:
                break
            address = next_address
            if mbi.State != 0x1000 or mbi.Protect & (0x01 | 0x100):
                continue
            if not mbi.Protect & (0x02 | 0x04 | 0x08 | 0x20 | 0x40 | 0x80):
                continue
            for offset in range(0, size, 1024 * 1024):
                length = min(1024 * 1024, size - offset)
                buf = ctypes.create_string_buffer(length)
                got = ctypes.c_size_t()
                if not K.ReadProcessMemory(handle, ctypes.c_void_p(base + offset), buf,
                                           length, ctypes.byref(got)) or not got.value:
                    continue
                data = buf.raw[:got.value]
                for sig in signatures:
                    found = data.find(sig)
                    if found < 0:
                        continue
                    window = data[found:found + (6000 if sig != ascii_sig else 3000)]
                    if sig != ascii_sig:
                        window = window.decode("utf-16le", "ignore").encode("ascii", "ignore")
                    match = re.match(rb"[A-Za-z0-9+/]{100,3000}={0,2}", window)
                    if match:
                        blob = match.group(0)
                        detail = inspect_candidate(blob)
                        if detail:
                            if expected_old_hash is None or detail[1] != expected_old_hash:
                                return targets[0].pid, blob, detail
                            fallback = (targets[0].pid, blob, detail)
                if time.monotonic() - start > max_seconds:
                    return fallback
            if time.monotonic() - start > max_seconds:
                return fallback
    finally:
        K.CloseHandle(handle)
    return fallback


def main():
    targets = [p for p in psutil.process_iter(["pid", "name"])
               if (p.info["name"] or "").lower() == "morimens.exe"]
    if len(targets) != 1:
        raise RuntimeError(f"expected one Morimens process, found {len(targets)}")
    pid = targets[0].pid
    handle = K.OpenProcess(0x0410, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    old_hash = captured_hash()
    ascii_sig = b"eyJ0b2tlbiI6"
    signatures = (ascii_sig, ascii_sig.decode().encode("utf-16le"))
    seen = set()
    scanned = 0
    regions = 0
    start = time.monotonic()
    try:
        address = 0
        mbi = MEMORY_BASIC_INFORMATION()
        while K.VirtualQueryEx(handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            base = mbi.BaseAddress or 0
            size = mbi.RegionSize
            next_address = base + size
            if next_address <= address:
                break
            address = next_address
            if mbi.State != 0x1000 or mbi.Protect & (0x01 | 0x100):
                continue
            if not mbi.Protect & (0x02 | 0x04 | 0x08 | 0x20 | 0x40 | 0x80):
                continue
            regions += 1
            for offset in range(0, size, 1024 * 1024):
                length = min(1024 * 1024, size - offset)
                buf = ctypes.create_string_buffer(length)
                got = ctypes.c_size_t()
                if not K.ReadProcessMemory(handle, ctypes.c_void_p(base + offset), buf,
                                           length, ctypes.byref(got)) or not got.value:
                    continue
                data = buf.raw[:got.value]
                scanned += len(data)
                for sig in signatures:
                    pos = 0
                    while True:
                        found = data.find(sig, pos)
                        if found < 0:
                            break
                        pos = found + 1
                        window = data[found:found + (6000 if sig != ascii_sig else 3000)]
                        if sig != ascii_sig:
                            window = window.decode("utf-16le", "ignore").encode("ascii", "ignore")
                        match = re.match(rb"[A-Za-z0-9+/]{100,3000}={0,2}", window)
                        if match:
                            detail = inspect_candidate(match.group(0))
                            if detail:
                                seen.add(detail)
                if time.monotonic() - start > 90:
                    break
            if time.monotonic() - start > 90:
                break
    finally:
        K.CloseHandle(handle)
    print("PROCESS_VM_READ_ONLY", "pid", pid, "regions", regions,
          "scanned_mb", round(scanned / 1048576), "seconds", round(time.monotonic() - start, 1))
    print("candidate_count", len(seen), "lengths", sorted({x[0] for x in seen}),
          "different_from_old", sum(h != old_hash for _, h in seen))


if __name__ == "__main__":
    main()
