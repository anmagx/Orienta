"""
Application manager package for Orienta.

Contains manager classes for preferences and application-wide configuration.
"""

from .preferences_manager import PreferencesManager
from .theme_manager import ThemeManager

__all__ = ["PreferencesManager", "ThemeManager"]
