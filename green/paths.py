"""Locations of GREEN's configuration and data on disk.

Data lives in the per-user application data directory, never in the source tree.
Set the ``GREEN_HOME`` environment variable to use a different (portable) location.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

APP_DIR_NAME = "GREEN"


@dataclass(frozen=True)
class AppPaths:
    config_dir: Path
    data_dir: Path

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.json"

    @property
    def database_file(self) -> Path:
        return self.data_dir / "green.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def file_backup_dir(self) -> Path:
        """Copies of project files taken before an approved change is applied."""
        return self.backup_dir / "files"

    def ensure(self) -> None:
        for directory in (self.config_dir, self.data_dir, self.log_dir, self.backup_dir):
            directory.mkdir(parents=True, exist_ok=True)


def default_paths() -> AppPaths:
    override = os.environ.get("GREEN_HOME")
    if override:
        base = Path(override).expanduser()
        return AppPaths(config_dir=base / "config", data_dir=base / "data")

    home = Path.home()
    if sys.platform == "win32":
        roaming = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        return AppPaths(config_dir=roaming / APP_DIR_NAME, data_dir=local / APP_DIR_NAME)
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support" / APP_DIR_NAME
        return AppPaths(config_dir=base, data_dir=base)
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    data_home = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return AppPaths(config_dir=config_home / "green", data_dir=data_home / "green")
