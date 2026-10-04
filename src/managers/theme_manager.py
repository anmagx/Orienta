"""
Theme manager for Orienta.

Handles loading and applying light/dark themes to the PyQt5 application.
"""

import os

from PyQt5.QtWidgets import QApplication


class ThemeManager:
    """Manages application themes and styling."""

    def __init__(self, app=None):
        """
        Initialize the theme manager.

        Args:
            app: QApplication instance
        """
        self.app = app or QApplication.instance()
        self.current_theme = "light"
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
            theme_name: Theme name ('light' or 'dark')
        """
        if theme_name not in ["light", "dark"]:
            try:
                from src.util.log_utils import log_warning
                log_warning(None, "ThemeManager", f"Unknown theme: {theme_name}, defaulting to light")
            except Exception:
                pass
            theme_name = "light"

        theme_file = os.path.join(self.themes_dir, f"{theme_name}.qss")

        try:
            if os.path.exists(theme_file):
                with open(theme_file, "r", encoding="utf-8") as f:
                    stylesheet = f.read()

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
        """Toggle between light and dark themes."""
        new_theme = "dark" if self.current_theme == "light" else "light"
        self.load_theme(new_theme)
        return new_theme

    def get_available_themes(self):
        """Get list of available theme names."""
        return ["light", "dark"]
