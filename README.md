# BitCraft Preview

<img src="preview.gif" alt="Preview" width="50%" loop="infinite" />

BitCraft Preview is a Windows-only utility that shows live DWM previews of multiple BitCraft clients and lets you switch between them from lightweight overlay tiles.

The project supports two modes:

- Native Mode: run multiple BitCraft instances without Sandboxie using BitCraft Preview's native solution. It manages accounts cleanly and provides better stability and comfort than the Sandboxie workflow.
- Sandboxie Mode: for users who do not want to use the native solution and only want the live preview overlay for existing Sandboxie clients.

## Overview

BitCraft Preview uses the Windows Desktop Window Manager thumbnail API instead of full screen capture, which keeps previews fast and relatively low overhead.

Native Mode is the intended day-to-day setup now. Each configured instance maps to its own local Windows user such as `bitcraft1` and its own Steam launcher copy under `C:\BitcraftPreview\SteamInstances`. The tray then becomes the control surface for launching and managing those instances.

Sandboxie Mode still works if you already run BitCraft through Sandboxie Plus, but it is now the secondary path and mainly meant for preview-only usage.


## Features

- Multiboxing without Sandboxie through the app's native account and launch management.
- **New: Graphical user interface** manage your accounts and settings directly inside the App
- **New: Update notifier** receive an in app notification once an update releases
- Live DWM preview overlays for BitCraft clients.
- Click-to-focus switching.
- Optional hotkey-based switching between BitCraft clients.
- Optional hiding of the active client's overlay.
- Native per-instance labels via `overlay_nickname`.

## Modes

### Native Mode

Native Mode is the primary supported workflow.

It provides:

- Multiboxing without Sandboxie.
- App-managed local Windows users.
- Per-instance Steam launcher roots.
- One-click setup from the system tray.
- Easy cleanup and removal from the system tray.
- Overlay labels that map to native instance config rather than window-title parsing.

### Sandboxie Mode

Sandboxie Mode remains available for users who already run BitCraft inside Sandboxie Plus.

It provides:

- Discovery of BitCraft windows from Sandboxie sessions.
- Sandbox-name labels parsed from window titles.
- Overlay previews for those windows.

It does not provision accounts, manage Steam roots, or act as the primary launch path.

## Installation

1. Go to the [Releases](../../releases) page.
2. Download the latest packaged build.
3. Extract it.
4. Run `BitCraftPreview.exe`.

## Quick Start

### Recommended Setup: Native Mode

Native setup requires **Administrator privileges** because it provisions and manages local Windows user accounts, normal usage does not require those priviliges.


1. Launch BitCraft Preview.

2. Open the tray icon and run native setup.

3. Use the `Native Accounts` menu for daily usage.

4. For each configured instance, the first tray action adapts automatically:

- `Launch` when no `steam.exe` or `BitCraft.exe` is running for that native user.
- `Restart` when the instance is already running.

5. If needed, use `Open Account Chooser` from the tray for a specific native instance.

### Optional Setup: Sandboxie Mode

If you are still running clients under Sandboxie Plus, just launch BitCraft Preview and let it discover the windows. In that mode, it serves as a preview overlay rather than a managed launcher.

## Tray Menu

The tray icon is the main control surface. (Till GUI is fully done)

- `Native Accounts`: per-instance launch or restart, account chooser, kill, entity ID, and overlay label management.
- `Launch All (Not Running)`: launches all configured native instances that are not already running.
- `Kill All Instances`: force-kills Steam and BitCraft across all configured native users.
- `Native Setup...` and `Native Cleanup...`: setup or remove app-managed native artifacts. (Requires running as admin)

To exit, right-click the tray icon and choose `Quit`.

## Configuration

BitCraft Preview creates `config.json` automatically.

Packaged builds store config here:

- `%LOCALAPPDATA%\BitCraftPreview\config.json`

Development runs store config here:

- repository root `config.json`

This keeps native configuration, encrypted windows passwords, and user settings persistent across rebuilds and updates.

Example:

```json
{
      "mode": "native",
      "UserSettings": {
            "inline_label": true,
            "preview_opacity": 0.8,
            "hover_zoom_enabled": true,
            "hover_zoom_percent": 200,
            "hide_active_window_overlay": false,
            "switch_window_enabled": true,
            "switch_window_hotkey": "MOUSE5",
            "preview_tile_width": 300,
            "preview_tile_height": 200
      },
      "native_mode": {
            "enabled": true,
            "setup_completed": true,
            "max_instances": 3,
            "steam_instance_root": "C:\\BitcraftPreview\\SteamInstances",
            "instances": []
      },
      "sandboxie_mode": {
            "enabled": true,
            "instances": []
      }
}
```

### Native Labels

Native instances display either `overlay_nickname` or `instance_id`.

Example:

```json
{
   "native_mode": {
      "instances": [
         {
            "instance_id": "steam1",
            "local_username": "bitcraft1",
            "overlay_nickname": "Main"
         },
         {
            "instance_id": "steam2",
            "local_username": "bitcraft2",
            "overlay_nickname": "Alt"
         }
      ]
   }
}
```

These labels can also be changed directly from the tray menu.

## Troubleshooting

- No windows found: verify that the BitCraft process is still named `BitCraft.exe`.
- Blank or black thumbnails: DWM thumbnails can fail in some fullscreen or overlay-heavy configurations. Borderless or windowed mode is usually more reliable.
- Native restart still leaves stale profile folders: check `%LOCALAPPDATA%\BitCraftPreview\bitcraft_preview.log` for profile-unload wait warnings.
- Native launch fails with `[WinError 1327] Account restrictions are preventing this user from signing in`: run `Native Repair...` from the tray tools menu as Administrator. Repair resets app-managed `bitcraft1`, `bitcraft2`, ... passwords, marks them as non-expiring, and updates encrypted config. This usually means the local Windows password policy expired the managed account passwords.
- Tray shows `Restart` instead of `Launch`: BitCraft Preview detected `steam.exe` or `BitCraft.exe` already running for that native account.
- Migrating from older builds: if you previously had a `config.json` next to the executable, move it to `%LOCALAPPDATA%\BitCraftPreview\config.json`.

## Uninstall

If you want to remove the native setup cleanly, use the tray's native cleanup action before deleting the app files.

After cleanup, also check `C:\Users` for leftover native user profile folders such as `bitcraft1`, `bitcraft2`, or suffixed folders like `bitcraft1.PCNAME`. Windows sometimes keeps those behind even after account cleanup, so they may need to be deleted manually.

## Local Development

1. Clone the repository:

```powershell
git clone https://github.com/osnium/bitcraft-preview.git
cd bitcraft-preview
```

2. Create a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
```

3. Install in editable mode:

```powershell
pip install -e .
```

## Packaging

PyInstaller is used for packaged Windows builds.

Example:

```powershell
pyinstaller.exe .\BitCraftPreview.spec --noconfirm
```

The packaged output is written to `dist\BitCraftPreview\`.

## Versioning

App release version uses `pyproject.toml` as the single source of truth.

- Bump version only in `pyproject.toml` under `[project].version`.
- Do not add or edit a separate hardcoded app `__version__` constant.

## Credits

- Icons created by XATE Media ([xate.eu](https://xate.eu))

## License

See `LICENSE`.
