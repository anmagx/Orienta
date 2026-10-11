/*
 * Orienta NPClient compatibility DLL, derived from uglyDwarf's LinuxTrack
 * NPClient implementation. The FreeTrack-to-NPClient mapping approach and
 * protocol behavior and NP_GetSignature identity payload are attributed to
 * that work; see README-CREDIT.txt and LICENSE.md for provenance and terms.
 *
 * Copyright (c) 2015 uglyDwarf
 * Copyright (c) 2026 Orienta contributors
 */
#define WIN32_LEAN_AND_MEAN
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <windows.h>

#include "npclient.h"

#define NP_MUTEX_WAIT_MS 50
#define NP_PI 3.14159265358979323846

_Static_assert(sizeof(OrientaFTData) == 92, "FreeTrack data layout mismatch");
_Static_assert(sizeof(OrientaFTHeap) == 108, "FreeTrack heap layout mismatch");
_Static_assert(offsetof(OrientaFTHeap, GameID) == 92, "FreeTrack GameID offset mismatch");
_Static_assert(sizeof(OrientaNPData) == 68, "NPClient pose layout mismatch");

static SRWLOCK mapping_lock = SRWLOCK_INIT;
static SRWLOCK encryption_lock = SRWLOCK_INIT;
static HANDLE mapping_handle;
static HANDLE mutex_handle;
static OrientaFTHeap *shared_heap;
static HINSTANCE client_instance;
static volatile LONG frame_number;
static BOOL game_id_seen;
static BOOL encryption_checked;
static BOOL encryption_active;
static LONG last_game_id;
static uint8_t encryption_table[8];
static volatile LONG trace_get_data;
static volatile LONG trace_query_version;
static volatile LONG trace_get_signature;
static volatile LONG trace_register_program;
static volatile LONG trace_mapping_failed;
static volatile LONG trace_mapping_opened;
static volatile LONG trace_first_active_pose;

static void trace_message(const char *message)
{
    WCHAR path[MAX_PATH];
    WCHAR *separator = NULL;
    const WCHAR filename[] = L"NPClient.log";
    DWORD length;
    DWORD bytes_written;
    HANDLE file;
    size_t index;
    size_t filename_length = sizeof(filename) / sizeof(filename[0]);

    length = GetModuleFileNameW(client_instance, path, MAX_PATH);
    if (length == 0 || length >= MAX_PATH) {
        OutputDebugStringA("Orienta NPClient: could not locate its module path\n");
        return;
    }
    for (index = 0; index < length; index++) {
        if (path[index] == L'\\' || path[index] == L'/') {
            separator = path + index;
        }
    }
    if (separator == NULL) {
        return;
    }
    separator[1] = L'\0';
    if ((size_t)(separator + 1 - path) + filename_length > MAX_PATH) {
        return;
    }
    memcpy(separator + 1, filename, sizeof(filename));

    file = CreateFileW(
        path,
        FILE_APPEND_DATA,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        NULL,
        OPEN_ALWAYS,
        FILE_ATTRIBUTE_NORMAL,
        NULL
    );
    if (file == INVALID_HANDLE_VALUE) {
        OutputDebugStringA("Orienta NPClient: could not open NPClient.log\n");
        return;
    }
    if (!WriteFile(file, message, (DWORD)strlen(message), &bytes_written, NULL)
        || !WriteFile(file, "\r\n", 2, &bytes_written, NULL)) {
        OutputDebugStringA("Orienta NPClient: could not write NPClient.log\n");
    }
    CloseHandle(file);
}

static void trace_once(volatile LONG *written, const char *message)
{
    if (InterlockedCompareExchange(written, 1, 0) == 0) {
        trace_message(message);
    }
}

static void close_mapping_locked(void)
{
    if (shared_heap != NULL) {
        UnmapViewOfFile(shared_heap);
        shared_heap = NULL;
    }
    if (mutex_handle != NULL) {
        CloseHandle(mutex_handle);
        mutex_handle = NULL;
    }
    if (mapping_handle != NULL) {
        CloseHandle(mapping_handle);
        mapping_handle = NULL;
    }
}

static BOOL open_mapping_locked(void)
{
    if (mapping_handle != NULL && mutex_handle != NULL && shared_heap != NULL) {
        return TRUE;
    }

    close_mapping_locked();
    mapping_handle = OpenFileMappingW(
        FILE_MAP_READ | FILE_MAP_WRITE,
        FALSE,
        ORIENTA_NP_MAPPING_NAME
    );
    if (mapping_handle == NULL) {
        mapping_handle = CreateFileMappingW(
            INVALID_HANDLE_VALUE,
            NULL,
            PAGE_READWRITE,
            0,
            (DWORD)sizeof(OrientaFTHeap),
            ORIENTA_NP_MAPPING_NAME
        );
    }
    if (mapping_handle == NULL) {
        trace_once(&trace_mapping_failed, "FT_SharedMem could not be opened or created");
        close_mapping_locked();
        return FALSE;
    }

    shared_heap = (OrientaFTHeap *)MapViewOfFile(
        mapping_handle,
        FILE_MAP_READ | FILE_MAP_WRITE,
        0,
        0,
        sizeof(OrientaFTHeap)
    );
    if (shared_heap == NULL) {
        trace_once(&trace_mapping_failed, "FT_SharedMem could not be mapped");
        close_mapping_locked();
        return FALSE;
    }

    mutex_handle = OpenMutexW(
        SYNCHRONIZE | MUTEX_MODIFY_STATE,
        FALSE,
        ORIENTA_NP_MUTEX_NAME
    );
    if (mutex_handle == NULL) {
        mutex_handle = CreateMutexW(
            NULL,
            FALSE,
            ORIENTA_NP_MUTEX_NAME
        );
    }
    if (mutex_handle == NULL) {
        trace_once(&trace_mapping_failed, "FT_Mutext could not be opened or created");
        close_mapping_locked();
        return FALSE;
    }

    trace_once(&trace_mapping_opened, "FT_SharedMem and FT_Mutext opened");
    return TRUE;
}

static BOOL copy_shared_heap(OrientaFTHeap *snapshot)
{
    DWORD wait_result;
    BOOL copied = FALSE;

    AcquireSRWLockExclusive(&mapping_lock);
    if (open_mapping_locked()) {
        wait_result = WaitForSingleObject(mutex_handle, NP_MUTEX_WAIT_MS);
        if (wait_result == WAIT_OBJECT_0 || wait_result == WAIT_ABANDONED) {
            memcpy(snapshot, shared_heap, sizeof(*snapshot));
            copied = TRUE;
            if (!ReleaseMutex(mutex_handle)) {
                close_mapping_locked();
                copied = FALSE;
            }
        } else if (wait_result == WAIT_FAILED) {
            trace_once(&trace_mapping_failed, "FT_Mutext wait failed");
            close_mapping_locked();
        } else if (wait_result == WAIT_TIMEOUT) {
            trace_once(&trace_mapping_failed, "FT_Mutext wait timed out");
        }
    }
    ReleaseSRWLockExclusive(&mapping_lock);
    return copied;
}

static BOOL write_game_id(uint16_t program_id)
{
    DWORD wait_result;
    BOOL written = FALSE;

    AcquireSRWLockExclusive(&mapping_lock);
    if (open_mapping_locked()) {
        wait_result = WaitForSingleObject(mutex_handle, NP_MUTEX_WAIT_MS);
        if (wait_result == WAIT_OBJECT_0 || wait_result == WAIT_ABANDONED) {
            shared_heap->GameID = program_id;
            written = TRUE;
            if (!ReleaseMutex(mutex_handle)) {
                close_mapping_locked();
                written = FALSE;
            }
        } else if (wait_result == WAIT_FAILED) {
            close_mapping_locked();
        }
    }
    ReleaseSRWLockExclusive(&mapping_lock);
    return written;
}

static BOOL get_game_encryption(
    const OrientaFTHeap *snapshot,
    uint8_t table[8]
)
{
    BOOL active;
    unsigned int index;

    AcquireSRWLockExclusive(&encryption_lock);
    if (!game_id_seen || last_game_id != snapshot->GameID) {
        game_id_seen = TRUE;
        last_game_id = snapshot->GameID;
        encryption_checked = FALSE;
        encryption_active = FALSE;
    }

    if (snapshot->GameID != snapshot->GameID2) {
        encryption_checked = FALSE;
        encryption_active = FALSE;
        memset(encryption_table, 0, sizeof(encryption_table));
    } else if (
        !encryption_checked
        || memcmp(encryption_table, snapshot->table, sizeof(encryption_table)) != 0
    ) {
        memcpy(encryption_table, snapshot->table, sizeof(encryption_table));
        encryption_checked = TRUE;
        encryption_active = FALSE;
        for (index = 0; index < sizeof(encryption_table); index++) {
            if (encryption_table[index] != 0) {
                encryption_active = TRUE;
                break;
            }
        }
    }

    memcpy(table, encryption_table, sizeof(encryption_table));
    active = encryption_active;
    ReleaseSRWLockExclusive(&encryption_lock);
    return active;
}

static void np_enhance(uint8_t *bytes, size_t size, const uint8_t table[8])
{
    size_t table_index = 0;
    uint8_t rolling = 0x88;

    while (size != 0) {
        size_t index = --size;
        uint8_t value = bytes[index];
        bytes[index] = value ^ table[table_index] ^ rolling;
        rolling = (uint8_t)(rolling + index + value);
        table_index++;
        if (table_index == 8) {
            table_index = 0;
        }
    }
}

static uint16_t read_signed_word(const uint8_t *bytes)
{
    return (uint16_t)((uint16_t)bytes[0] | ((uint16_t)bytes[1] << 8));
}

static uint32_t arithmetic_shift_right(uint32_t value, unsigned int shift)
{
    uint32_t shifted = value >> shift;
    if ((value & 0x80000000u) != 0) {
        shifted |= UINT32_MAX << (32u - shift);
    }
    return shifted;
}

static uint32_t np_checksum(const uint8_t *bytes, size_t size)
{
    size_t rounds = size >> 2;
    size_t remainder = size & 3;
    uint32_t checksum = (uint32_t)size;
    size_t offset = 0;

    while (rounds-- != 0) {
        int32_t first = (int16_t)read_signed_word(bytes + offset);
        int32_t second = (int16_t)read_signed_word(bytes + offset + 2);
        uint32_t mixed = (uint32_t)second;

        offset += 4;
        checksum += (uint32_t)first;
        mixed ^= checksum << 5;
        mixed <<= 11;
        checksum ^= mixed;
        checksum += arithmetic_shift_right(checksum, 11);
    }

    if (remainder == 3) {
        int32_t first = (int16_t)read_signed_word(bytes + offset);
        int32_t second = (int8_t)bytes[offset + 2];
        uint32_t mixed;

        checksum += (uint32_t)first;
        mixed = (uint32_t)second << 2;
        mixed ^= checksum;
        checksum ^= mixed << 16;
        mixed = arithmetic_shift_right(checksum, 11);
        checksum += mixed;
    } else if (remainder == 2) {
        int32_t second = (int16_t)read_signed_word(bytes + offset);
        checksum += (uint32_t)second;
        checksum ^= checksum << 11;
        checksum += arithmetic_shift_right(checksum, 17);
    } else if (remainder == 1) {
        int32_t second = (int8_t)bytes[offset];
        checksum += (uint32_t)second;
        checksum ^= checksum << 10;
        checksum += arithmetic_shift_right(checksum, 1);
    }

    checksum ^= checksum << 3;
    checksum += arithmetic_shift_right(checksum, 5);
    checksum ^= checksum << 4;
    checksum += arithmetic_shift_right(checksum, 17);
    checksum ^= checksum << 25;
    checksum += arithmetic_shift_right(checksum, 6);
    return checksum;
}

static float axis_value(float value, double scale)
{
    double scaled;
    if (!isfinite(value)) {
        return 0.0f;
    }
    scaled = (double)value * scale;
    if (scaled > NPCLIENT_AXIS_MAX) {
        scaled = NPCLIENT_AXIS_MAX;
    } else if (scaled < -NPCLIENT_AXIS_MAX) {
        scaled = -NPCLIENT_AXIS_MAX;
    }
    return (float)scaled;
}

static BOOL pose_is_valid(const OrientaFTData *data)
{
    return isfinite(data->Roll)
        && isfinite(data->Pitch)
        && isfinite(data->Yaw)
        && isfinite(data->X)
        && isfinite(data->Y)
        && isfinite(data->Z);
}

static void trace_pose_frame(
    LONG sequence,
    BOOL have_pose,
    const OrientaFTHeap *snapshot,
    BOOL encrypted,
    const uint8_t table[8],
    const OrientaNPData *data
)
{
    char message[384];
    int length;

    if (sequence > 5 && sequence % 120 != 0) {
        return;
    }
    if (have_pose) {
        length = snprintf(
            message,
            sizeof(message),
            "NP_GetData frame=%ld status=%d game=%ld/%ld table=%02x%02x%02x%02x%02x%02x%02x%02x encrypted=%d source=[%.5f,%.5f,%.5f;%.2f,%.2f,%.2f] np=[%.1f,%.1f,%.1f;%.1f,%.1f,%.1f] checksum=%08lx",
            (long)sequence,
            (int)data->status,
            (long)snapshot->GameID,
            (long)snapshot->GameID2,
            table[0],
            table[1],
            table[2],
            table[3],
            table[4],
            table[5],
            table[6],
            table[7],
            encrypted,
            snapshot->data.Yaw,
            snapshot->data.Pitch,
            snapshot->data.Roll,
            snapshot->data.X,
            snapshot->data.Y,
            snapshot->data.Z,
            data->yaw,
            data->pitch,
            data->roll,
            data->tx,
            data->ty,
            data->tz,
            (unsigned long)data->checksum
        );
    } else {
        length = snprintf(
            message,
            sizeof(message),
            "NP_GetData frame=%ld status=%d game unavailable; no shared pose available",
            (long)sequence,
            (int)data->status
        );
    }
    if (length > 0 && (size_t)length < sizeof(message)) {
        trace_message(message);
    }
}

int WINAPI NPPriv_ClientNotify(void) { return 0; }
int WINAPI NPPriv_GetLastError(void) { return 0; }
int WINAPI NPPriv_SetData(void) { return 0; }
int WINAPI NPPriv_SetLastError(void) { return 0; }
int WINAPI NPPriv_SetParameter(void) { return 0; }
int WINAPI NPPriv_SetSignature(void) { return 0; }
int WINAPI NPPriv_SetVersion(void) { return 0; }

int WINAPI NP_GetData(OrientaNPData *data)
{
    OrientaFTHeap snapshot;
    BOOL have_pose;
    BOOL encryption = FALSE;
    int active;
    LONG sequence;
    uint8_t table[8] = {0};

    if (data == NULL) {
        return NPCLIENT_STATUS_DISABLED;
    }
    trace_once(&trace_get_data, "NP_GetData was called");
    memset(data, 0, sizeof(*data));
    sequence = InterlockedIncrement(&frame_number);
    data->frame = (int16_t)sequence;

    have_pose = copy_shared_heap(&snapshot);
    if (have_pose) {
        data->yaw = axis_value(snapshot.data.Yaw, NPCLIENT_AXIS_MAX / NP_PI);
        data->pitch = axis_value(snapshot.data.Pitch, NPCLIENT_AXIS_MAX / NP_PI);
        data->roll = axis_value(snapshot.data.Roll, NPCLIENT_AXIS_MAX / NP_PI);
        data->tx = axis_value(snapshot.data.X, NPCLIENT_AXIS_MAX / 500.0);
        data->ty = axis_value(snapshot.data.Y, NPCLIENT_AXIS_MAX / 500.0);
        data->tz = axis_value(snapshot.data.Z, NPCLIENT_AXIS_MAX / 500.0);
        encryption = get_game_encryption(&snapshot, table);
    }

    active = have_pose && pose_is_valid(&snapshot.data)
        && (snapshot.data.CamWidth > 0 && snapshot.data.CamHeight > 0);
    if (active) {
        trace_once(&trace_first_active_pose, "NP_GetData returned a valid pose");
    }
    data->status = active ? NPCLIENT_STATUS_OK : NPCLIENT_STATUS_DISABLED;
    data->checksum = np_checksum((const uint8_t *)data, sizeof(*data));
    trace_pose_frame(sequence, have_pose, &snapshot, encryption, table, data);
    if (encryption) {
        np_enhance((uint8_t *)data, sizeof(*data), table);
    }
    return active ? NPCLIENT_STATUS_OK : NPCLIENT_STATUS_DISABLED;
}

int WINAPI NP_GetParameter(int parameter, int value)
{
    (void)parameter;
    (void)value;
    return 0;
}

int WINAPI NP_GetSignature(OrientaNPClientSignature *signature)
{
    static const char dll_signature[] =
        "precise head tracking\n"
        " put your head into the game\n"
        " now go look around\n\n"
        " Copyright EyeControl Technologies";
    static const char app_signature[] =
        "hardware camera\n"
        " software processing data\n"
        " track user movement\n\n"
        " Copyright EyeControl Technologies";

    if (signature == NULL) {
        return 1;
    }
    trace_once(&trace_get_signature, "NP_GetSignature was called");
    memset(signature, 0, sizeof(*signature));
    memcpy(signature->DllSignature, dll_signature, sizeof(dll_signature));
    memcpy(signature->AppSignature, app_signature, sizeof(app_signature));
    return 0;
}

int WINAPI NP_QueryVersion(uint16_t *version)
{
    if (version == NULL) {
        return 1;
    }
    trace_once(&trace_query_version, "NP_QueryVersion was called");
    *version = 0x0500;
    return 0;
}

int WINAPI NP_ReCenter(void) { return 0; }

int WINAPI NP_RegisterProgramProfileID(uint16_t program_id)
{
    trace_once(&trace_register_program, "NP_RegisterProgramProfileID was called");
    if (!write_game_id(program_id)) {
        trace_once(&trace_mapping_failed, "Could not register the game ID in FT_SharedMem");
        return NPCLIENT_STATUS_DISABLED;
    }
    return NPCLIENT_STATUS_OK;
}

int WINAPI NP_RegisterWindowHandle(HWND window)
{
    (void)window;
    return 0;
}

int WINAPI NP_RequestData(uint16_t request)
{
    (void)request;
    return 0;
}

int WINAPI NP_SetParameter(int parameter, int value)
{
    (void)parameter;
    (void)value;
    return 0;
}

int WINAPI NP_StartCursor(void) { return 0; }
int WINAPI NP_StartDataTransmission(void) { return 0; }
int WINAPI NP_StopCursor(void) { return 0; }
int WINAPI NP_StopDataTransmission(void) { return 0; }
int WINAPI NP_UnregisterWindowHandle(void) { return 0; }

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        client_instance = instance;
        DisableThreadLibraryCalls(instance);
    } else if (reason == DLL_PROCESS_DETACH) {
        close_mapping_locked();
    }
    return TRUE;
}

#if defined(_MSC_VER) && defined(_M_IX86)
#pragma comment(linker, "/EXPORT:NP_GetData=_NP_GetData@4")
#endif
