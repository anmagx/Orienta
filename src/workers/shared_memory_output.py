"""FreeTrack-compatible shared-memory pose publisher."""

import ctypes
import csv
import math
import re
import struct
import sys
from pathlib import Path

FREETRACK_MAPPING_NAME = "FT_SharedMem"
FREETRACK_MUTEX_NAME = "FT_Mutext"
FREETRACK_PUBLISHER_MUTEX_NAME = "Local\\Orienta_FreeTrack_Publisher"
FREETRACK_GAME_CATALOG_NAME = "facetracknoir_supported_games.csv"
FREETRACK_GAME_CATALOG_COLUMNS = (
    "No",
    "Game Name",
    "Game protocol",
    "Supported since",
    "Verified",
    "By",
    "INTERNATIONAL_ID",
    "FTN_ID",
)
ZERO_RESPONSE_TABLE = (0,) * 8


class FTData(ctypes.Structure):
    _fields_ = [
        ("DataID", ctypes.c_uint32),
        ("CamWidth", ctypes.c_int32),
        ("CamHeight", ctypes.c_int32),
        ("Yaw", ctypes.c_float),
        ("Pitch", ctypes.c_float),
        ("Roll", ctypes.c_float),
        ("X", ctypes.c_float),
        ("Y", ctypes.c_float),
        ("Z", ctypes.c_float),
        ("RawYaw", ctypes.c_float),
        ("RawPitch", ctypes.c_float),
        ("RawRoll", ctypes.c_float),
        ("RawX", ctypes.c_float),
        ("RawY", ctypes.c_float),
        ("RawZ", ctypes.c_float),
        ("X1", ctypes.c_float),
        ("Y1", ctypes.c_float),
        ("X2", ctypes.c_float),
        ("Y2", ctypes.c_float),
        ("X3", ctypes.c_float),
        ("Y3", ctypes.c_float),
        ("X4", ctypes.c_float),
        ("Y4", ctypes.c_float),
    ]


class FTHeap(ctypes.Structure):
    _fields_ = [
        ("data", FTData),
        ("GameID", ctypes.c_int32),
        ("table", ctypes.c_uint8 * 8),
        ("GameID2", ctypes.c_int32),
    ]


if ctypes.sizeof(FTData) != 92 or ctypes.sizeof(FTHeap) != 108:
    raise RuntimeError("Unexpected FreeTrack shared-memory structure layout")


def load_freetrack_game_response_tables(catalog_path=None):
    """Load game response tables using FaceTrackNoIR/OpenTrack lookup rules."""
    if catalog_path is None:
        if getattr(sys, "frozen", False):
            catalog_path = (
                Path(sys._MEIPASS)
                / "freetrack_catalog"
                / FREETRACK_GAME_CATALOG_NAME
            )
        else:
            project_root = Path(__file__).resolve().parents[2]
            catalog_path = project_root / "data" / FREETRACK_GAME_CATALOG_NAME
    else:
        catalog_path = Path(catalog_path)

    response_tables = {}
    with catalog_path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as catalog_file:
        reader = csv.DictReader(catalog_file, delimiter=";")
        if tuple(reader.fieldnames or ()) != FREETRACK_GAME_CATALOG_COLUMNS:
            raise ValueError(
                f"Unexpected FaceTrackNoIR catalog columns in {catalog_path}"
            )

        for line_number, row in enumerate(reader, start=2):
            if row is None:
                continue
            if None in row or any(
                row.get(column) is None
                for column in FREETRACK_GAME_CATALOG_COLUMNS
            ):
                raise ValueError(
                    f"Malformed FaceTrackNoIR catalog row at line {line_number}"
                )
            if all(not row[column].strip() for column in FREETRACK_GAME_CATALOG_COLUMNS):
                continue

            game_id_text = row["INTERNATIONAL_ID"].strip()
            if not game_id_text:
                continue
            if re.fullmatch(r"[0-9]+", game_id_text) is None:
                raise ValueError(
                    f"Invalid game ID at FaceTrackNoIR catalog line {line_number}"
                )
            game_id = int(game_id_text)
            if game_id > 0x7FFFFFFF:
                raise ValueError(
                    f"Game ID is out of range at FaceTrackNoIR catalog line {line_number}"
                )

            # OpenTrack's catalog lookup returns the first matching row.
            if game_id in response_tables:
                continue

            table = ZERO_RESPONSE_TABLE
            ftn_id = row["FTN_ID"].strip()
            if row["Supported since"].strip() != "V160" and len(ftn_id) == 22:
                if re.fullmatch(r"[0-9A-Fa-f]{22}", ftn_id) is None:
                    raise ValueError(
                        f"Invalid FTN_ID at FaceTrackNoIR catalog line {line_number}"
                    )
                encoded_table = bytes.fromhex(ftn_id)
                table = (
                    encoded_table[5],
                    encoded_table[4],
                    encoded_table[3],
                    encoded_table[2],
                    encoded_table[9],
                    encoded_table[8],
                    encoded_table[7],
                    encoded_table[6],
                )
            response_tables[game_id] = table

    return response_tables


def freetrack_pose_from_euler(yaw, pitch, roll):
    """Convert Orienta's degree angles to FreeTrack radians and axis signs."""
    angles = tuple(float(value) for value in (yaw, pitch, roll))
    if not all(math.isfinite(value) for value in angles):
        raise ValueError("FreeTrack pose must contain only finite angles")
    return (
        -math.radians(angles[0]),
        -math.radians(angles[1]),
        math.radians(angles[2]),
    )


class FreeTrackSharedMemory:
    """Own the named Windows mapping and publish fixed-layout FTHeap frames."""

    def __init__(self, mapping_name=FREETRACK_MAPPING_NAME,
                 mutex_name=FREETRACK_MUTEX_NAME,
                 publisher_mutex_name=FREETRACK_PUBLISHER_MUTEX_NAME,
                 game_response_tables=None):
        if sys.platform != "win32":
            raise OSError("FreeTrack shared-memory output is available only on Windows")

        self._game_response_tables = (
            load_freetrack_game_response_tables()
            if game_response_tables is None
            else game_response_tables
        )
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._mapping_handle = None
        self._mutex_handle = None
        self._publisher_mutex_handle = None
        self._owns_publisher_mutex = False
        self._view = None
        self._mapping_already_exists = False
        self._configure_api()
        self._open(mapping_name, mutex_name, publisher_mutex_name)

    @property
    def mapping_already_exists(self):
        return self._mapping_already_exists

    def _configure_api(self):
        handle = ctypes.c_void_p
        kernel32 = self._kernel32

        kernel32.CreateFileMappingW.argtypes = [
            handle, handle, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_uint32, ctypes.c_wchar_p,
        ]
        kernel32.CreateFileMappingW.restype = handle
        kernel32.OpenFileMappingW.argtypes = [
            ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p,
        ]
        kernel32.OpenFileMappingW.restype = handle
        kernel32.MapViewOfFile.argtypes = [
            handle, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_uint32, ctypes.c_size_t,
        ]
        kernel32.MapViewOfFile.restype = handle
        kernel32.CreateMutexW.argtypes = [handle, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = handle
        kernel32.WaitForSingleObject.argtypes = [handle, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.ReleaseMutex.argtypes = [handle]
        kernel32.ReleaseMutex.restype = ctypes.c_int
        kernel32.UnmapViewOfFile.argtypes = [handle]
        kernel32.UnmapViewOfFile.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [handle]
        kernel32.CloseHandle.restype = ctypes.c_int

    def _open(self, mapping_name, mutex_name, publisher_mutex_name):
        kernel32 = self._kernel32
        invalid_file = ctypes.c_void_p(-1).value

        ctypes.set_last_error(0)
        self._publisher_mutex_handle = kernel32.CreateMutexW(
            None,
            True,
            publisher_mutex_name,
        )
        if not self._publisher_mutex_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            self.close()
            raise RuntimeError(
                "Another Orienta FreeTrack publisher is already active"
            )
        self._owns_publisher_mutex = True

        ctypes.set_last_error(0)
        self._mapping_handle = kernel32.CreateFileMappingW(
            ctypes.c_void_p(invalid_file),
            None,
            0x04,  # PAGE_READWRITE
            0,
            ctypes.sizeof(FTHeap),
            mapping_name,
        )
        if not self._mapping_handle:
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)
        mapping_already_exists = ctypes.get_last_error() == 183
        self._mapping_already_exists = mapping_already_exists

        self._view = kernel32.MapViewOfFile(
            self._mapping_handle,
            0x000F001F,  # FILE_MAP_ALL_ACCESS
            0,
            0,
            ctypes.sizeof(FTHeap),
        )
        if not self._view:
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

        ctypes.set_last_error(0)
        self._mutex_handle = kernel32.CreateMutexW(None, False, mutex_name)
        if not self._mutex_handle:
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

        wait_result = kernel32.WaitForSingleObject(self._mutex_handle, 1000)
        if wait_result not in (0, 0x80):
            self.close()
            raise OSError(f"Could not initialize FreeTrack mapping (status {wait_result})")
        try:
            if not mapping_already_exists:
                self._store_word(
                    FTHeap.data.offset + FTData.DataID.offset,
                    1,
                )
                self._store_word(FTHeap.GameID2.offset, 0)
            self._store_word(
                FTHeap.data.offset + FTData.CamWidth.offset,
                100,
            )
            self._store_word(
                FTHeap.data.offset + FTData.CamHeight.offset,
                250,
            )
        finally:
            if not kernel32.ReleaseMutex(self._mutex_handle):
                error = ctypes.get_last_error()
                self.close()
                raise ctypes.WinError(error)

    def _write_word(self, offset, value):
        address = int(self._view) + offset
        ctypes.c_uint32.from_address(address).value = int(value) & 0xFFFFFFFF

    def _store_word(self, offset, value):
        self._write_word(offset, value)

    def _store_float(self, offset, value):
        bits = struct.unpack("<i", struct.pack("<f", float(value)))[0]
        self._write_word(offset, bits)

    def write_pose(self, pose):
        if len(pose) < 3:
            raise ValueError("FreeTrack pose requires yaw, pitch, and roll")

        yaw, pitch, roll = freetrack_pose_from_euler(*pose[:3])
        wait_result = self._kernel32.WaitForSingleObject(self._mutex_handle, 1000)
        if wait_result not in (0, 0x80):  # WAIT_OBJECT_0 / WAIT_ABANDONED
            raise OSError(f"Could not acquire FreeTrack mutex (status {wait_result})")

        try:
            values = (
                ("Yaw", yaw),
                ("Pitch", pitch),
                ("Roll", roll),
                ("X", 0.0),
                ("Y", 0.0),
                ("Z", 0.0),
                ("RawYaw", yaw),
                ("RawPitch", pitch),
                ("RawRoll", roll),
                ("RawX", 0.0),
                ("RawY", 0.0),
                ("RawZ", 0.0),
            )
            data_offset = FTHeap.data.offset
            for field_name, value in values:
                self._store_float(
                    data_offset + getattr(FTData, field_name).offset,
                    value,
                )
            data_id = ctypes.c_uint32.from_address(int(self._view))
            heap_address = int(self._view)
            game_id = ctypes.c_int32.from_address(
                heap_address + FTHeap.GameID.offset
            ).value
            game_id_2 = ctypes.c_int32.from_address(
                heap_address + FTHeap.GameID2.offset
            ).value
            if game_id != game_id_2:
                response_table = self._game_response_tables.get(
                    game_id,
                    ZERO_RESPONSE_TABLE,
                )
                for index, value in enumerate(response_table):
                    ctypes.c_uint8.from_address(
                        heap_address + FTHeap.table.offset + index
                    ).value = value
                self._store_word(FTHeap.GameID2.offset, game_id)
                data_id.value = 0
            else:
                data_id.value = (data_id.value + 1) & 0xFFFFFFFF
        finally:
            if not self._kernel32.ReleaseMutex(self._mutex_handle):
                raise ctypes.WinError(ctypes.get_last_error())

    def send_pose(self, pose):
        """Implement the common output-backend pose method."""
        self.write_pose(pose)

    def close(self):
        errors = []
        if self._view:
            if not self._kernel32.UnmapViewOfFile(self._view):
                errors.append(ctypes.get_last_error())
            self._view = None
        for handle_name in ("_mutex_handle", "_mapping_handle"):
            handle = getattr(self, handle_name)
            if handle:
                if not self._kernel32.CloseHandle(handle):
                    errors.append(ctypes.get_last_error())
                setattr(self, handle_name, None)
        if self._publisher_mutex_handle:
            if self._owns_publisher_mutex:
                if not self._kernel32.ReleaseMutex(self._publisher_mutex_handle):
                    errors.append(ctypes.get_last_error())
                self._owns_publisher_mutex = False
            if not self._kernel32.CloseHandle(self._publisher_mutex_handle):
                errors.append(ctypes.get_last_error())
            self._publisher_mutex_handle = None
        if errors:
            raise ctypes.WinError(errors[0])
