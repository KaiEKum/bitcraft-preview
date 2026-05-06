import unittest
from unittest.mock import patch

from bitcraft_preview.native.process_control import NativeProcessControlError, NativeProcessController
from bitcraft_preview.native.process_launcher import ProcessLaunchError
from bitcraft_preview.native.state_manager import NativeInstance


class _FakeState:
    def __init__(self) -> None:
        self.instance = NativeInstance(
            instance_id="steam1",
            local_username="bitcraft1",
            steam_exe_path=r"C:\BitcraftPreview\SteamInstances\Steam1\steam.exe",
            status="ready",
        )

    def get_instance(self, instance_id: str):
        return self.instance if instance_id.lower() == "steam1" else None

    def get_instance_by_username(self, username: str):
        return self.instance if username.lower() == "bitcraft1" else None

    def get_plain_password(self, instance_id: str):
        return "pw"

    def list_instances(self):
        return [self.instance]


class _Proc:
    def __init__(self, name: str, username: str):
        self.info = {"name": name, "username": username}


class _KillProc:
    def __init__(self, name: str, username: str) -> None:
        self.info = {"name": name, "username": username, "pid": 1234}
        self._running = True

    def kill(self) -> None:
        self._running = False

    def is_running(self) -> bool:
        return self._running


class _KillAllState:
    def __init__(self) -> None:
        self.instances = [
            NativeInstance(
                instance_id="steam1",
                local_username="bitcraft1",
                steam_exe_path=r"C:\BitcraftPreview\SteamInstances\Steam1\steam.exe",
                status="ready",
            ),
            NativeInstance(
                instance_id="steam2",
                local_username="bitcraft2",
                steam_exe_path=r"C:\BitcraftPreview\SteamInstances\Steam2\steam.exe",
                status="ready",
            ),
        ]

    def list_instances(self):
        return self.instances


class _FakeLauncher:
    def __init__(self) -> None:
        self.calls = []

    def launch_silent(self, **kwargs):
        self.calls.append(("silent", kwargs))
        return 1111

    def launch_foreground(self, **kwargs):
        self.calls.append(("foreground", kwargs))
        return 2222


class _FailingLauncher:
    def launch_silent(self, **kwargs):
        raise ProcessLaunchError("[WinError 1327] Account restrictions are preventing this user from signing in", 1327)

    def launch_foreground(self, **kwargs):
        raise AssertionError("not used")


class NativeProcessControlTests(unittest.TestCase):
    def test_is_instance_running_true_for_matching_user_and_process_name(self) -> None:
        controller = NativeProcessController(state=_FakeState())
        procs = [
            _Proc("explorer.exe", "MACHINE\\user"),
            _Proc("BitCraft.exe", "MACHINE\\bitcraft1"),
        ]

        with patch("bitcraft_preview.native.process_control.psutil.process_iter", return_value=procs):
            self.assertTrue(controller.is_instance_running("steam1"))

    def test_is_instance_running_false_for_other_user(self) -> None:
        controller = NativeProcessController(state=_FakeState())
        procs = [
            _Proc("steam.exe", "MACHINE\\bitcraft2"),
        ]

        with patch("bitcraft_preview.native.process_control.psutil.process_iter", return_value=procs):
            self.assertFalse(controller.is_instance_running("steam1"))

    def test_launch_instance_uses_silent_applaunch_flow(self) -> None:
        launcher = _FakeLauncher()
        controller = NativeProcessController(state=_FakeState(), launcher=launcher)

        with patch.object(controller, "force_kill_instance_processes") as kill_mock, patch(
            "bitcraft_preview.native.process_control.time.sleep"
        ):
            result = controller.launch_instance("steam1")

        self.assertEqual(result.steam_pid, 1111)
        kill_mock.assert_not_called()
        self.assertEqual(len(launcher.calls), 1)
        mode, kwargs = launcher.calls[0]
        self.assertEqual(mode, "silent")
        self.assertEqual(kwargs["username"], "bitcraft1")
        self.assertIn("-silent", kwargs["args"])
        self.assertIn("-applaunch 3454650", kwargs["args"])
        self.assertNotIn("-userchooser", kwargs["args"])

    def test_launch_instance_explains_account_restriction(self) -> None:
        controller = NativeProcessController(state=_FakeState(), launcher=_FailingLauncher())

        with self.assertRaises(NativeProcessControlError) as raised:
            controller.launch_instance("steam1")

        message = str(raised.exception)
        self.assertIn("Native Repair", message)
        self.assertIn("bitcraft1", message)

    def test_open_user_chooser_uses_foreground_userchooser_flow(self) -> None:
        launcher = _FakeLauncher()
        controller = NativeProcessController(state=_FakeState(), launcher=launcher)

        with patch.object(controller, "force_kill_instance_processes") as kill_mock, patch(
            "bitcraft_preview.native.process_control.time.sleep"
        ):
            result = controller.open_user_chooser("steam1")

        self.assertEqual(result.steam_pid, 2222)
        kill_mock.assert_called_once_with("steam1", timeout=10.0)
        self.assertEqual(len(launcher.calls), 1)
        mode, kwargs = launcher.calls[0]
        self.assertEqual(mode, "foreground")
        self.assertEqual(kwargs["username"], "bitcraft1")
        self.assertIn("-userchooser", kwargs["args"])
        self.assertNotIn("-applaunch", kwargs["args"])

    def test_relogin_alias_uses_userchooser_flow(self) -> None:
        launcher = _FakeLauncher()
        controller = NativeProcessController(state=_FakeState(), launcher=launcher)

        with patch.object(controller, "force_kill_instance_processes"), patch(
            "bitcraft_preview.native.process_control.time.sleep"
        ):
            result = controller.relogin_instance("steam1")

        self.assertEqual(result.steam_pid, 2222)
        self.assertEqual(len(launcher.calls), 1)
        mode, kwargs = launcher.calls[0]
        self.assertEqual(mode, "foreground")
        self.assertIn("-userchooser", kwargs["args"])

    def test_kill_all_instances_applies_delay_between_kills(self) -> None:
        controller = NativeProcessController(state=_KillAllState())
        procs = [
            _KillProc("steam.exe", "MACHINE\\bitcraft1"),
            _KillProc("BitCraft.exe", "MACHINE\\bitcraft2"),
            _KillProc("steam.exe", "MACHINE\\bitcraft3"),
        ]

        with patch("bitcraft_preview.native.process_control.psutil.process_iter", return_value=procs), patch(
            "bitcraft_preview.native.process_control.time.sleep"
        ) as sleep_mock:
            killed = controller.kill_all_instances(timeout=10.0, kill_interval=0.1)

        self.assertEqual(killed, 2)
        for proc in procs[:2]:
            self.assertFalse(proc.is_running())
        self.assertTrue(procs[2].is_running())
        sleep_mock.assert_any_call(0.1)

    def test_kill_all_instances_skips_delay_when_interval_zero(self) -> None:
        controller = NativeProcessController(state=_KillAllState())
        procs = [
            _KillProc("steam.exe", "MACHINE\\bitcraft1"),
            _KillProc("BitCraft.exe", "MACHINE\\bitcraft2"),
        ]

        with patch("bitcraft_preview.native.process_control.psutil.process_iter", return_value=procs), patch(
            "bitcraft_preview.native.process_control.time.sleep"
        ) as sleep_mock:
            killed = controller.kill_all_instances(timeout=10.0, kill_interval=0.0)

        self.assertEqual(killed, 2)
        sleep_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
