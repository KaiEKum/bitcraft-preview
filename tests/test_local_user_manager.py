import subprocess
import unittest
from unittest.mock import patch

from bitcraft_preview.native.local_user_manager import LocalUserError, LocalUserManager


def _cp(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class LocalUserManagerTests(unittest.TestCase):
    def test_user_exists_true(self) -> None:
        mgr = LocalUserManager()
        with patch("bitcraft_preview.native.local_user_manager._run_command", return_value=_cp(0)):
            self.assertTrue(mgr.user_exists("bitcraft1"))

    def test_user_exists_false(self) -> None:
        mgr = LocalUserManager()
        with patch("bitcraft_preview.native.local_user_manager._run_command", return_value=_cp(2)):
            self.assertFalse(mgr.user_exists("missing"))

    def test_run_command_replaces_undecodable_output(self) -> None:
        with patch("bitcraft_preview.native.local_user_manager.subprocess.run", return_value=_cp(0)) as run_mock:
            from bitcraft_preview.native.local_user_manager import _run_command

            _run_command(["net", "user", "bitcraft1"])

        self.assertEqual(run_mock.call_args.kwargs["errors"], "replace")

    def test_create_user_raises_if_exists(self) -> None:
        mgr = LocalUserManager()
        with patch.object(mgr, "user_exists", return_value=True):
            with self.assertRaises(LocalUserError):
                mgr.create_user("bitcraft1", "pw")

    def test_create_user_success_returns_password(self) -> None:
        mgr = LocalUserManager()
        with patch.object(mgr, "user_exists", return_value=False), patch(
            "bitcraft_preview.native.local_user_manager._run_command",
            side_effect=[_cp(0), _cp(0)],
        ), patch.object(mgr, "harden_user") as harden_mock:
            result = mgr.create_user("bitcraft1", "pw")
        self.assertEqual(result, "pw")
        harden_mock.assert_called_once_with("bitcraft1")

    def test_create_user_failure_raises(self) -> None:
        mgr = LocalUserManager()
        with patch.object(mgr, "user_exists", return_value=False), patch(
            "bitcraft_preview.native.local_user_manager._run_command",
            return_value=_cp(1, stderr="access denied"),
        ), patch("bitcraft_preview.native.local_user_manager._create_user_with_powershell", return_value=_cp(1, stderr="ps failed")):
            with self.assertRaises(LocalUserError):
                mgr.create_user("bitcraft1", "pw")

    def test_create_user_fallback_powershell_success(self) -> None:
        mgr = LocalUserManager()
        with patch.object(mgr, "user_exists", return_value=False), patch(
            "bitcraft_preview.native.local_user_manager._run_command",
            return_value=_cp(1, stderr="No valid response was provided."),
        ), patch("bitcraft_preview.native.local_user_manager._create_user_with_powershell", return_value=_cp(0)), patch.object(
            mgr, "harden_user"
        ) as harden_mock:
            result = mgr.create_user("bitcraft1", "pw")
        self.assertEqual(result, "pw")
        harden_mock.assert_called_once_with("bitcraft1")

    def test_repair_user_resets_password_and_hardens_account(self) -> None:
        mgr = LocalUserManager()
        with patch.object(mgr, "user_exists", return_value=True), patch(
            "bitcraft_preview.native.local_user_manager._run_command",
            return_value=_cp(0),
        ) as run_mock, patch.object(mgr, "harden_user") as harden_mock:
            mgr.repair_user("bitcraft1", "newpw")

        run_mock.assert_called_once_with(["net", "user", "bitcraft1", "newpw"])
        harden_mock.assert_called_once_with("bitcraft1")

    def test_harden_user_sets_non_expiring_enabled_account(self) -> None:
        mgr = LocalUserManager()
        with patch.object(mgr, "user_exists", return_value=True), patch(
            "bitcraft_preview.native.local_user_manager._run_command",
            return_value=_cp(0),
        ) as run_mock, patch("bitcraft_preview.native.local_user_manager._harden_user_with_powershell", return_value=_cp(0)):
            mgr.harden_user("bitcraft1")

        calls = [call.args[0] for call in run_mock.call_args_list]
        self.assertIn(["net", "user", "bitcraft1", "/active:yes"], calls)
        self.assertIn(["net", "user", "bitcraft1", "/expires:never"], calls)
        self.assertIn(["net", "user", "bitcraft1", "/times:all"], calls)

    def test_powershell_harden_only_sets_local_user_flags(self) -> None:
        from bitcraft_preview.native.local_user_manager import _harden_user_with_powershell

        with patch("bitcraft_preview.native.local_user_manager._run_command", return_value=_cp(0)) as run_mock:
            _harden_user_with_powershell("bitcraft1")

        command = run_mock.call_args.args[0][-1]
        self.assertIn("Set-LocalUser", command)
        self.assertIn("-PasswordNeverExpires $true", command)
        self.assertNotIn("Add-LocalGroupMember", command)

    def test_get_user_sid(self) -> None:
        mgr = LocalUserManager()
        with patch.object(mgr, "user_exists", return_value=True), patch(
            "bitcraft_preview.native.local_user_manager._lookup_account_sid", return_value="S-1-5-21-123"
        ):
            sid = mgr.get_user_sid("bitcraft1")
        self.assertEqual(sid, "S-1-5-21-123")

    def test_ensure_user_created(self) -> None:
        mgr = LocalUserManager()
        with patch.object(mgr, "user_exists", return_value=False), patch.object(
            mgr, "create_user", return_value="newpw"
        ):
            created, password = mgr.ensure_user("bitcraft5")
        self.assertTrue(created)
        self.assertEqual(password, "newpw")

    def test_generate_password_length(self) -> None:
        password = LocalUserManager.generate_password(30)
        self.assertEqual(len(password), 30)


if __name__ == "__main__":
    unittest.main()
