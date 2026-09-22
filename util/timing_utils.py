"""
Windows high-resolution timer helpers.

Since Windows 10 version 2004, ``timeBeginPeriod`` only raises the system
timer resolution for the calling process, not system-wide. Each process that
relies on sub-16ms ``time.sleep()`` precision (the default Windows clock tick
is ~15.6ms) must request it for itself.
"""

import sys

_active = False


def enable_high_res_timer():
    """Request 1ms timer resolution for the current process (Windows only)."""
    global _active
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        ctypes.windll.winmm.timeBeginPeriod(1)
        _active = True
    except Exception:
        _active = False
    return _active


def disable_high_res_timer():
    """Release a previously requested 1ms timer resolution."""
    global _active
    if not _active:
        return
    try:
        import ctypes
        ctypes.windll.winmm.timeEndPeriod(1)
    except Exception:
        pass
    _active = False
