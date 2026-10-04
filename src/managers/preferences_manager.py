"""
Preferences Manager for centralized preference persistence.

Handles loading and saving user preferences to config/config.cfg file.
Uses atomic writes to prevent corruption if process is killed during save.
"""

import configparser
import os
from typing import Dict, Optional

from config.config import DEFAULT_THEME, PREFS_FILE_NAME


class PreferencesManager:
    """Manages loading and saving preferences to config file."""

    def __init__(self, config_dir: Optional[str] = None):
        """
        Initialize preferences manager.

        Args:
            config_dir: Optional path to config directory. If None, will auto-detect
                       relative to the repository root.
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

        try:
            project_root = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            cfg_dir = os.path.join(project_root, "config")
            return os.path.join(cfg_dir, PREFS_FILE_NAME)
        except Exception:
            return PREFS_FILE_NAME

    def _ensure_config_dir(self):
        """Ensure the config directory exists."""
        config_dir = os.path.dirname(self.config_path)
        if config_dir and not os.path.exists(config_dir):
            try:
                os.makedirs(config_dir, exist_ok=True)
            except Exception:
                pass

    def load(self) -> Dict[str, str]:
        """Load preferences from config file.

        Returns:
            Dictionary of preference key-value pairs.
            Returns nested structure for PyQt5 compatibility.
        """
        if not os.path.exists(self.config_path):
            return {}

        cfg = configparser.ConfigParser()
        try:
            cfg.read(self.config_path)

            result = {}
            for section_name in cfg.sections():
                result[section_name] = dict(cfg[section_name])

            return result
        except Exception as e:
            try:
                from src.util.log_utils import log_error
                log_error(None, "PreferencesManager", f"Error loading preferences: {e}")
            except Exception:
                pass
            return {}

    def save(self, preferences: Dict[str, str]) -> bool:
        """Save preferences to config file using atomic write.

        Args:
            preferences: Dictionary of key-value pairs to save. Can be nested
                        (PyQt5 format) or flat (legacy format).

        Returns:
            True if save succeeded, False otherwise
        """
        cfg = configparser.ConfigParser()

        for section_name, section_data in preferences.items():
            if isinstance(section_data, dict):
                cfg[section_name] = {str(k): str(v) for k, v in section_data.items()}
            else:
                if "gui" not in cfg:
                    cfg["gui"] = {}
                cfg["gui"][str(section_name)] = str(section_data)

        tmp_path = self.config_path + ".tmp"

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                cfg.write(f)

            try:
                os.replace(tmp_path, self.config_path)
            except Exception:
                try:
                    if os.path.exists(self.config_path):
                        os.remove(self.config_path)
                except Exception:
                    pass
                try:
                    os.rename(tmp_path, self.config_path)
                except Exception:
                    with open(tmp_path, "r", encoding="utf-8") as fr:
                        with open(self.config_path, "w", encoding="utf-8") as fw:
                            fw.write(fr.read())

            return True

        except Exception as e:
            try:
                from src.util.log_utils import log_error
                log_error(None, "PreferencesManager", f"Error saving preferences: {e}")
            except Exception:
                pass
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            return False

    def get(self, key: str, default: str = "") -> str:
        """Get a single preference value."""
        prefs = self.load()
        return prefs.get(key, default)

    def set(self, key: str, value: str) -> bool:
        """Set a single preference value."""
        prefs = self.load()
        prefs[key] = str(value)
        return self.save(prefs)

    def update(self, updates: Dict[str, str]) -> bool:
        """Update multiple preference values at once."""
        prefs = self.load()
        prefs.update({str(k): str(v) for k, v in updates.items()})
        return self.save(prefs)

    def delete(self, key: str) -> bool:
        """Delete a preference key."""
        prefs = self.load()
        if key in prefs:
            del prefs[key]
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
        prefs = self.load()
        gui_prefs = prefs.get("gui", {})
        return gui_prefs.get("theme", DEFAULT_THEME)

    def set_theme(self, theme_name: str) -> bool:
        """Set theme preference."""
        prefs = self.load()
        if "gui" not in prefs:
            prefs["gui"] = {}
        prefs["gui"]["theme"] = theme_name
        return self.save(prefs)
