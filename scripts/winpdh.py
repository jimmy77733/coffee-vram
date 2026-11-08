"""Windows GPU memory as Task Manager counts it: dedicated plus shared, per adapter (PDH, ctypes).

NVML on unified-memory Windows machines reports only the dedicated carve-out; a model bigger than
the carve-out spills into shared memory, which only these counters see.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes

_PDH_FMT_LARGE = 0x00000400
_PDH_MORE_DATA = 0x800007D2
_COUNTERS = {
    "dedicated": r"\GPU Adapter Memory(*)\Dedicated Usage",
    "shared": r"\GPU Adapter Memory(*)\Shared Usage",
}


class _Value(ctypes.Union):
    _fields_ = [("longValue", ctypes.c_long), ("doubleValue", ctypes.c_double),
                ("largeValue", ctypes.c_longlong), ("wide", ctypes.c_wchar_p)]


class _FmtValue(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("value", _Value)]


class _Item(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _FmtValue)]


class AdapterMemory:
    """One PDH query kept open; ``read()`` returns {adapter instance: (dedicated, shared)} bytes."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("PDH is Windows-only")
        self._pdh = ctypes.WinDLL("pdh.dll")
        self._query = wintypes.HANDLE()
        if self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)) != 0:
            raise OSError("PdhOpenQueryW failed")
        self._counters: dict[str, wintypes.HANDLE] = {}
        for key, path in _COUNTERS.items():
            handle = wintypes.HANDLE()
            if self._pdh.PdhAddEnglishCounterW(self._query, path, 0, ctypes.byref(handle)) != 0:
                raise OSError(f"PDH counter unavailable: {path}")
            self._counters[key] = handle
        self._lock = threading.Lock()

    def _array(self, handle: wintypes.HANDLE) -> dict[str, int]:
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        rc = self._pdh.PdhGetFormattedCounterArrayW(handle, _PDH_FMT_LARGE, ctypes.byref(size),
                                                    ctypes.byref(count), None)
        if rc & 0xFFFFFFFF != _PDH_MORE_DATA or not size.value:
            return {}
        buf = ctypes.create_string_buffer(size.value)
        rc = self._pdh.PdhGetFormattedCounterArrayW(handle, _PDH_FMT_LARGE, ctypes.byref(size),
                                                    ctypes.byref(count), buf)
        if rc != 0:
            return {}
        items = ctypes.cast(buf, ctypes.POINTER(_Item))
        return {items[i].szName: int(items[i].FmtValue.value.largeValue) for i in range(count.value)
                if items[i].FmtValue.CStatus in (0, 1)}

    def read(self) -> dict[str, tuple[int, int]]:
        with self._lock:
            if self._pdh.PdhCollectQueryData(self._query) != 0:
                return {}
            dedicated = self._array(self._counters["dedicated"])
            shared = self._array(self._counters["shared"])
        return {name: (dedicated.get(name, 0), shared.get(name, 0)) for name in {*dedicated, *shared}}

    def busiest(self) -> tuple[int, int] | None:
        """(dedicated, shared) of the adapter using the most memory: the GPU, not the NPU."""
        adapters = self.read()
        return max(adapters.values(), key=sum) if adapters else None

    def close(self) -> None:
        self._pdh.PdhCloseQuery(self._query)
