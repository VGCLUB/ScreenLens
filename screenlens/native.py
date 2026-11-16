"""Small, typed Win32 helpers; all screen coordinates here are physical pixels."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


def enable_dpi_awareness():
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()


def exclude_from_capture(hwnd: int) -> bool:
    if os.environ.get("SCREENLENS_QA") == "1":
        return False
    # Windows 10 2004+: prevent the translation window being OCR'd recursively.
    fn = ctypes.windll.user32.SetWindowDisplayAffinity
    fn.argtypes = [wintypes.HWND, wintypes.DWORD]
    return bool(fn(hwnd, 0x11))


def place_native_window(hwnd: int, left: int, top: int, width: int, height: int) -> bool:
    """Position a frameless window in physical pixels without taking focus."""
    fn = ctypes.windll.user32.SetWindowPos
    fn.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                   ctypes.c_int, ctypes.c_int, wintypes.UINT]
    return bool(fn(hwnd, wintypes.HWND(-1), left, top, width, height, 0x0010 | 0x0200))


def monitor_rect(name: str, geometry=None, dpr=1.0):
    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                    ("work", wintypes.RECT), ("flags", wintypes.DWORD),
                    ("device", wintypes.WCHAR * 32)]
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                     ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    user32 = ctypes.windll.user32
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MonitorInfo)]
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, callback_type, wintypes.LPARAM]

    def callback(handle, dc, rect, param):
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            r = info.monitor
            found.append((info.device, (r.left, r.top, r.right - r.left, r.bottom - r.top)))
        return True

    user32.EnumDisplayMonitors(None, None, callback_type(callback), 0)
    for device, bounds in found:
        if device == name:
            return bounds
    # Qt 6.12 can return the monitor's friendly name instead of DISPLAY1.
    # On Windows, Qt keeps native screen origins and scales the screen sizes.
    if geometry is not None:
        candidates = [bounds for _, bounds in found if bounds[:2] == (geometry.x(), geometry.y())]
        if len(candidates) == 1:
            return candidates[0]
        candidates = [bounds for _, bounds in found
                      if abs(bounds[2] - geometry.width() * dpr) <= 2
                      and abs(bounds[3] - geometry.height() * dpr) <= 2]
        if len(candidates) == 1:
            return candidates[0]
    return found[0][1] if len(found) == 1 else None
