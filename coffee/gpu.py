"""Device-memory readings: NVML through ctypes, nvidia-smi as the fallback, nothing otherwise.

On the RTX Spark both report the GPU carve-out (62.5 GiB of 128) and track model loads exactly;
the CUDA driver's pool figure does not move and is not used (RESEARCH.md §1).
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class GpuReading:
    total: int
    used: int
    name: str
    source: str


class GpuBackend(Protocol):
    name: str

    def read(self) -> GpuReading | None: ...


class _NvmlMemory(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


class NvmlBackend:
    name = "nvml"

    def __init__(self, lib: ctypes.CDLL) -> None:
        self._lib = lib
        self._lock = threading.Lock()
        handle = ctypes.c_void_p()
        if lib.nvmlInit_v2() != 0 or lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(handle)) != 0:
            raise OSError("NVML has no device 0")
        self._handle = handle
        buf = ctypes.create_string_buffer(96)
        self._device = buf.value.decode() if lib.nvmlDeviceGetName(handle, buf, 96) == 0 else "NVIDIA GPU"

    @classmethod
    def open(cls) -> "NvmlBackend | None":
        for lib_name in ("nvml.dll", "libnvidia-ml.so.1"):
            try:
                return cls(ctypes.CDLL(lib_name))
            except OSError:
                continue
            except AttributeError:  # a library without the v2 entry points
                return None
        return None

    def read(self) -> GpuReading | None:
        mem = _NvmlMemory()
        with self._lock:
            rc = self._lib.nvmlDeviceGetMemoryInfo(self._handle, ctypes.byref(mem))
        if rc != 0 or not mem.total:
            return None
        return GpuReading(total=mem.total, used=mem.used, name=self._device, source=self.name)


def parse_smi(stdout: str) -> GpuReading | None:
    """The first row with numeric memory; the Spark lists its NPU as a second, all-[N/A] device."""
    for line in stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            total, used = int(float(parts[2])), int(float(parts[3]))
        except ValueError:
            continue
        if total > 0:
            return GpuReading(total=total << 20, used=used << 20, name=parts[1], source="nvidia-smi")
    return None


class NvidiaSmiBackend:
    name = "nvidia-smi"
    QUERY = ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used", "--format=csv,noheader,nounits"]

    def read(self) -> GpuReading | None:
        try:
            done = subprocess.run(self.QUERY, capture_output=True, text=True, timeout=5, shell=False,
                                  creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired):
            return None
        return parse_smi(done.stdout) if done.returncode == 0 else None


class NullBackend:
    name = "none"

    def read(self) -> GpuReading | None:
        return None


def open_backend() -> GpuBackend:
    nvml = NvmlBackend.open()
    if nvml is not None and nvml.read() is not None:
        return nvml
    smi = NvidiaSmiBackend()
    if smi.read() is not None:
        return smi
    logger.info("coffee-vram: no NVIDIA telemetry on %s; using Ollama's own figures", sys.platform)
    return NullBackend()
