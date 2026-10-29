"""Read-only access to a running Windows process: find it by its exe path, find a module's load address, read memory.

The process is opened with PROCESS_VM_READ and PROCESS_QUERY_INFORMATION only; nothing is ever written into it.
"""
import ctypes
import ctypes.wintypes as wt
import struct

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)


def find_pid(exe):
    """The id of the process whose image is exactly this exe path (case-insensitive), or None."""
    pids = (wt.DWORD * 4096)()
    needed = wt.DWORD()
    psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(needed))
    for pid in pids[: needed.value // 4]:
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            continue
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        k32.CloseHandle(h)
        if ok and buf.value.lower() == exe.lower():
            return pid
    return None


def module_base(pid, exe):
    """The load address of the module with this path in the process (EnumProcessModulesEx), or None."""
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    mods = (ctypes.c_void_p * 2048)()
    needed = wt.DWORD()
    psapi.EnumProcessModulesEx(h, mods, ctypes.sizeof(mods), ctypes.byref(needed), 3)
    base = None
    name = ctypes.create_unicode_buffer(1024)
    for m in mods[: needed.value // ctypes.sizeof(ctypes.c_void_p)]:
        psapi.GetModuleFileNameExW(h, ctypes.c_void_p(m), name, 1024)
        if name.value.lower() == exe.lower():
            base = m
            break
    k32.CloseHandle(h)
    return base


class Mem:
    """Reads memory of one process; read() returns None for an address that cannot be read."""

    def __init__(self, pid):
        self.h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        self.buf = ctypes.create_string_buffer(0x200000)
        self.got = ctypes.c_size_t()

    def read(self, address, size):
        if address is None or address < 0x10000 or address > 0x7FFFFFFFFFFF or size > len(self.buf):
            return None
        if not k32.ReadProcessMemory(self.h, ctypes.c_void_p(address), self.buf, size, ctypes.byref(self.got)):
            return None
        return ctypes.string_at(self.buf, size)


def q(blob, off):
    return struct.unpack_from("<Q", blob, off)[0] if blob and 0 <= off and off + 8 <= len(blob) else None


def i32(blob, off):
    return struct.unpack_from("<i", blob, off)[0] if blob and 0 <= off and off + 4 <= len(blob) else None
