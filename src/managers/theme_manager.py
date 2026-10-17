"""
Theme manager for Orienta.

Handles loading and applying light/dark themes to the PyQt5 application.
"""

import os

from PyQt5.QtWidgets import QApplication

from src.config.config import DEFAULT_THEME


THEME_OPTIONS = (
    ("light", "Light"),
    ("dark", "Dark"),
    ("solarized_light", "Solarized Light"),
    ("solarized_dark", "Solarized Dark"),
)
THEMES = tuple(option[0] for option in THEME_OPTIONS)
SOLARIZED_BASE_THEMES = {
    "solarized_light": "light",
    "solarized_dark": "dark",
}


class ThemeManager:
    """Manages application themes and styling."""

    def __init__(self, app=None):
        """
        Initialize the theme manager.

        Args:
            app: QApplication instance
        """
        self.app = app or QApplication.instance()
        self.current_theme = DEFAULT_THEME
        self.themes_dir = self._find_themes_dir()

    def _find_themes_dir(self):
        """Find the repository themes directory by searching upward."""
        search_dir = os.path.dirname(os.path.abspath(__file__))
        while True:
            candidate = os.path.join(search_dir, "themes")
            if os.path.isdir(candidate):
                return candidate
            parent = os.path.dirname(search_dir)
            if not parent or parent == search_dir:
                break
            search_dir = parent

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(project_root, "themes")

    def load_theme(self, theme_name):
        """
        Load and apply a theme.

        Args:
            theme_name: An available theme identifier.
        """
        if theme_name not in THEMES:
            try:
                from src.util.log_utils import log_warning
                log_warning(
                    None,
                    "ThemeManager",
                    f"Unknown theme: {theme_name}, defaulting to {DEFAULT_THEME}",
                )
            except Exception:
                pass
            theme_name = DEFAULT_THEME

        theme_file = os.path.join(self.themes_dir, f"{theme_name}.qss")

        try:
            if os.path.exists(theme_file):
                with open(theme_file, "r", encoding="utf-8") as f:
                    stylesheet = f.read()

                base_theme = SOLARIZED_BASE_THEMES.get(theme_name)
                if base_theme is not None:
                    base_file = os.path.join(
                        self.themes_dir, f"{base_theme}.qss"
                    )
                    with open(base_file, "r", encoding="utf-8") as f:
                        stylesheet = f"{f.read()}\n{stylesheet}"

                if self.app:
                    self.app.setStyleSheet(stylesheet)
                    self.current_theme = theme_name
                    try:
                        from src.util.log_utils import log_info
                        log_info(None, "ThemeManager", f"Applied {theme_name} theme")
                    except Exception:
                        pass
                else:
                    try:
                        from src.util.log_utils import log_warning
                        log_warning(None, "ThemeManager", "No QApplication instance available")
                    except Exception:
                        pass
            else:
                try:
                    from src.util.log_utils import log_warning
                    log_warning(None, "ThemeManager", f"Theme file not found: {theme_file}")
                except Exception:
                    pass

        except Exception as e:
            try:
                from src.util.log_utils import log_error
                log_error(None, "ThemeManager", f"Error loading theme {theme_name}: {e}")
            except Exception:
                pass

    def get_current_theme(self):
        """Get the currently active theme name."""
        return self.current_theme

    def toggle_theme(self):
        """Toggle between light/dark or their corresponding Solarized variants."""
        theme_pairs = {
            "light": "dark",
            "dark": "light",
            "solarized_light": "solarized_dark",
            "solarized_dark": "solarized_light",
        }
        new_theme = theme_pairs.get(self.current_theme, "dark")
        self.load_theme(new_theme)
        return new_theme

    def get_available_themes(self):
        """Get list of available theme names."""
        return list(THEMES)
