"""
Theme manager for orienta GUI.

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
        self.current_theme = 'light'
        
        # Calculate path to themes directory by searching upward from this file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        search_dir = current_dir
        found = False
        # Walk upward until we find a 'themes' directory or reach filesystem root
        while True:
            candidate = os.path.join(search_dir, 'themes')
            if os.path.isdir(candidate):
                self.themes_dir = candidate
                found = True
                break
            parent = os.path.dirname(search_dir)
            if not parent or parent == search_dir:
                break
            search_dir = parent

        # Fallback: assume project root is two levels up (workers/gui_qt -> project)
        if not found:
            project_root = os.path.dirname(os.path.dirname(current_dir))
            self.themes_dir = os.path.join(project_root, 'themes')
        
    def load_theme(self, theme_name):
        """
        Load and apply a theme.
        
        Args:
            theme_name: Theme name ('light' or 'dark')
        """
        if theme_name not in ['light', 'dark']:
            try:
                from util.log_utils import log_warning
                log_warning(None, 'ThemeManager', f"Unknown theme: {theme_name}, defaulting to light")
            except Exception:
                pass
            theme_name = 'light'
        
        theme_file = os.path.join(self.themes_dir, f"{theme_name}.qss")
        
        try:
            if os.path.exists(theme_file):
                with open(theme_file, 'r', encoding='utf-8') as f:
                    stylesheet = f.read()
                
                if self.app:
                    self.app.setStyleSheet(stylesheet)
                    self.current_theme = theme_name
                    try:
                        from util.log_utils import log_info
                        log_info(None, 'ThemeManager', f"Applied {theme_name} theme")
                    except Exception:
                        pass
                else:
                    try:
                        from util.log_utils import log_warning
                        log_warning(None, 'ThemeManager', "No QApplication instance available")
                    except Exception:
                        pass
            else:
                try:
                    from util.log_utils import log_warning
                    log_warning(None, 'ThemeManager', f"Theme file not found: {theme_file}")
                except Exception:
                    pass
                
        except Exception as e:
            try:
                from util.log_utils import log_error
                log_error(None, 'ThemeManager', f"Error loading theme {theme_name}: {e}")
            except Exception:
                pass
    
    def get_current_theme(self):
        """
        Get the currently active theme name.
        
        Returns:
            str: Current theme name
        """
        return self.current_theme
    
    def toggle_theme(self):
        """Toggle between light and dark themes."""
        new_theme = 'dark' if self.current_theme == 'light' else 'light'
        self.load_theme(new_theme)
        return new_theme
    
    def get_available_themes(self):
        """
        Get list of available theme names.
        
        Returns:
            list: Available theme names
        """
        return ['light', 'dark']