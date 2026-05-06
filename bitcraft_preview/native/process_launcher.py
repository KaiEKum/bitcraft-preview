from __future__ import annotations

import ctypes
from ctypes import wintypes


class ProcessLaunchError(RuntimeError):
    def __init__(self, message: str, winerror: int | None = None) -> None:
        super().__init__(message)
        self.winerror = winerror


LOGON_WITH_PROFILE = 0x00000001
CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_NO_WINDOW = 0x08000000


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


advapi32 = ctypes.windll.advapi32
kernel32 = ctypes.windll.kernel32


class ProcessLauncher:
    """Launch processes under specific local Windows user credentials."""

    def _create_process(
        self,
        *,
        username: str,
        password: str,
        command_line: str,
        domain: str = ".",
        working_directory: str | None = None,
        create_no_window: bool = True,
    ) -> int:
        startup_info = STARTUPINFOW()
        startup_info.cb = ctypes.sizeof(STARTUPINFOW)

        process_info = PROCESS_INFORMATION()
        flags = CREATE_UNICODE_ENVIRONMENT
        if create_no_window:
            flags |= CREATE_NO_WINDOW

        cmd_buffer = ctypes.create_unicode_buffer(command_line)

        ok = advapi32.CreateProcessWithLogonW(
            ctypes.c_wchar_p(username),
            ctypes.c_wchar_p(domain),
            ctypes.c_wchar_p(password),
            LOGON_WITH_PROFILE,
            None,
            cmd_buffer,
            flags,
            None,
            ctypes.c_wchar_p(working_directory) if working_directory else None,
            ctypes.byref(startup_info),
            ctypes.byref(process_info),
        )
        if not ok:
            error = ctypes.WinError()
            raise ProcessLaunchError(str(error), getattr(error, "winerror", None))

        try:
            return int(process_info.dwProcessId)
        finally:
            if process_info.hThread:
                kernel32.CloseHandle(process_info.hThread)
            if process_info.hProcess:
                kernel32.CloseHandle(process_info.hProcess)

    def launch_silent(
        self,
        *,
        username: str,
        password: str,
        exe_path: str,
        args: str = "",
        working_directory: str | None = None,
    ) -> int:
        command_line = f'"{exe_path}" {args}'.strip()
        return self._create_process(
            username=username,
            password=password,
            command_line=command_line,
            working_directory=working_directory,
            create_no_window=True,
        )

    def launch_foreground(
        self,
        *,
        username: str,
        password: str,
        exe_path: str,
        args: str = "",
        working_directory: str | None = None,
    ) -> int:
        command_line = f'"{exe_path}" {args}'.strip()
        return self._create_process(
            username=username,
            password=password,
            command_line=command_line,
            working_directory=working_directory,
            create_no_window=False,
        )

    def taskkill_for_user(self, *, username: str, password: str) -> int:
        # Run taskkill as the target local user and only terminate Steam/BitCraft trees.
        # This avoids killing unrelated processes owned by that account.
        command_line = (
            f'taskkill.exe /F /T /FI "USERNAME eq {username}" /IM steam.exe /IM BitCraft.exe'
        )
        return self._create_process(
            username=username,
            password=password,
            command_line=command_line,
            create_no_window=True,
        )
