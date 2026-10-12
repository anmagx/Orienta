"""
Preferences Manager for centralized preference persistence.

Handles loading and saving user preferences to %LOCALAPPDATA%\\Orienta\\config.cfg.
Uses atomic writes to prevent corruption if process is killed during save.
"""

import configparser
import logging
import os
from typing import Any, Dict, Optional

from src.config.config import DEFAULT_THEME, PREFS_FILE_NAME
from src.util.paths import get_app_data_dir


class PreferencesManager:
    """Manages loading and saving preferences to config file."""

    def __init__(self, config_dir: Optional[str] = None):
        """
        Initialize preferences manager.

        Args:
            config_dir: Optional path to config directory. If None, uses
                       %LOCALAPPDATA%\\Orienta.
        """
        self.config_path = self._determine_config_path(config_dir)
        self._ensure_config_dir()

    def _determine_config_path(self, config_dir: Optional[str] = None) -> str:
        """Determine the full path to the config file.

        Args:
            config_dir: Optional explicit config directory path

        Returns:
            Full path to config.cfg file
        """
        if config_dir:
            return os.path.join(config_dir, PREFS_FILE_NAME)

        return os.path.join(get_app_data_dir(), PREFS_FILE_NAME)

    def _ensure_config_dir(self):
        """Ensure the config directory exists."""
        config_dir = os.path.dirname(self.config_path)
        if config_dir:
            os.makedirs(config_dir, exist_ok=True)

    def load(self) -> Dict[str, Dict[str, str]]:
        """Load preferences from config file.

        Returns:
            Section names mapped to their string key-value pairs.
        """
        if not os.path.exists(self.config_path):
            return {}

        cfg = configparser.ConfigParser(interpolation=None)
        try:
            with open(self.config_path, "r", encoding="utf-8") as preferences_file:
                cfg.read_file(preferences_file)

            result = {}
            for section_name in cfg.sections():
                result[section_name] = dict(cfg[section_name])

            return result
        except (OSError, UnicodeError, configparser.Error) as error:
            logging.exception(
                "Error loading preferences from %s: %s",
                self.config_path,
                error,
            )
            return {}

    def save(self, preferences: Dict[str, Any]) -> bool:
        """Save preferences to config file using atomic write.

        Args:
            preferences: Dictionary of key-value pairs to save. Can be nested
                        (PyQt5 format) or flat (legacy format).

        Returns:
            True if save succeeded, False otherwise
        """
        cfg = configparser.ConfigParser(interpolation=None)
        tmp_path = self.config_path + ".tmp"

        try:
            for section_name, section_data in preferences.items():
                if isinstance(section_data, dict):
                    cfg[section_name] = {
                        str(key): str(value)
                        for key, value in section_data.items()
                    }
                else:
                    if "gui" not in cfg:
                        cfg["gui"] = {}
                    cfg["gui"][str(section_name)] = str(section_data)

            with open(tmp_path, "w", encoding="utf-8") as f:
                cfg.write(f)
            os.replace(tmp_path, self.config_path)
            return True

        except (OSError, configparser.Error, TypeError, ValueError) as error:
            logging.exception(
                "Error saving preferences to %s: %s",
                self.config_path,
                error,
            )
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                logging.error(
                    "Could not remove temporary preferences file %s: %s",
                    tmp_path,
                    cleanup_error,
                )
            return False

    def get(self, key: str, default: str = "") -> str:
        """Get a single legacy flat preference stored in the gui section."""
        prefs = self.load()
        gui_preferences = prefs.get("gui", {})
        return gui_preferences.get(str(key), default)

    def set(self, key: str, value: str) -> bool:
        """Set a single legacy flat preference in the gui section."""
        prefs = self.load()
        prefs.setdefault("gui", {})[str(key)] = str(value)
        return self.save(prefs)

    def update(self, updates: Dict[str, str]) -> bool:
        """Update legacy flat preferences in the gui section."""
        prefs = self.load()
        gui_preferences = prefs.setdefault("gui", {})
        gui_preferences.update({str(key): str(value) for key, value in updates.items()})
        return self.save(prefs)

    def delete(self, key: str) -> bool:
        """Delete a single legacy flat preference from the gui section."""
        prefs = self.load()
        gui_preferences = prefs.get("gui", {})
        if str(key) in gui_preferences:
            del gui_preferences[str(key)]
            return self.save(prefs)
        return True

    def clear(self) -> bool:
        """Clear all preferences."""
        return self.save({})

    def exists(self) -> bool:
        """Check if preferences file exists."""
        return os.path.exists(self.config_path)

    def get_theme(self) -> str:
        """Get current theme preference."""
        return self.get("theme", DEFAULT_THEME)

    def set_theme(self, theme_name: str) -> bool:
        """Set theme preference."""
        return self.set("theme", theme_name)
