"""Stage and reversibly register Orienta's FreeTrack and NPClient game DLLs."""

import os
import filecmp
import ctypes
from pathlib import Path
import shutil
import sys

from src.util.paths import get_app_data_dir


CLIENT_DLLS = ("FreeTrackClient.dll", "FreeTrackClient64.dll")
FREETRACK_CLIENT_KEY = r"Software\Freetrack\FreetrackClient"
ORIENTA_CLIENT_STATE_KEY = r"Software\Orienta\FreeTrackClient"
ORIENTA_PUBLISHER_MUTEX_NAME = r"Local\Orienta_FreeTrack_Publisher"
NPCLIENT_DLLS = ("NPClient.dll", "NPClient64.dll")
NPCLIENT_CLIENT_KEY = r"Software\NaturalPoint\NATURALPOINT\NPClient Location"
ORIENTA_NPCLIENT_STATE_KEY = r"Software\Orienta\NPClient"


class FreeTrackClientRegistration:
    """Stage the client DLLs and reversibly register their directory per user."""

    def __init__(self, source_directory=None, app_data_directory=None,
                 client_registry_key=FREETRACK_CLIENT_KEY,
                 state_registry_key=ORIENTA_CLIENT_STATE_KEY,
                 publisher_mutex_name=ORIENTA_PUBLISHER_MUTEX_NAME,
                 client_dlls=CLIENT_DLLS,
                 source_subdirectory="freetrack_client",
                 staging_directory="FreeTrackClient",
                 client_name="FreeTrack"):
        self.source_directory = source_directory
        self.app_data_directory = app_data_directory
        self.client_registry_key = client_registry_key
        self.state_registry_key = state_registry_key
        self.publisher_mutex_name = publisher_mutex_name
        self.client_dlls = tuple(client_dlls)
        self.source_subdirectory = source_subdirectory
        self.staging_directory = staging_directory
        self.client_name = client_name
        self._registered_path = None

    def _source_dir(self):
        if self.source_directory is not None:
            return os.path.abspath(self.source_directory)
        if getattr(sys, "frozen", False):
            return os.path.join(sys._MEIPASS, self.source_subdirectory)
        project_root = Path(__file__).resolve().parents[2]
        return str(project_root / "build" / "dist" / self.source_subdirectory)

    def _app_data_dir(self):
        if self.app_data_directory is not None:
            os.makedirs(self.app_data_directory, exist_ok=True)
            return os.path.abspath(self.app_data_directory)
        return get_app_data_dir()

    @staticmethod
    def _registry():
        if os.name != "nt":
            raise OSError("FreeTrack client registration is available only on Windows")
        import winreg
        return winreg

    @staticmethod
    def _read_value(winreg, key, name):
        try:
            value, value_type = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            return False, None, None
        if (
            value_type not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ)
            or not isinstance(value, str)
        ):
            raise OSError(f"Unexpected registry value type for {name}")
        return True, value, value_type

    @staticmethod
    def _normalized_path(path):
        if path is None:
            return None
        return os.path.normcase(os.path.normpath(path))

    def _stage_dlls(self):
        source_dir = self._source_dir()
        for filename in self.client_dlls:
            source = os.path.join(source_dir, filename)
            if not os.path.isfile(source):
                raise FileNotFoundError(
                    f"{self.client_name} client DLL is missing: {source}. "
                    "Run build\\build_game_clients.bat before packaging."
                )

        destination_dir = os.path.join(
            self._app_data_dir(),
            self.staging_directory,
        )
        os.makedirs(destination_dir, exist_ok=True)
        for filename in self.client_dlls:
            source = os.path.join(source_dir, filename)
            destination = os.path.join(destination_dir, filename)
            if self._normalized_path(source) == self._normalized_path(destination):
                continue
            if os.path.isfile(destination) and filecmp.cmp(
                source,
                destination,
                shallow=False,
            ):
                continue
            temporary = destination + ".tmp"
            try:
                shutil.copyfile(source, temporary)
                os.replace(temporary, destination)
            finally:
                if os.path.exists(temporary):
                    os.remove(temporary)
        return os.path.abspath(destination_dir).rstrip("\\/") + os.sep

    def register(self):
        """Register the staged client, preserving any prior client path."""
        winreg = self._registry()
        client_path = self._stage_dlls()

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            self.client_registry_key,
            0,
            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
        ) as client_key:
            has_current, current_path, current_type = self._read_value(
                winreg, client_key, "Path"
            )

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            self.state_registry_key,
            0,
            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
        ) as state_key:
            has_owned, owned_path, _ = self._read_value(
                winreg, state_key, "OwnedPath"
            )
            has_previous, previous_path, _ = self._read_value(
                winreg, state_key, "PreviousPath"
            )
            try:
                previous_type, _ = winreg.QueryValueEx(state_key, "PreviousType")
            except FileNotFoundError:
                previous_type = winreg.REG_SZ
            try:
                had_previous, had_previous_value = winreg.QueryValueEx(
                    state_key,
                    "HadPreviousPath",
                )
            except FileNotFoundError:
                had_previous = False
                had_previous_value = 0

        target_normalized = self._normalized_path(client_path)
        current_normalized = self._normalized_path(current_path) if has_current else None
        owned_normalized = self._normalized_path(owned_path) if has_owned else None

        if current_normalized == target_normalized:
            if (
                has_owned
                and owned_normalized == target_normalized
                and self._registered_path is None
            ):
                raise RuntimeError(
                    f"{self.client_name} output is registered by another Orienta session. "
                    "Stop output in that session before starting it here."
                )
            self._registered_path = (
                client_path if has_owned and owned_normalized == target_normalized
                else None
            )
            return client_path

        if has_owned and owned_normalized == current_normalized:
            saved_path = previous_path if has_previous else ""
            saved_had_path = bool(had_previous_value)
            saved_type = previous_type
        else:
            saved_path = current_path if has_current else ""
            saved_had_path = has_current
            saved_type = current_type if has_current else winreg.REG_SZ

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            self.state_registry_key,
            0,
            winreg.KEY_SET_VALUE,
        ) as state_key:
            winreg.SetValueEx(state_key, "OwnedPath", 0, winreg.REG_SZ, client_path)
            winreg.SetValueEx(
                state_key,
                "PreviousPath",
                0,
                winreg.REG_SZ,
                saved_path,
            )
            winreg.SetValueEx(
                state_key,
                "HadPreviousPath",
                0,
                winreg.REG_DWORD,
                int(saved_had_path),
            )
            winreg.SetValueEx(
                state_key,
                "PreviousType",
                0,
                winreg.REG_DWORD,
                int(saved_type),
            )

        try:
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                self.client_registry_key,
                0,
                winreg.KEY_SET_VALUE,
            ) as client_key:
                winreg.SetValueEx(
                    client_key,
                    "Path",
                    0,
                    winreg.REG_SZ,
                    client_path,
                )
        except OSError:
            self._clear_saved_state(winreg)
            raise

        self._registered_path = client_path
        return client_path

    def restore(self):
        """Restore the previous client path only if Orienta still owns it."""
        if self._registered_path is None:
            return False
        winreg = self._registry()
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            self.state_registry_key,
            0,
            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
        ) as state_key:
            has_owned, owned_path, _ = self._read_value(
                winreg, state_key, "OwnedPath"
            )
            has_previous, previous_path, _ = self._read_value(
                winreg, state_key, "PreviousPath"
            )
            try:
                previous_type, _ = winreg.QueryValueEx(state_key, "PreviousType")
            except FileNotFoundError:
                previous_type = winreg.REG_SZ
            try:
                had_previous, had_previous_value = winreg.QueryValueEx(
                    state_key,
                    "HadPreviousPath",
                )
            except FileNotFoundError:
                had_previous = False
                had_previous_value = 0

        if not has_owned:
            self._registered_path = None
            return False

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            self.client_registry_key,
            0,
            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
        ) as client_key:
            has_current, current_path, _ = self._read_value(
                winreg, client_key, "Path"
            )
            still_owned = (
                has_current
                and self._normalized_path(current_path)
                == self._normalized_path(owned_path)
            )
            if still_owned:
                if had_previous and had_previous_value and has_previous:
                    winreg.SetValueEx(
                        client_key,
                        "Path",
                        0,
                        previous_type,
                        previous_path,
                    )
                else:
                    try:
                        winreg.DeleteValue(client_key, "Path")
                    except FileNotFoundError:
                        pass

        self._clear_saved_state(winreg)
        self._registered_path = None
        return still_owned

    def recover_previous_registration(self):
        """Restore stale registration left by a crashed Orienta instance."""
        winreg = self._registry()
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                self.state_registry_key,
                0,
                winreg.KEY_QUERY_VALUE,
            ) as state_key:
                has_owned, owned_path, _ = self._read_value(
                    winreg,
                    state_key,
                    "OwnedPath",
                )
        except FileNotFoundError:
            return False
        if not has_owned:
            return False

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenMutexW.argtypes = [
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_wchar_p,
        ]
        kernel32.OpenMutexW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
        kernel32.ReleaseMutex.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int

        ctypes.set_last_error(0)
        publisher_mutex = kernel32.OpenMutexW(
            0x00100001,  # SYNCHRONIZE | MUTEX_MODIFY_STATE
            False,
            self.publisher_mutex_name,
        )
        if publisher_mutex:
            try:
                wait_result = kernel32.WaitForSingleObject(publisher_mutex, 0)
                if wait_result == 0x102:  # WAIT_TIMEOUT: an Orienta publisher owns it
                    return False
                if wait_result not in (0, 0x80):  # WAIT_OBJECT_0 / WAIT_ABANDONED
                    raise ctypes.WinError(ctypes.get_last_error())
                if not kernel32.ReleaseMutex(publisher_mutex):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                if not kernel32.CloseHandle(publisher_mutex):
                    raise ctypes.WinError(ctypes.get_last_error())
        elif ctypes.get_last_error() not in (2, 3):  # FILE/PATH_NOT_FOUND
            raise ctypes.WinError(ctypes.get_last_error())

        self._registered_path = owned_path
        return self.restore()

    def _clear_saved_state(self, winreg):
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            self.state_registry_key,
            0,
            winreg.KEY_SET_VALUE,
        ) as state_key:
            for name in (
                "OwnedPath",
                "PreviousPath",
                "HadPreviousPath",
                "PreviousType",
            ):
                try:
                    winreg.DeleteValue(state_key, name)
                except FileNotFoundError:
                    pass


class NPClientRegistration(FreeTrackClientRegistration):
    """Stage Orienta's NPClient DLLs and reversibly register their directory."""

    def __init__(self, source_directory=None, app_data_directory=None,
                 client_registry_key=NPCLIENT_CLIENT_KEY,
                 state_registry_key=ORIENTA_NPCLIENT_STATE_KEY,
                 publisher_mutex_name=ORIENTA_PUBLISHER_MUTEX_NAME):
        super().__init__(
            source_directory=source_directory,
            app_data_directory=app_data_directory,
            client_registry_key=client_registry_key,
            state_registry_key=state_registry_key,
            publisher_mutex_name=publisher_mutex_name,
            client_dlls=NPCLIENT_DLLS,
            source_subdirectory="npclient",
            staging_directory="NPClient",
            client_name="NPClient",
        )
