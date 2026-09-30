"""`ThemeListener`: follows Windows' light/dark setting while "Seguir o Windows" is chosen.

Spec 5.1 and 5.6 (ruling P11(d)). QFluentWidgets 1.11.3 has the piece for
this, `SystemThemeListener`: a `QThread` that waits for Windows' theme to
change and then, only while the theme mode is `Theme.AUTO`, re-reads it
into `qconfig` (`_onThemeChanged`) and emits `systemThemeChanged`. What
restyles the widgets is up to its owner (`MainWindow`).

On Windows, though, its `run()` is `darkdetect.listener`: a *synchronous*
`RegNotifyChangeKeyValue` loop that never returns, so the only way to stop
it is `QThread.terminate()` -- killing a thread that may be holding the
GIL or a heap lock, which can hang the whole program -- and a `QThread`
destroyed while it runs aborts the program. `ThemeListener` keeps the
library's class, signal and reaction to a change, and only replaces that
wait on Windows: the same registry notification, asked for
*asynchronously*, is waited on together with a stop event, so `stop()`
ends the thread within milliseconds. Elsewhere the library's own loop is
used as is (it checks `isInterruptionRequested()` between polls).
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from qfluentwidgets import SystemThemeListener

_PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
_LIGHT_THEME_VALUE = "AppsUseLightTheme"  # 1: light apps, 0: dark apps (what darkdetect reads too)
_REG_NOTIFY_CHANGE_LAST_SET = 0x00000004
_WAIT_OBJECT_0 = 0x00000000
_INFINITE = 0xFFFFFFFF


class _Win32:
    """The few Win32 calls the Windows wait needs (private `WinDLL`s: darkdetect's stay untouched)."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.wintypes = wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

        kernel32.CreateEventW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateEventW.restype = wintypes.HANDLE
        kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
        kernel32.SetEvent.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.WaitForMultipleObjects.argtypes = (
            wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD,
        )
        kernel32.WaitForMultipleObjects.restype = wintypes.DWORD
        advapi32.RegNotifyChangeKeyValue.argtypes = (
            wintypes.HKEY, wintypes.BOOL, wintypes.DWORD, wintypes.HANDLE, wintypes.BOOL,
        )
        advapi32.RegNotifyChangeKeyValue.restype = wintypes.LONG

        self.create_event = kernel32.CreateEventW
        self.set_event = kernel32.SetEvent
        self.close_handle = kernel32.CloseHandle
        self.wait_for_any = kernel32.WaitForMultipleObjects
        self.notify_change = advapi32.RegNotifyChangeKeyValue
        self.handles = lambda *handles: (wintypes.HANDLE * len(handles))(*handles)


_win32: _Win32 | None = None


def _api() -> _Win32:
    global _win32
    if _win32 is None:
        _win32 = _Win32()
    return _win32


def watch_windows_theme(on_change: Callable[[str], None], stop_event: int) -> None:
    """Call `on_change("Light" | "Dark")` whenever Windows' app theme changes, until `stop_event` is set.

    Runs in the listener's thread. Returns at once when the registry key
    is missing (a headless Windows has none) or a Win32 call fails.
    """
    import winreg

    api = _api()
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERSONALIZE_KEY, 0, winreg.KEY_READ)
    except OSError:
        return

    def light() -> int | None:
        try:
            return int(winreg.QueryValueEx(key, _LIGHT_THEME_VALUE)[0])
        except (OSError, TypeError, ValueError):
            return None

    changed = api.create_event(None, False, False, None)  # auto-reset: one wake-up per change
    try:
        if not changed:
            return
        handles = api.handles(changed, stop_event)
        last = light()
        while True:
            # One notification per call: ask for the next change first, then read, so that a change
            # made between two waits is not missed.
            if api.notify_change(api.wintypes.HKEY(key.handle), False, _REG_NOTIFY_CHANGE_LAST_SET,
                                 changed, True) != 0:
                return
            current = light()
            if current is not None and current != last:
                last = current
                on_change("Light" if current else "Dark")
            if api.wait_for_any(2, handles, False, _INFINITE) != _WAIT_OBJECT_0:
                return  # the stop event (or a failed wait)
    finally:
        if changed:
            api.close_handle(changed)
        key.Close()


class ThemeListener(SystemThemeListener):
    """QFluentWidgets' `SystemThemeListener`, stoppable at once on Windows (see the module docstring).

    `start()` it while the theme mode is `Theme.AUTO`; `stop()` it when the
    user picks a theme of their own and before its owner goes away. A
    stopped listener may be started again.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stop_event: int | None = None  # on Windows, a Win32 event handle from start() to stop()

    def start(self, *args) -> None:
        if sys.platform == "win32":
            if not self._stop_event:
                self._stop_event = _api().create_event(None, True, False, None)  # manual reset: stays set
            if not self._stop_event:
                return  # without its stop event the thread could never be stopped: do not start it
        super().start(*args)

    def run(self) -> None:
        if sys.platform != "win32":
            super().run()  # the library's own loop: it checks isInterruptionRequested()
            return
        watch_windows_theme(self._onThemeChanged, self._stop_event)

    def stop(self) -> None:
        """End the thread and wait for it: no `terminate()`, and nothing left running.

        The stop event is set -- even before `run()` begins, the wait then
        returns at once -- and closed only once the thread has finished.
        """
        self.requestInterruption()
        stop_event = self._stop_event
        if stop_event:
            _api().set_event(stop_event)
        self.wait()
        if stop_event:
            self._stop_event = None
            _api().close_handle(stop_event)
