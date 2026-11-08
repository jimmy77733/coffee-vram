"""System RAM, CPU, network throughput and GPU temperature — optional panel rows, no Python deps."""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_PDH_FMT_DOUBLE = 0x00000200
_PDH_MORE_DATA = 0x800007D2


@dataclass(frozen=True)
class HostStats:
    ram_total: int = 0
    ram_used: int = 0
    cpu_pct: float | None = None
    net_bps: float | None = None
    gpu_temp_c: int | None = None
    gpu_util_pct: float | None = None  # NVML utilization.gpu (compute), not memory

    @property
    def ram_pct(self) -> float:
        return self.ram_used / self.ram_total * 100 if self.ram_total else 0.0


class _NvmlUtilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


class _FileTime(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


def _filetime_int(ft: _FileTime) -> int:
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


def read_ram() -> tuple[int, int]:
    """(total, used) bytes of Windows-visible system RAM — not the GPU carve-out."""
    if sys.platform != "win32":
        try:
            mem = open("/proc/meminfo", encoding="utf-8").read()
        except OSError:
            return 0, 0
        values = {}
        for line in mem.splitlines():
            key, _, rest = line.partition(":")
            values[key] = int(rest.strip().split()[0]) * 1024
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", values.get("MemFree", 0))
        return total, max(0, total - available)
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return 0, 0
    return int(status.ullTotalPhys), int(status.ullTotalPhys - status.ullAvailPhys)


class CpuMeter:
    """Idle/busy deltas from GetSystemTimes (Windows) or /proc/stat."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._prev: tuple[int, int] | None = None  # (idle, total)

    def _sample(self) -> tuple[int, int] | None:
        if sys.platform == "win32":
            idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
            if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel),
                                                         ctypes.byref(user)):
                return None
            idle_t = _filetime_int(idle)
            total = _filetime_int(kernel) + _filetime_int(user)
            return idle_t, total
        try:
            line = open("/proc/stat", encoding="utf-8").readline()
        except OSError:
            return None
        parts = [int(p) for p in line.split()[1:]]
        if len(parts) < 4:
            return None
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        return idle, sum(parts)

    def read(self) -> float | None:
        sample = self._sample()
        if sample is None:
            return None
        with self._lock:
            prev, self._prev = self._prev, sample
        if prev is None:
            return None
        idle = sample[0] - prev[0]
        total = sample[1] - prev[1]
        if total <= 0:
            return None
        return max(0.0, min(100.0, (1 - idle / total) * 100))


class _PdhValue(ctypes.Union):
    _fields_ = [("doubleValue", ctypes.c_double), ("largeValue", ctypes.c_longlong)]


class _PdhFmt(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("value", _PdhValue)]


class _PdhItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _PdhFmt)]


class NetMeter:
    """Sum of ``Bytes Total/sec`` across interfaces (Windows PDH); None elsewhere."""

    def __init__(self) -> None:
        self._ok = False
        if sys.platform != "win32":
            return
        try:
            self._pdh = ctypes.WinDLL("pdh.dll")
            self._query = wintypes.HANDLE()
            if self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)) != 0:
                return
            self._counter = wintypes.HANDLE()
            if self._pdh.PdhAddEnglishCounterW(
                    self._query, r"\Network Interface(*)\Bytes Total/sec", 0,
                    ctypes.byref(self._counter)) != 0:
                return
            self._lock = threading.Lock()
            self._ok = True
            self._pdh.PdhCollectQueryData(self._query)  # prime rate counters
        except OSError:
            logger.debug("coffee-vram: network counters unavailable", exc_info=True)

    def read(self) -> float | None:
        if not self._ok:
            return None
        with self._lock:
            if self._pdh.PdhCollectQueryData(self._query) != 0:
                return None
            size, count = wintypes.DWORD(0), wintypes.DWORD(0)
            rc = self._pdh.PdhGetFormattedCounterArrayW(
                self._counter, _PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), None)
            if rc & 0xFFFFFFFF != _PDH_MORE_DATA or not size.value:
                return None
            buf = ctypes.create_string_buffer(size.value)
            if self._pdh.PdhGetFormattedCounterArrayW(
                    self._counter, _PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), buf) != 0:
                return None
            items = ctypes.cast(buf, ctypes.POINTER(_PdhItem))
            total = 0.0
            for i in range(count.value):
                name = items[i].szName or ""
                if items[i].FmtValue.CStatus in (0, 1) and "isatap" not in name.lower():
                    total += float(items[i].FmtValue.value.doubleValue)
            return total


def _smi_query(field: str) -> float | None:
    if not sys.platform.startswith(("win", "linux")):
        return None
    import subprocess
    from .gpu import NO_WINDOW
    try:
        done = subprocess.run(
            ["nvidia-smi", f"--query-gpu={field}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, shell=False, creationflags=NO_WINDOW,
            encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    for line in done.stdout.splitlines():
        try:
            return float(line.strip().split(",")[0])
        except ValueError:
            continue
    return None


def read_gpu_temp(gpu) -> int | None:
    """Celsius from NVML when the backend exposes it; else nvidia-smi."""
    lib = getattr(gpu, "_lib", None)
    handle = getattr(gpu, "_handle", None)
    if lib is not None and handle is not None:
        temp = ctypes.c_uint()
        # 0 = NVML_TEMPERATURE_GPU
        if lib.nvmlDeviceGetTemperature(handle, 0, ctypes.byref(temp)) == 0:
            return int(temp.value)
    value = _smi_query("temperature.gpu")
    return None if value is None else int(value)


def read_gpu_util(gpu) -> float | None:
    """Compute utilization % (NVML utilization.gpu); not memory occupancy."""
    lib = getattr(gpu, "_lib", None)
    handle = getattr(gpu, "_handle", None)
    if lib is not None and handle is not None:
        rates = _NvmlUtilization()
        if lib.nvmlDeviceGetUtilizationRates(handle, ctypes.byref(rates)) == 0:
            return float(rates.gpu)
    return _smi_query("utilization.gpu")


class HostSampler:
    def __init__(self) -> None:
        self._cpu = CpuMeter()
        self._net = NetMeter()
        self._at = 0.0
        self._cache = HostStats()

    def read(self, gpu, *, fresh: bool = False) -> HostStats:
        now = time.monotonic()
        if not fresh and now - self._at < 0.8:
            return self._cache
        total, used = read_ram()
        self._cache = HostStats(
            ram_total=total, ram_used=used, cpu_pct=self._cpu.read(),
            net_bps=self._net.read(), gpu_temp_c=read_gpu_temp(gpu),
            gpu_util_pct=read_gpu_util(gpu))
        self._at = now
        return self._cache
