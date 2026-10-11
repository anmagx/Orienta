#define WIN32_LEAN_AND_MEAN
/* Independently authored FreeTrack client; it reads poses from Orienta's publisher. */
#include <windows.h>
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <wchar.h>

#include "freetrack_client.h"

_Static_assert(sizeof(OrientaFreeTrackData) == 92, "FreeTrack data ABI size mismatch");
_Static_assert(offsetof(OrientaFreeTrackData, Yaw) == 12, "FreeTrack pose offset mismatch");
_Static_assert(offsetof(OrientaFreeTrackData, RawYaw) == 36, "FreeTrack raw-pose offset mismatch");

static SRWLOCK state_lock = SRWLOCK_INIT;
static HANDLE mapping_handle;
static HANDLE mutex_handle;
static const OrientaFreeTrackData *shared_data;
static HINSTANCE client_instance;
static volatile LONG trace_get_data;
static volatile LONG trace_provider;
static volatile LONG trace_version;
static volatile LONG trace_mapping_failed;
static volatile LONG trace_mutex_failed;
static volatile LONG trace_mapping_opened;
static volatile LONG trace_first_frame;

static void trace_once(volatile LONG *written, const char *message)
{
    WCHAR module_path[MAX_PATH];
    WCHAR *separator;
    const WCHAR filename[] = L"FreeTrackClient.log";
    HANDLE file;
    DWORD bytes_written;
    DWORD path_length;
    size_t base_length;
    size_t filename_length = sizeof(filename);

    if (InterlockedCompareExchange(written, 1, 0) != 0) {
        return;
    }

    module_path[0] = L'\0';
    path_length = GetModuleFileNameW(client_instance, module_path, MAX_PATH);
    if (path_length == 0 || path_length >= MAX_PATH) {
        OutputDebugStringA("Orienta FreeTrack: could not locate its module path\n");
        return;
    }
    separator = wcsrchr(module_path, L'\\');
    if (separator == NULL) {
        separator = wcsrchr(module_path, L'/');
    }
    if (separator == NULL) {
        return;
    }

    base_length = (size_t)(separator - module_path) + 1;
    if (base_length + filename_length / sizeof(filename[0]) > MAX_PATH) {
        return;
    }
    memcpy(module_path + base_length, filename, filename_length);

    file = CreateFileW(
        module_path,
        FILE_APPEND_DATA,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        NULL,
        OPEN_ALWAYS,
        FILE_ATTRIBUTE_NORMAL,
        NULL
    );
    if (file == INVALID_HANDLE_VALUE) {
        OutputDebugStringA("Orienta FreeTrack: could not open FreeTrackClient.log\n");
        return;
    }
    if (!WriteFile(file, message, (DWORD)strlen(message), &bytes_written, NULL)
        || !WriteFile(file, "\r\n", 2, &bytes_written, NULL)) {
        OutputDebugStringA("Orienta FreeTrack: could not write FreeTrackClient.log\n");
    }
    CloseHandle(file);
}

static void close_shared_memory(void)
{
    if (shared_data != NULL) {
        UnmapViewOfFile(shared_data);
        shared_data = NULL;
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

static BOOL connect_shared_memory(void)
{
    if (shared_data != NULL && mutex_handle != NULL && mapping_handle != NULL) {
        return TRUE;
    }

    close_shared_memory();

    mapping_handle = OpenFileMappingW(FILE_MAP_READ, FALSE, ORIENTA_FT_MAPPING_NAME);
    if (mapping_handle == NULL) {
        trace_once(&trace_mapping_failed, "FT_SharedMem could not be opened");
        return FALSE;
    }

    shared_data = (const OrientaFreeTrackData *)MapViewOfFile(
        mapping_handle,
        FILE_MAP_READ,
        0,
        0,
        sizeof(*shared_data)
    );
    if (shared_data == NULL) {
        trace_once(&trace_mapping_failed, "FT_SharedMem could not be mapped");
        close_shared_memory();
        return FALSE;
    }

    mutex_handle = OpenMutexW(
        SYNCHRONIZE | MUTEX_MODIFY_STATE,
        FALSE,
        ORIENTA_FT_MUTEX_NAME
    );
    if (mutex_handle == NULL) {
        trace_once(&trace_mutex_failed, "FT_Mutext could not be opened");
        close_shared_memory();
        return FALSE;
    }
    trace_once(&trace_mapping_opened, "FT_SharedMem and FT_Mutext opened");
    return TRUE;
}

static BOOL pose_is_finite(const OrientaFreeTrackData *data)
{
    return isfinite(data->Yaw)
        && isfinite(data->Pitch)
        && isfinite(data->Roll)
        && isfinite(data->X)
        && isfinite(data->Y)
        && isfinite(data->Z);
}

BOOL WINAPI FTGetData(OrientaFreeTrackData *data)
{
    OrientaFreeTrackData sample;
    DWORD wait_result;
    BOOL read_succeeded = FALSE;

    if (data == NULL) {
        return FALSE;
    }

    trace_once(&trace_get_data, "FTGetData was called");
    AcquireSRWLockExclusive(&state_lock);
    if (!connect_shared_memory()) {
        ReleaseSRWLockExclusive(&state_lock);
        return FALSE;
    }

    wait_result = WaitForSingleObject(mutex_handle, 2);
    if (wait_result != WAIT_OBJECT_0 && wait_result != WAIT_ABANDONED) {
        if (wait_result == WAIT_FAILED) {
            close_shared_memory();
        }
        ReleaseSRWLockExclusive(&state_lock);
        return FALSE;
    }

    memcpy(&sample, shared_data, sizeof(sample));
    if (!ReleaseMutex(mutex_handle)) {
        trace_once(&trace_mutex_failed, "FT_Mutext could not be released");
        close_shared_memory();
        ReleaseSRWLockExclusive(&state_lock);
        return FALSE;
    }

    if (pose_is_finite(&sample)) {
        *data = sample;
        read_succeeded = TRUE;
        trace_once(&trace_first_frame, "FTGetData returned a valid frame");
    }

    ReleaseSRWLockExclusive(&state_lock);
    return read_succeeded;
}

void WINAPI FTReportName(int32_t game_identifier)
{
    (void)game_identifier;
    trace_once(&trace_provider, "FTReportName was called");
}

void WINAPI FTReportID(int32_t game_identifier)
{
    (void)game_identifier;
    trace_once(&trace_provider, "FTReportID was called");
}

const char *WINAPI FTGetDllVersion(void)
{
    trace_once(&trace_version, "FTGetDllVersion was called");
    return "1.0.0.0";
}

const char *WINAPI FTProvider(void)
{
    trace_once(&trace_provider, "FTProvider was called");
    return "FreeTrack";
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        client_instance = instance;
        DisableThreadLibraryCalls(instance);
    } else if (reason == DLL_PROCESS_DETACH) {
        close_shared_memory();
    }
    return TRUE;
}

#if defined(_MSC_VER) && defined(_M_IX86)
#pragma comment(linker, "/EXPORT:FTGetData=_FTGetData@4")
#pragma comment(linker, "/EXPORT:FTReportName=_FTReportName@4")
#pragma comment(linker, "/EXPORT:FTReportID=_FTReportID@4")
#pragma comment(linker, "/EXPORT:FTGetDllVersion=_FTGetDllVersion@0")
#pragma comment(linker, "/EXPORT:FTProvider=_FTProvider@0")
#endif
