from __future__ import annotations

import logging
import os
import re
import time
import winreg
from dataclasses import dataclass
from typing import List

import psutil

from .process_launcher import ProcessLaunchError, ProcessLauncher
from .state_manager import NativeInstance, NativeModeStateManager

logger = logging.getLogger(__name__)

APP_ID_BITCRAFT = 3454650

ERROR_ACCOUNT_RESTRICTION = 1327


def _wait_for_profile_unloaded(sid: str, timeout: float = 15.0) -> None:
    """Block until Windows has fully unloaded the user profile hive for *sid*.

    Windows keeps ``HKEY_USERS\\<sid>`` open while a profile is loaded.
    Polling for that key to disappear guarantees that ``CreateProcessWithLogonW``
    will not race profile teardown and create a suffixed fallback folder such as
    ``bitcraft1.PCName`` or ``bitcraft1.PCName.000``.

    Falls back to a 2-second sleep when no SID is stored.
    """
    if not sid:
        logger.debug("_wait_for_profile_unloaded: no SID stored, falling back to sleep(2)")
        time.sleep(2)
        return
    logger.debug("_wait_for_profile_unloaded: waiting for profile hive SID=%s to unload", sid)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            key = winreg.OpenKey(winreg.HKEY_USERS, sid)
            winreg.CloseKey(key)
            time.sleep(0.25)  # profile still loaded, keep polling
        except OSError:
            logger.debug("_wait_for_profile_unloaded: profile hive SID=%s unloaded", sid)
            return
    logger.warning(
        "_wait_for_profile_unloaded: timed out after %.1fs waiting for SID=%s; proceeding anyway",
        timeout,
        sid,
    )


class NativeProcessControlError(RuntimeError):
    pass


@dataclass
class LaunchResult:
    steam_pid: int
    instance_id: str
    local_username: str


class NativeProcessController:
    """High-level launch/restart/account-chooser flow for Native Mode instances."""

    def __init__(self, state: NativeModeStateManager | None = None, launcher: ProcessLauncher | None = None) -> None:
        self._state = state or NativeModeStateManager()
        self._launcher = launcher or ProcessLauncher()

    def _resolve_instance(self, instance_ref: str) -> NativeInstance:
        instance = self._state.get_instance(instance_ref)
        if instance is None:
            instance = self._state.get_instance_by_username(instance_ref)
        if instance is None:
            raise NativeProcessControlError(f"Unknown native instance: {instance_ref}")
        if not instance.steam_exe_path:
            raise NativeProcessControlError(
                f"Native instance '{instance.instance_id}' has no steam_exe_path. Run setup/reconcile first."
            )
        return instance

    @staticmethod
    def _username_matches(proc_username: str | None, local_username: str) -> bool:
        if not proc_username:
            return False
        normalized = proc_username.strip().lower()
        target = local_username.strip().lower()
        if "\\" in normalized:
            normalized = normalized.split("\\", 1)[1]
        return normalized == target

    def is_instance_running(self, instance_ref: str) -> bool:
        instance = self._resolve_instance(instance_ref)
        target_user = instance.local_username.strip().lower()
        names = {"steam.exe", "bitcraft.exe"}

        for proc in psutil.process_iter(["name", "username"]):
            try:
                name = (proc.info.get("name") or "").strip().lower()
                if name not in names:
                    continue
                username = proc.info.get("username")
                if self._username_matches(username, target_user):
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        return False

    def force_kill_instance_processes(self, instance_ref: str, timeout: float = 10.0) -> None:
        """Force-kill all Steam and BitCraft processes for this instance.
        
        Args:
            instance_ref: Instance ID or local username
            timeout: Maximum seconds to wait for processes to terminate
        """
        instance = self._resolve_instance(instance_ref)
        target_user = instance.local_username.strip().lower()
        target_names = {"steam.exe", "bitcraft.exe"}
        
        # Find all matching processes
        processes_to_kill: List[psutil.Process] = []
        for proc in psutil.process_iter(["name", "username", "pid"]):
            try:
                name = (proc.info.get("name") or "").strip().lower()
                if name not in target_names:
                    continue
                username = proc.info.get("username")
                if self._username_matches(username, target_user):
                    processes_to_kill.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        
        if not processes_to_kill:
            return  # Nothing to kill
        
        # Force-kill all matching processes
        for proc in processes_to_kill:
            try:
                proc.kill()  # SIGKILL on Unix, TerminateProcess on Windows
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass  # Already dead or can't access
        
        # Wait for them to actually terminate
        start_time = time.time()
        while time.time() - start_time < timeout:
            still_alive = []
            for proc in processes_to_kill:
                try:
                    if proc.is_running():
                        still_alive.append(proc)
                except psutil.NoSuchProcess:
                    pass  # Good, it's dead
            
            if not still_alive:
                break  # All processes terminated
            
            time.sleep(0.2)  # Short polling interval

    def kill_all_instances(self, timeout: float = 10.0, kill_interval: float = 0.1) -> int:
        """Force-kill all Steam and BitCraft processes for ALL configured native instances.
        
        Args:
            timeout: Maximum seconds to wait for processes to terminate
            kill_interval: Delay in seconds between killing each process
            
        Returns:
            Number of processes killed
        """
        instances = self._state.list_instances()
        if not instances:
            return 0
        
        target_users = {inst.local_username.strip().lower() for inst in instances}
        target_names = {"steam.exe", "bitcraft.exe"}
        
        # Find all matching processes across all native instance users
        processes_to_kill: List[psutil.Process] = []
        for proc in psutil.process_iter(["name", "username", "pid"]):
            try:
                name = (proc.info.get("name") or "").strip().lower()
                if name not in target_names:
                    continue
                username = proc.info.get("username")
                if not username:
                    continue
                    
                # Check if this process belongs to any native instance
                for target_user in target_users:
                    if self._username_matches(username, target_user):
                        processes_to_kill.append(proc)
                        break
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        
        if not processes_to_kill:
            return 0
        
        # Force-kill all matching processes with slight staggering to reduce system disruption.
        for index, proc in enumerate(processes_to_kill):
            try:
                proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            if kill_interval > 0 and index < len(processes_to_kill) - 1:
                time.sleep(kill_interval)
        
        # Wait for them to actually terminate
        start_time = time.time()
        while time.time() - start_time < timeout:
            still_alive = []
            for proc in processes_to_kill:
                try:
                    if proc.is_running():
                        still_alive.append(proc)
                except psutil.NoSuchProcess:
                    pass
            
            if not still_alive:
                break
            
            time.sleep(0.2)
        
        return len(processes_to_kill)

    @staticmethod
    def _master_override_name(instance_id: str) -> str:
        match = re.search(r"(\d+)$", instance_id)
        if match:
            return f"steam{match.group(1)}"
        safe = re.sub(r"[^a-zA-Z0-9]", "", instance_id) or "steam"
        return f"steam{safe}"

    @staticmethod
    def _friendly_launch_error(instance: NativeInstance, error: ProcessLaunchError) -> NativeProcessControlError:
        if error.winerror == ERROR_ACCOUNT_RESTRICTION or "WinError 1327" in str(error):
            return NativeProcessControlError(
                f"Windows rejected native account '{instance.local_username}' for {instance.instance_id}. "
                "The password or account policy is likely expired/restricted. "
                "Run Native Repair as Administrator, then launch again."
            )
        return NativeProcessControlError(str(error))

    def _launch(
        self,
        instance: NativeInstance,
        *,
        userchooser_mode: bool,
        restart_mode: bool,
        pre_kill: bool = False,
    ) -> LaunchResult:
        password = self._state.get_plain_password(instance.instance_id)

        if restart_mode or userchooser_mode or pre_kill:
            logger.debug(
                "Pre-launch kill: instance=%s user=%s restart=%s chooser=%s",
                instance.instance_id, instance.local_username, restart_mode, userchooser_mode,
            )
            # Force-kill all Steam/BitCraft processes for this user before launching.
            # This avoids stale CEF state, IPC conflicts, and ensures clean startup.
            self.force_kill_instance_processes(instance.instance_id, timeout=10.0)
            # Wait for Windows to fully unload the user profile before relaunching.
            # Without this, CreateProcessWithLogonW races profile teardown and causes
            # Windows to create a suffixed fallback folder (e.g. bitcraft1.PCName).
            _wait_for_profile_unloaded(instance.local_user_sid, timeout=15.0)

        override = self._master_override_name(instance.instance_id)
        working_dir = os.path.dirname(instance.steam_exe_path) if instance.steam_exe_path else None

        logger.debug(
            "Launching Steam: instance=%s user=%s mode=%s",
            instance.instance_id, instance.local_username,
            "userchooser" if userchooser_mode else "silent",
        )
        if userchooser_mode:
            args = f"-master_ipc_name_override {override} -userchooser"
            try:
                steam_pid = self._launcher.launch_foreground(
                    username=instance.local_username,
                    password=password,
                    exe_path=instance.steam_exe_path,
                    args=args,
                    working_directory=working_dir,
                )
            except ProcessLaunchError as e:
                raise self._friendly_launch_error(instance, e) from e
        else:
            args = f"-master_ipc_name_override {override} -silent -applaunch {APP_ID_BITCRAFT}"
            try:
                steam_pid = self._launcher.launch_silent(
                    username=instance.local_username,
                    password=password,
                    exe_path=instance.steam_exe_path,
                    args=args,
                    working_directory=working_dir,
                )
            except ProcessLaunchError as e:
                raise self._friendly_launch_error(instance, e) from e

        logger.debug("Steam launched: instance=%s steam_pid=%d", instance.instance_id, steam_pid)
        return LaunchResult(
            steam_pid=steam_pid,
            instance_id=instance.instance_id,
            local_username=instance.local_username,
        )

    def launch_instance(self, instance_ref: str) -> LaunchResult:
        instance = self._resolve_instance(instance_ref)
        return self._launch(instance, userchooser_mode=False, restart_mode=False, pre_kill=False)

    def restart_instance(self, instance_ref: str) -> LaunchResult:
        instance = self._resolve_instance(instance_ref)
        return self._launch(instance, userchooser_mode=False, restart_mode=True)

    def launch_or_restart(self, instance_ref: str) -> tuple[LaunchResult, bool]:
        """Launch if not running, restart (kill + profile-unload wait + relaunch) if running.

        Returns ``(result, was_restart)`` so callers can log the correct verb.
        """
        instance = self._resolve_instance(instance_ref)
        running = self.is_instance_running(instance_ref)
        logger.debug(
            "launch_or_restart: instance=%s running=%s -> %s",
            instance.instance_id, running, "restart" if running else "launch",
        )
        result = self._launch(instance, userchooser_mode=False, restart_mode=running, pre_kill=False)
        return result, running

    def open_user_chooser(self, instance_ref: str) -> LaunchResult:
        instance = self._resolve_instance(instance_ref)
        return self._launch(instance, userchooser_mode=True, restart_mode=True)

    def relogin_instance(self, instance_ref: str) -> LaunchResult:
        # Backward-compatible alias for the legacy CLI/API surface.
        return self.open_user_chooser(instance_ref)
