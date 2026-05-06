"""Native Mode infrastructure for BitCraft Preview."""

from .local_user_manager import LocalUserManager, LocalUserError
from .process_control import NativeProcessController, NativeProcessControlError
from .process_launcher import ProcessLauncher, ProcessLaunchError
from .setup_service import CleanupSummary, NativeSetupError, NativeSetupService, RepairSummary, is_admin, setup_disclaimer_text
from .steam_locator import SteamInstallInfo, SteamLocatorError, find_bitcraft_install, get_primary_steam_path
from .state_manager import NativeModeStateManager

__all__ = [
    "LocalUserManager",
    "LocalUserError",
    "NativeProcessController",
    "NativeProcessControlError",
    "ProcessLauncher",
    "ProcessLaunchError",
    "NativeSetupService",
    "NativeSetupError",
    "CleanupSummary",
    "RepairSummary",
    "is_admin",
    "setup_disclaimer_text",
    "SteamInstallInfo",
    "SteamLocatorError",
    "find_bitcraft_install",
    "get_primary_steam_path",
    "NativeModeStateManager",
]
