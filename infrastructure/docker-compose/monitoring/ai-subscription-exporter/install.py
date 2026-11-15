#!/usr/bin/env python3
"""Install the exporter as a per-user macOS LaunchAgent."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "com.gullinkambi.ai-subscription-exporter"
UPSTREAM = Path.home() / ".config" / "gullinkambi" / "claude-statusline-upstream.json"


def paths() -> tuple[Path, Path, Path]:
    root = Path(__file__).resolve().parent
    plist = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    logs = Path.home() / "Library" / "Logs" / "Gullinkambi"
    return root, plist, logs


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def desired_statusline(root: Path) -> dict[str, str]:
    command = (
        f"{shlex_quote(sys.executable)} "
        f"{shlex_quote(str(root / 'claude_statusline_mux.py'))}"
    )
    return {"type": "command", "command": command}


def configure_claude(root: Path) -> None:
    settings_path = Path.home() / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings: dict[str, object] = {}
    if settings_path.exists():
        with settings_path.open(encoding="utf-8") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict):
            raise ValueError(f"{settings_path} must contain a JSON object")
        settings = loaded
    desired = desired_statusline(root)
    current = settings.get("statusLine")
    if current and current != desired:
        if not isinstance(current, dict) or current.get("type") != "command":
            raise RuntimeError(f"Unsupported existing Claude statusLine: {current!r}")
        if not isinstance(current.get("command"), str):
            raise RuntimeError("Existing Claude statusLine command is not a string")
        write_json(UPSTREAM, current)
        print(f"Preserved existing Claude status line: {UPSTREAM}")
    settings["statusLine"] = desired
    write_json(settings_path, settings)
    print(f"Configured Claude status line: {settings_path}")


def shlex_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def launchctl(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *arguments], check=check, text=True, capture_output=True
    )


def install(configure_statusline: bool) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("This installer must run on macOS")
    root, plist_path, logs = paths()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    cache = Path.home() / ".cache" / "gullinkambi"
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    codex_bin = shutil.which("codex") or "codex"
    antigravity_bin = shutil.which("agy") or "agy"
    program = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, str(root / "exporter.py")],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "WorkingDirectory": str(root),
        "StandardOutPath": str(logs / "ai-subscription-exporter.log"),
        "StandardErrorPath": str(logs / "ai-subscription-exporter.error.log"),
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "CLAUDE_STATUS_FILE": str(cache / "claude-status.json"),
            "CODEX_BIN": codex_bin,
            "ANTIGRAVITY_BIN": antigravity_bin,
            "ANTIGRAVITY_TIMEOUT_SECONDS": "45",
        },
    }
    with plist_path.open("wb") as handle:
        plistlib.dump(program, handle)
    os.chmod(plist_path, 0o600)
    if configure_statusline:
        configure_claude(root)
    domain = f"gui/{os.getuid()}"
    launchctl("bootout", domain, str(plist_path), check=False)
    launchctl("bootstrap", domain, str(plist_path))
    launchctl("kickstart", "-k", f"{domain}/{LABEL}")
    print(f"Installed {LABEL}")
    print("Metrics: http://127.0.0.1:9819/metrics")


def restore_claude(root: Path) -> None:
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        return
    with settings_path.open(encoding="utf-8") as handle:
        settings = json.load(handle)
    if not isinstance(settings, dict) or settings.get("statusLine") != desired_statusline(root):
        return
    if UPSTREAM.exists():
        with UPSTREAM.open(encoding="utf-8") as handle:
            settings["statusLine"] = json.load(handle)
        UPSTREAM.unlink()
    else:
        settings.pop("statusLine", None)
    write_json(settings_path, settings)
    print(f"Restored Claude status line: {settings_path}")


def uninstall() -> None:
    root, plist_path, _ = paths()
    domain = f"gui/{os.getuid()}"
    launchctl("bootout", domain, str(plist_path), check=False)
    if plist_path.exists():
        plist_path.unlink()
    restore_claude(root)
    print(f"Removed {LABEL}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--configure-claude-statusline",
        action="store_true",
        help="configure Claude Code to capture subscription rate limits",
    )
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    if args.uninstall:
        uninstall()
    else:
        install(args.configure_claude_statusline)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
