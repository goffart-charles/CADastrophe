"""Ensure automated native windows are isolated from the user's input desktop."""
import ctypes
from ctypes import wintypes
import os
import sys

import pytest

from cadastrophe.test_desktop import WORKER_ENV


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native process boundary")
def test_desktop_without_console_preserves_log_and_child_exit_code(tmp_path, monkeypatch):
    from cadastrophe.test_desktop import run_on_test_desktop
    script = tmp_path/"child.py"
    script.write_text("print('native child output', flush=True)\nraise SystemExit(17)\n", encoding="utf8")
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", None)
        code = run_on_test_desktop(script, [], timeout=30)
    assert code == 17
    log, = (tmp_path/"artifacts").glob("test-desktop-*.log")
    assert "native child output" in log.read_text(encoding="utf8")


@pytest.mark.skipif(sys.platform != "win32" or not os.environ.get(WORKER_ENV),
                    reason="Private desktop check applies to the Windows test launcher")
def test_native_windows_run_on_nonvisible_desktop():
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentThreadId.restype = wintypes.DWORD
    user.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user.GetThreadDesktop.restype = wintypes.HANDLE
    user.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user.OpenInputDesktop.restype = wintypes.HANDLE
    user.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    user.GetUserObjectInformationW.restype = wintypes.BOOL
    user.CloseDesktop.argtypes = [wintypes.HANDLE]
    user.CloseDesktop.restype = wintypes.BOOL

    def name(handle):
        buffer = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        assert user.GetUserObjectInformationW(handle, 2, buffer, ctypes.sizeof(buffer), ctypes.byref(needed))
        return buffer.value

    current = user.GetThreadDesktop(kernel.GetCurrentThreadId())
    visible = user.OpenInputDesktop(0, False, 1)
    assert current and visible
    try:
        assert name(current) == os.environ[WORKER_ENV]
        assert name(current) != name(visible)
    finally:
        user.CloseDesktop(visible)
