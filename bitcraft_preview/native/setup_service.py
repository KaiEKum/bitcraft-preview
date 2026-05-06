from __future__ import annotations

import ctypes
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone

from .local_user_manager import LocalUserManager
from .state_manager import NativeModeStateManager, ReconcileSummary
from .steam_locator import find_bitcraft_install, get_primary_steam_path


class NativeSetupError(RuntimeError):
    pass


@dataclass
class CleanupSummary:
    users_deleted: int = 0
    users_failed: int = 0
    folders_deleted: int = 0
    folders_failed: int = 0


@dataclass
class RepairSummary:
    users_repaired: int = 0
    users_failed: int = 0


def is_admin() -> bool:
    """Check if the current process has administrator privileges."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def setup_disclaimer_text() -> str:
    return (
        "Native Mode setup will create or manage local Windows user accounts and per-instance Steam folders.\n"
        "Administrator rights are REQUIRED for this operation.\n"
        "BitCraft Preview includes a built-in cleanup/revert operation to remove app-managed users and folders."
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class NativeSetupService:
    """Provision/reconcile native instances and cleanup app-managed artifacts."""

    def __init__(
        self,
        state: NativeModeStateManager | None = None,
        user_manager: LocalUserManager | None = None,
    ) -> None:
        self._state = state or NativeModeStateManager()
        self._users = user_manager or LocalUserManager()

    @staticmethod
    def _instance_id(index: int) -> str:
        return f"steam{index}"

    @staticmethod
    def _local_username(index: int) -> str:
        return f"bitcraft{index}"

    @staticmethod
    def _instance_root(base_root: str, index: int) -> str:
        return os.path.join(base_root, f"Steam{index}")

    @staticmethod
    def _same_path(left: str, right: str) -> bool:
        return os.path.normcase(os.path.normpath(left)) == os.path.normcase(os.path.normpath(right))

    def _ensure_steamapps_link(self, link_path: str, target_path: str) -> str:
        os.makedirs(os.path.dirname(link_path), exist_ok=True)

        if os.path.lexists(link_path):
            entry_stat = os.lstat(link_path)
            is_reparse = bool(
                getattr(entry_stat, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            )

            if os.path.islink(link_path) or is_reparse:
                current_target = os.path.normpath(os.path.realpath(link_path))
                if self._same_path(current_target, target_path):
                    return "reused"
                if os.path.isdir(link_path):
                    os.rmdir(link_path)
                else:
                    os.unlink(link_path)
                action = "repaired"
            elif os.path.isdir(link_path):
                if os.listdir(link_path):
                    raise NativeSetupError(
                        f"Cannot replace non-empty existing directory at steamapps link path: {link_path}"
                    )
                os.rmdir(link_path)
                action = "repaired"
            else:
                os.remove(link_path)
                action = "repaired"
        else:
            action = "created"

        try:
            os.symlink(target_path, link_path, target_is_directory=True)
            return action
        except OSError:
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", link_path, target_path],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                stderr = (result.stderr or "").strip()
                stdout = (result.stdout or "").strip()
                detail = stderr or stdout or f"exit code {result.returncode}"
                raise NativeSetupError(f"Failed to create steamapps junction {link_path} -> {target_path}: {detail}")
            return action

    def reconcile(self, max_instances: int | None = None) -> ReconcileSummary:
        if not is_admin():
            raise NativeSetupError(
                "Native Mode setup requires Administrator privileges. "
                "Please run BitCraftPreview.exe as Administrator."
            )

        cfg = self._state.load_config()
        native_cfg = cfg["native_mode"]

        configured_count = int(max_instances if max_instances is not None else native_cfg.get("max_instances", 8))
        if configured_count <= 0:
            raise NativeSetupError("max_instances must be a positive integer")

        base_root = os.path.normpath(str(native_cfg.get("steam_instance_root", r"C:\BitcraftPreview\SteamInstances")))
        os.makedirs(base_root, exist_ok=True)

        steam_root = get_primary_steam_path()
        install = find_bitcraft_install(steam_root)
        steam_exe_source = os.path.join(steam_root, "steam.exe")
        if not os.path.isfile(steam_exe_source):
            raise NativeSetupError(f"steam.exe not found at expected path: {steam_exe_source}")

        steamapps_target = os.path.join(install.library_path, "steamapps")
        if not os.path.isdir(steamapps_target):
            raise NativeSetupError(f"Steam library steamapps directory missing: {steamapps_target}")

        summary = ReconcileSummary(run_at=_utc_now_iso())

        for idx in range(1, configured_count + 1):
            instance_id = self._instance_id(idx)
            username = self._local_username(idx)
            instance_root = self._instance_root(base_root, idx)
            steam_exe_path = os.path.join(instance_root, "steam.exe")
            steamapps_link_path = os.path.join(instance_root, "steamapps")

            existing = self._state.get_instance(instance_id)
            if existing is None:
                existing = self._state.get_instance_by_username(username)

            known_password = ""
            if existing is not None:
                try:
                    known_password = self._state.get_plain_password(existing.instance_id)
                except Exception:
                    known_password = ""

            created, password = self._users.ensure_user(username, known_password or None)
            if created:
                summary.users_created += 1
            else:
                summary.users_reused += 1

            final_password = password or known_password
            if not final_password:
                raise NativeSetupError(
                    f"No managed password available for existing user '{username}'. "
                    "Reset password manually and rerun setup."
                )
            self._users.harden_user(username)

            if os.path.isdir(instance_root):
                summary.folders_reused += 1
            else:
                os.makedirs(instance_root, exist_ok=True)
                summary.folders_created += 1

            # Ensure steam.exe is present in the instance folder.
            if not os.path.isfile(steam_exe_path):
                shutil.copy2(steam_exe_source, steam_exe_path)

            link_action = self._ensure_steamapps_link(steamapps_link_path, steamapps_target)
            if link_action == "created":
                summary.folders_created += 1
            elif link_action == "repaired":
                summary.folders_repaired += 1
            else:
                summary.folders_reused += 1

            sid = self._users.get_user_sid(username)
            self._state.upsert_instance(
                instance_id=instance_id,
                local_username=username,
                plain_password=final_password,
                local_user_sid=sid,
                instance_root=instance_root,
                steam_exe_path=steam_exe_path,
                steamapps_link_path=steamapps_link_path,
                steamapps_link_target=steamapps_target,
                status="ready",
            )

        cfg = self._state.load_config()
        cfg["native_mode"]["enabled"] = True
        cfg["native_mode"]["setup_completed"] = True
        cfg["native_mode"]["max_instances"] = configured_count
        cfg["mode"] = "native"
        self._state.save_config(cfg)
        self._state.set_last_reconcile(summary)
        return summary

    def repair(self) -> RepairSummary:
        if not is_admin():
            raise NativeSetupError(
                "Native Mode repair requires Administrator privileges. "
                "Please run BitCraftPreview.exe as Administrator."
            )

        instances = [instance for instance in self._state.list_instances() if instance.managed_by_app and instance.local_username]
        if not instances:
            raise NativeSetupError("No managed native accounts configured.")

        summary = RepairSummary()
        failures: list[str] = []
        for instance in instances:
            password = LocalUserManager.generate_password()
            try:
                self._users.reset_password(instance.local_username, password)
                sid = self._users.get_user_sid(instance.local_username)
                self._state.upsert_instance(
                    instance_id=instance.instance_id,
                    local_username=instance.local_username,
                    plain_password=password,
                    local_user_sid=sid,
                    status="ready",
                )
                self._users.harden_user(instance.local_username)
                summary.users_repaired += 1
            except Exception as e:
                summary.users_failed += 1
                failures.append(f"{instance.instance_id} ({instance.local_username}): {e}")

        if failures:
            raise NativeSetupError("Native repair failed for some accounts:\n" + "\n".join(failures))

        return summary

    def cleanup(self) -> CleanupSummary:
        if not is_admin():
            raise NativeSetupError(
                "Native Mode cleanup requires Administrator privileges. "
                "Please run BitCraftPreview.exe as Administrator."
            )

        cfg = self._state.load_config()
        native_cfg = cfg.get("native_mode", {})
        base_root = os.path.normpath(str(native_cfg.get("steam_instance_root", r"C:\BitcraftPreview\SteamInstances")))

        # Kill all Steam processes for managed users before cleanup to avoid locked files.
        summary = CleanupSummary()
        for instance in self._state.list_instances():
            if instance.managed_by_app and instance.local_username:
                try:
                    # Best-effort process kill; use subprocess instead of process_launcher to avoid circular import.
                    password = self._state.get_plain_password(instance.instance_id)
                    subprocess.run(
                        ["taskkill", "/F", "/FI", f"USERNAME eq {instance.local_username}"],
                        check=False,
                        capture_output=True,
                        timeout=10,
                    )
                except Exception:
                    pass  # Best-effort; continue with cleanup even if taskkill fails.

        # Now delete users and folders.
        for instance in self._state.list_instances():
            if instance.managed_by_app and instance.local_username:
                try:
                    self._users.delete_user(instance.local_username)
                    summary.users_deleted += 1
                except Exception:
                    # User may already be deleted; count as success if user doesn't exist.
                    if not self._users.user_exists(instance.local_username):
                        summary.users_deleted += 1
                    else:
                        summary.users_failed += 1

            root = (instance.instance_root or "").strip()
            if root and os.path.isdir(root):
                normalized_root = os.path.normpath(root)
                safe_root = False
                try:
                    safe_root = self._same_path(os.path.commonpath([base_root, normalized_root]), base_root)
                except ValueError:
                    safe_root = False

                if safe_root and re.search(r"[\\/]Steam\d+$", normalized_root, re.IGNORECASE):
                    try:
                        shutil.rmtree(normalized_root)
                        summary.folders_deleted += 1
                    except Exception:
                        summary.folders_failed += 1

        native_cfg["instances"] = []
        native_cfg["enabled"] = False
        native_cfg["setup_completed"] = False
        cfg["mode"] = "sandboxie"
        self._state.save_config(cfg)
        return summary
