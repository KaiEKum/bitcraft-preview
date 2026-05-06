import os
import tempfile
import unittest
from unittest.mock import patch

from bitcraft_preview import config
from bitcraft_preview.native.setup_service import NativeSetupService, setup_disclaimer_text
from bitcraft_preview.native.state_manager import NativeModeStateManager
from bitcraft_preview.native.steam_locator import SteamInstallInfo


class _FakeUserManager:
    def __init__(self) -> None:
        self._created = set()
        self.deleted = []
        self.hardened = []
        self.repaired = []

    def ensure_user(self, username: str, password: str | None = None):
        if username in self._created:
            return False, password or ""
        self._created.add(username)
        return True, password or f"{username}-pw"

    def get_user_sid(self, username: str) -> str:
        return f"S-1-5-21-{username}"

    def harden_user(self, username: str) -> None:
        self.hardened.append(username)

    def repair_user(self, username: str, password: str) -> None:
        self.repaired.append((username, password))

    def delete_user(self, username: str) -> None:
        self.deleted.append(username)


class NativeSetupServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._old_config_path = config.config_file_path
        config.config_file_path = os.path.join(self._tmp_dir.name, "config.json")

        self._steam_root = os.path.join(self._tmp_dir.name, "SteamRoot")
        self._library = os.path.join(self._tmp_dir.name, "Library")
        self._base_root = os.path.join(self._tmp_dir.name, "SteamInstances")

        os.makedirs(self._steam_root, exist_ok=True)
        os.makedirs(os.path.join(self._library, "steamapps"), exist_ok=True)
        with open(os.path.join(self._steam_root, "steam.exe"), "wb") as f:
            f.write(b"steam")

    def tearDown(self) -> None:
        config.config_file_path = self._old_config_path
        self._tmp_dir.cleanup()

    def _state(self) -> NativeModeStateManager:
        manager = NativeModeStateManager()
        cfg = manager.load_config()
        cfg["native_mode"]["steam_instance_root"] = self._base_root
        cfg["native_mode"]["max_instances"] = 2
        manager.save_config(cfg)
        return manager

    def test_reconcile_is_idempotent_and_reuses_users(self) -> None:
        manager = self._state()
        users = _FakeUserManager()
        service = NativeSetupService(state=manager, user_manager=users)

        install = SteamInstallInfo(
            steam_root=self._steam_root,
            library_path=self._library,
            bitcraft_path=os.path.join(self._library, "steamapps", "common", "BitCraft", "BitCraft.exe"),
        )

        with patch("bitcraft_preview.native.setup_service.is_admin", return_value=True), patch(
            "bitcraft_preview.native.setup_service.get_primary_steam_path", return_value=self._steam_root
        ), patch("bitcraft_preview.native.setup_service.find_bitcraft_install", return_value=install), patch.object(
            NativeSetupService, "_ensure_steamapps_link", return_value="created"
        ), patch("bitcraft_preview.native.state_manager.protect_text", side_effect=lambda p, **_: f"ENC:{p}"), patch(
            "bitcraft_preview.native.state_manager.unprotect_text", side_effect=lambda c: c.replace("ENC:", "", 1)
        ):
            first = service.reconcile(2)

        self.assertEqual(first.users_created, 2)
        self.assertEqual(first.users_reused, 0)
        self.assertEqual(users.hardened, ["bitcraft1", "bitcraft2"])
        self.assertEqual(len(manager.list_instances()), 2)

        with patch("bitcraft_preview.native.setup_service.is_admin", return_value=True), patch(
            "bitcraft_preview.native.setup_service.get_primary_steam_path", return_value=self._steam_root
        ), patch("bitcraft_preview.native.setup_service.find_bitcraft_install", return_value=install), patch.object(
            NativeSetupService, "_ensure_steamapps_link", return_value="reused"
        ), patch("bitcraft_preview.native.state_manager.protect_text", side_effect=lambda p, **_: f"ENC:{p}"), patch(
            "bitcraft_preview.native.state_manager.unprotect_text", side_effect=lambda c: c.replace("ENC:", "", 1)
        ):
            second = service.reconcile(2)

        self.assertEqual(second.users_created, 0)
        self.assertEqual(second.users_reused, 2)
        self.assertEqual(users.hardened, ["bitcraft1", "bitcraft2", "bitcraft1", "bitcraft2"])
        self.assertEqual(len(manager.list_instances()), 2)

    def test_repair_rotates_passwords_and_updates_state(self) -> None:
        manager = self._state()
        users = _FakeUserManager()
        service = NativeSetupService(state=manager, user_manager=users)

        with patch("bitcraft_preview.native.state_manager.protect_text", side_effect=lambda p, **_: f"ENC:{p}"), patch(
            "bitcraft_preview.native.state_manager.unprotect_text", side_effect=lambda c: c.replace("ENC:", "", 1)
        ):
            manager.upsert_instance(
                instance_id="steam1",
                local_username="bitcraft1",
                plain_password="oldpw",
                instance_root=os.path.join(self._base_root, "Steam1"),
                steam_exe_path=os.path.join(self._base_root, "Steam1", "steam.exe"),
            )

            with patch("bitcraft_preview.native.setup_service.is_admin", return_value=True), patch(
                "bitcraft_preview.native.setup_service.LocalUserManager.generate_password", return_value="newpw"
            ):
                summary = service.repair()

            self.assertEqual(summary.users_repaired, 1)
            self.assertEqual(summary.users_failed, 0)
            self.assertEqual(users.repaired, [("bitcraft1", "newpw")])
            self.assertEqual(manager.get_plain_password("steam1"), "newpw")
            self.assertEqual(manager.get_instance("steam1").local_user_sid, "S-1-5-21-bitcraft1")

    def test_cleanup_resets_native_state_and_deletes_managed_folders(self) -> None:
        manager = self._state()
        users = _FakeUserManager()
        service = NativeSetupService(state=manager, user_manager=users)

        instance_root = os.path.join(self._base_root, "Steam1")
        os.makedirs(instance_root, exist_ok=True)

        with patch("bitcraft_preview.native.state_manager.protect_text", return_value="ENC"):
            manager.upsert_instance(
                instance_id="steam1",
                local_username="bitcraft1",
                plain_password="pw",
                instance_root=instance_root,
                steam_exe_path=os.path.join(instance_root, "steam.exe"),
            )

        with patch("bitcraft_preview.native.setup_service.is_admin", return_value=True):
            summary = service.cleanup()
        cfg = manager.load_config()

        self.assertEqual(summary.users_deleted, 1)
        self.assertEqual(summary.users_failed, 0)
        self.assertFalse(os.path.exists(instance_root))
        self.assertEqual(cfg["mode"], "sandboxie")
        self.assertEqual(cfg["native_mode"]["instances"], [])

    def test_disclaimer_mentions_revert_cleanup(self) -> None:
        text = setup_disclaimer_text().lower()
        self.assertIn("windows user", text)
        self.assertIn("cleanup", text)
        self.assertIn("revert", text)


if __name__ == "__main__":
    unittest.main()
