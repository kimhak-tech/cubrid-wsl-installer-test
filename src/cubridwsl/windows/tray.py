"""The CUBRID WSL Tray application, as a running process and as a file on disk.

"Is it running" needs no UI-automation library: the Tray's MUTEX exists exactly
while the process does -- the Tray creates it at startup and Windows destroys
it when the process exits, so its existence IS the answer.
"""
from __future__ import annotations

from pathlib import Path

from .. import constants
from . import registry


class ProbeError(RuntimeError):
    """The Tray could not be probed at all -- Windows refused the question.

    RAISED rather than reported as "not running", and that choice is the whole
    reason this class exists. A case asserting the Tray is GONE would pass on a
    probe that simply failed, which is a green result for a machine nobody
    looked at. A framework fault should stop the run loudly; only the PRODUCT
    gets to fail a test.

    In practice this never fires: the mutex probe treats "no such mutex" as not
    running and "access denied" as running, so only an unexpected Win32 error
    reaches here.
    """


_SYNCHRONIZE = 0x00100000                 # the least OpenMutexW can ask for
_ERROR_FILE_NOT_FOUND = 2
_ERROR_ACCESS_DENIED = 5


def is_running() -> bool:
    """Is the Tray process up? Raises ProbeError if that cannot be established.

    Always a plain True or False, so every caller reads the same way:

        assert not tray.is_running(), "the Tray is still running"
    """
    try:
        return _mutex_exists(constants.TRAY_MUTEX)
    except Exception as exc:
        raise ProbeError(
            f"could not probe the Tray mutex {constants.TRAY_MUTEX!r}: "
            f"{type(exc).__name__}: {exc}") from exc


def binary_path() -> Path | None:
    """Where the product says its Tray executable is, while it is installed.

    Read BEFORE an uninstall: the registry value that names it is one of the
    things the uninstall removes.
    """
    return registry.tray_binary_path()


def binary_exists(path: Path | None) -> bool:
    """Is the Tray binary still on disk at the path the product recorded?

    Takes the path rather than looking it up, so it still answers after the
    registry key that named it has gone -- which is exactly when it is asked.
    """
    return bool(path) and Path(path).is_file()


def _mutex_exists(name: str) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # A HANDLE is POINTER-sized. ctypes defaults every function's restype to
    # c_int, which truncates one on 64-bit Windows -- and the truncated value
    # is then what CloseHandle is handed, so the real handle stays open for the
    # life of the run.
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    kernel32.OpenMutexW.argtypes = (wintypes.DWORD, wintypes.BOOL,
                                    wintypes.LPCWSTR)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenMutexW(_SYNCHRONIZE, False, name)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    error = ctypes.get_last_error()
    if error == _ERROR_FILE_NOT_FOUND:
        return False
    # A mutex you are not allowed to open is a mutex that EXISTS.
    if error == _ERROR_ACCESS_DENIED:
        return True
    raise OSError(error, f"OpenMutexW({name!r}) failed with error {error}")


def stop() -> bool:
    """Kill a running Tray. True if one was killed.

    Needed before a CLEANUP uninstall: by the second run of any suite there is
    a Tray holding the install directory open, and a process with a handle on
    that directory is the likeliest reason for a leftover folder -- which would
    then read as a product defect rather than as our own doing.

    NEVER call this before an uninstall that is under test. Whether the product
    copes with its own running Tray is exactly what LCM-001 asks.
    """
    import subprocess

    try:
        result = subprocess.run(["taskkill", "/F", "/IM", constants.TRAY_EXE],
                                capture_output=True, timeout=30)
    except Exception:
        return False
    # taskkill returns 128 when no such process is running, which is the normal
    # case and not a failure.
    return result.returncode == 0
