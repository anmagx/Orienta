"""Synchronize native Windows title bars with the user's Windows app theme."""

import ctypes
import logging
import sys
from ctypes import wintypes

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication


_PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
_DARK_TITLE_BAR_ATTRIBUTE = 20
_THEME_POLL_INTERVAL_MS = 1500


def _uses_native_windows_platform():
    app = QApplication.instance()
    return (
        sys.platform == "win32"
        and app is not None
        and app.platformName() == "windows"
    )


def _read_apps_use_light_theme():
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        _PERSONALIZE_KEY,
        0,
        winreg.KEY_READ,
    ) as key:
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
    if value not in (0, 1):
        raise ValueError(f"Unexpected AppsUseLightTheme value: {value!r}")
    return bool(value)


def _set_dwm_title_bar_dark_mode(hwnd, dark_mode):
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    set_attribute = dwmapi.DwmSetWindowAttribute
    set_attribute.argtypes = (
        wintypes.HWND,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    set_attribute.restype = ctypes.c_long
    value = ctypes.c_int(bool(dark_mode))
    result = set_attribute(
        wintypes.HWND(hwnd),
        wintypes.DWORD(_DARK_TITLE_BAR_ATTRIBUTE),
        ctypes.byref(value),
        wintypes.DWORD(ctypes.sizeof(value)),
    )
    if result != 0:
        raise OSError(result, "DwmSetWindowAttribute failed")


def _report_warning(message, warning_callback):
    if warning_callback is None:
        logging.getLogger(__name__).warning(message)
    else:
        warning_callback(message)


def apply_windows_titlebar_theme(window, is_light=None, warning_callback=None):
    """Set one native title bar to match Windows' app light/dark preference."""
    if not _uses_native_windows_platform():
        return False
    try:
        if is_light is None:
            is_light = _read_apps_use_light_theme()
        _set_dwm_title_bar_dark_mode(int(window.winId()), not is_light)
    except (AttributeError, OSError, RuntimeError, ValueError) as error:
        _report_warning(
            f"Could not synchronize the Windows title bar theme: {error}",
            warning_callback,
        )
        return False
    return True


def schedule_windows_titlebar_theme(window, warning_callback=None):
    """Apply the native title bar setting after Qt creates the window handle."""
    if _uses_native_windows_platform():
        QTimer.singleShot(
            0,
            lambda: apply_windows_titlebar_theme(
                window,
                warning_callback=warning_callback,
            ),
        )


def install_windows_titlebar_theme_sync(app, warning_callback=None):
    """Apply current OS mode to existing windows and track OS theme changes."""
    if not _uses_native_windows_platform():
        return None
    if getattr(app, "_windows_titlebar_theme_timer", None) is not None:
        return app._windows_titlebar_theme_timer

    try:
        is_light = _read_apps_use_light_theme()
    except (OSError, ValueError) as error:
        _report_warning(
            f"Could not read the Windows app theme preference: {error}",
            warning_callback,
        )
        is_light = None

    timer = QTimer(app)
    timer.setInterval(_THEME_POLL_INTERVAL_MS)
    last_error = [None]
    current_mode = [is_light]

    def refresh_title_bars():
        try:
            new_mode = _read_apps_use_light_theme()
        except (OSError, ValueError) as error:
            message = str(error)
            if message != last_error[0]:
                _report_warning(
                    f"Could not read the Windows app theme preference: {error}",
                    warning_callback,
                )
                last_error[0] = message
            return

        last_error[0] = None
        if new_mode == current_mode[0]:
            return
        current_mode[0] = new_mode
        for window in app.topLevelWidgets():
            apply_windows_titlebar_theme(
                window,
                is_light=new_mode,
                warning_callback=warning_callback,
            )

    timer.timeout.connect(refresh_title_bars)
    timer.start()
    app._windows_titlebar_theme_timer = timer

    if is_light is not None:
        for window in app.topLevelWidgets():
            apply_windows_titlebar_theme(
                window,
                is_light=is_light,
                warning_callback=warning_callback,
            )
    return timer
