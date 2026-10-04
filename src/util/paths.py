"""Writable per-user paths, independent of the source or executable location."""

import os


def get_app_data_dir() -> str:
    """Return and create Orienta's directory under Windows Local AppData."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        local_app_data = os.path.join(os.path.expanduser("~"), "AppData", "Local")

    app_data_dir = os.path.join(local_app_data, "Orienta")
    os.makedirs(app_data_dir, exist_ok=True)
    return app_data_dir
