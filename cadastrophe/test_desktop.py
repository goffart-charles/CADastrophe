"""Run native Qt/WGL tests on a private Windows desktop that is never activated.

Uses only stdlib/Win32. No SwitchDesktop, window hiding race or Qt platform
substitution: the child retains real native windows, input focus and OpenGL.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

WORKER_ENV = "CADASTROPHE_TEST_DESKTOP"


def _forward_output(payload):
    # A pythonw launcher has no console stream. The private desktop log still
    # retains the complete native output, even when there is nothing to relay to.
    if sys.stdout is not None:
        sys.stdout.write(payload)
        sys.stdout.flush()


def run_on_test_desktop(script: Path, arguments: list[str], timeout=300.0, *, environment=None) -> int:
    import msvcrt

    class StartupInfo(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
            ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE)]

    class ProcessInfo(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                    ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
                                    wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    user.CreateDesktopW.restype = wintypes.HANDLE
    user.CloseDesktop.argtypes = [wintypes.HANDLE]
    user.CloseDesktop.restype = wintypes.BOOL
    kernel.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p,
        wintypes.LPCWSTR, ctypes.POINTER(StartupInfo), ctypes.POINTER(ProcessInfo)]
    kernel.CreateProcessW.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL

    name = f"CADastropheTests_{os.getpid()}_{uuid4().hex}"
    # Read/write objects, create windows/menus, hook and enumerate. No right to
    # switch the desktop is requested and no desktop activation API is called.
    desktop = user.CreateDesktopW(name, None, None, 0, 0x00CF, None)
    if not desktop:
        raise ctypes.WinError(ctypes.get_last_error())
    output_dir = script.parent / "artifacts"
    output_dir.mkdir(exist_ok=True)
    log = output_dir / f"test-desktop-{os.getpid()}-{uuid4().hex[:8]}.log"
    process = ProcessInfo()
    try:
        env = dict(os.environ, **(environment or {}), **{WORKER_ENV: name, "PYTHONUNBUFFERED": "1",
                                  "PYTHONIOENCODING": "utf-8"})
        environment = ctypes.create_unicode_buffer("\0".join(
            f"{key}={value}" for key, value in sorted(env.items(), key=lambda item: item[0].casefold())) + "\0\0")
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(
            [sys.executable, str(script), *arguments]))
        with open(os.devnull, "rb") as stdin, open(log, "wb", buffering=0) as output:
            os.set_inheritable(stdin.fileno(), True)
            os.set_inheritable(output.fileno(), True)
            startup = StartupInfo()
            startup.cb = ctypes.sizeof(startup)
            startup.lpDesktop = name
            startup.dwFlags = 0x100  # STARTF_USESTDHANDLES
            startup.hStdInput = msvcrt.get_osfhandle(stdin.fileno())
            startup.hStdOutput = startup.hStdError = msvcrt.get_osfhandle(output.fileno())
            if not kernel.CreateProcessW(sys.executable, command, None, None, True,
                    0x08000400, environment, str(script.parent), ctypes.byref(startup), ctypes.byref(process)):
                raise ctypes.WinError(ctypes.get_last_error())
            print(f"Tests sur un bureau Windows non affiche. Journal : {log}", flush=True)
            deadline = time.monotonic() + timeout
            with open(log, encoding="utf-8", errors="replace") as reader:
                while True:
                    status = kernel.WaitForSingleObject(process.hProcess, 100)
                    _forward_output(reader.read())
                    if status == 0:
                        break
                    if status != 0x102:
                        raise ctypes.WinError(ctypes.get_last_error())
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Tests interrompus apres {timeout:g} s : {log}")
                _forward_output(reader.read())
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(process.hProcess, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            return ctypes.c_int32(code.value).value
    finally:
        if process.hProcess:
            if kernel.WaitForSingleObject(process.hProcess, 0) == 0x102:
                kernel.TerminateProcess(process.hProcess, 130)
                kernel.WaitForSingleObject(process.hProcess, 5000)
            kernel.CloseHandle(process.hThread)
            kernel.CloseHandle(process.hProcess)
        user.CloseDesktop(desktop)
