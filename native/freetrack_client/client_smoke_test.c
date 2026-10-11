#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "freetrack_client.h"

typedef BOOL (WINAPI *GetDataFunction)(OrientaFreeTrackData *);
typedef void (WINAPI *ReportFunction)(int32_t);
typedef const char *(WINAPI *StringFunction)(void);

typedef struct OrientaFreeTrackHeap {
    OrientaFreeTrackData data;
    int32_t game_id;
    uint8_t response_table[8];
    int32_t game_id_2;
} OrientaFreeTrackHeap;

_Static_assert(sizeof(OrientaFreeTrackHeap) == 108, "FreeTrack heap layout mismatch");

typedef struct MutexProbe {
    const wchar_t *name;
    DWORD wait_result;
} MutexProbe;

static DWORD WINAPI probe_mutex_release(void *context)
{
    MutexProbe *probe = (MutexProbe *)context;
    HANDLE mutex = OpenMutexW(SYNCHRONIZE | MUTEX_MODIFY_STATE, FALSE, probe->name);
    if (mutex == NULL) {
        probe->wait_result = WAIT_FAILED;
        return 1;
    }

    probe->wait_result = WaitForSingleObject(mutex, 100);
    if (probe->wait_result == WAIT_OBJECT_0
        || probe->wait_result == WAIT_ABANDONED) {
        ReleaseMutex(mutex);
    }
    CloseHandle(mutex);
    return 0;
}

static int fail(const char *message)
{
    fprintf(stderr, "FreeTrack client smoke test failed: %s (error %lu)\n",
            message, (unsigned long)GetLastError());
    return 1;
}

int main(int argc, char **argv)
{
    HANDLE map_handle = NULL;
    HANDLE mutex_handle = NULL;
    void *view = NULL;
    HMODULE client = NULL;
    HANDLE probe_thread = NULL;
    GetDataFunction get_data;
    ReportFunction report_name;
    ReportFunction report_id;
    StringFunction get_version;
    StringFunction get_provider;
    OrientaFreeTrackData pose;
    OrientaFreeTrackHeap *heap;
    DWORD wait_result;
    int result = 1;

    if (argc != 2) {
        fprintf(stderr, "Usage: %s <FreeTrackClient DLL path>\n", argv[0]);
        return 2;
    }

    SetLastError(ERROR_SUCCESS);
    map_handle = CreateFileMappingW(
        INVALID_HANDLE_VALUE,
        NULL,
        PAGE_READWRITE,
        0,
        sizeof(OrientaFreeTrackHeap),
        ORIENTA_FT_MAPPING_NAME
    );
    if (map_handle == NULL) {
        return fail("could not create shared memory");
    }
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
        fail("FT_SharedMem already exists; close other trackers and games");
        goto cleanup;
    }

    view = MapViewOfFile(
        map_handle,
        FILE_MAP_ALL_ACCESS,
        0,
        0,
        sizeof(OrientaFreeTrackHeap)
    );
    if (view == NULL) {
        fail("could not map shared memory");
        goto cleanup;
    }
    heap = (OrientaFreeTrackHeap *)view;
    memset(heap, 0, sizeof(*heap));

    mutex_handle = CreateMutexW(NULL, FALSE, ORIENTA_FT_MUTEX_NAME);
    if (mutex_handle == NULL) {
        fail("could not create the FreeTrack mutex");
        goto cleanup;
    }

    wait_result = WaitForSingleObject(mutex_handle, 1000);
    if (wait_result != WAIT_OBJECT_0) {
        fail("could not initialize the FreeTrack frame");
        goto cleanup;
    }
    heap->data.DataID = 1;
    heap->data.CamWidth = 100;
    heap->data.CamHeight = 250;
    heap->data.Yaw = 0.25f;
    heap->data.Pitch = -0.5f;
    heap->data.Roll = 0.75f;
    ReleaseMutex(mutex_handle);

    client = LoadLibraryA(argv[1]);
    if (client == NULL) {
        fail("could not load the client DLL");
        goto cleanup;
    }

    get_data = (GetDataFunction)GetProcAddress(client, "FTGetData");
    report_name = (ReportFunction)GetProcAddress(client, "FTReportName");
    report_id = (ReportFunction)GetProcAddress(client, "FTReportID");
    get_version = (StringFunction)GetProcAddress(client, "FTGetDllVersion");
    get_provider = (StringFunction)GetProcAddress(client, "FTProvider");
    if (get_data == NULL) {
        fail("FTGetData export is missing");
        goto cleanup;
    }
    if (report_name == NULL) {
        fail("FTReportName export is missing");
        goto cleanup;
    }
    if (report_id == NULL) {
        fail("FTReportID export is missing");
        goto cleanup;
    }
    if (get_version == NULL) {
        fail("FTGetDllVersion export is missing");
        goto cleanup;
    }
    if (get_provider == NULL) {
        fail("FTProvider export is missing");
        goto cleanup;
    }

    memset(&pose, 0, sizeof(pose));
    if (!get_data(&pose)
        || pose.DataID != 1
        || pose.CamWidth != 100
        || pose.CamHeight != 250
        || pose.Yaw != 0.25f
        || pose.Pitch != -0.5f
        || pose.Roll != 0.75f) {
        fail("first published pose was not returned correctly");
        goto cleanup;
    }

    if (!get_data(&pose) || pose.DataID != 1) {
        fail("successful read of an unchanged frame was not reported");
        goto cleanup;
    }

    {
        MutexProbe probe = {ORIENTA_FT_MUTEX_NAME, WAIT_FAILED};
        probe_thread = CreateThread(NULL, 0, probe_mutex_release, &probe, 0, NULL);
        if (probe_thread == NULL) {
            fail("could not start the cross-thread mutex probe");
            goto cleanup;
        }
        WaitForSingleObject(probe_thread, INFINITE);
        CloseHandle(probe_thread);
        probe_thread = NULL;
        if (probe.wait_result != WAIT_OBJECT_0
            && probe.wait_result != WAIT_ABANDONED) {
            fail("FreeTrack client did not release the mutex after reading");
            goto cleanup;
        }
    }

    wait_result = WaitForSingleObject(mutex_handle, 1000);
    if (wait_result != WAIT_OBJECT_0) {
        fail("could not acquire the publisher mutex");
        goto cleanup;
    }
    heap->data.DataID = 2;
    heap->data.Yaw = -1.25f;
    ReleaseMutex(mutex_handle);

    if (!get_data(&pose) || pose.DataID != 2 || pose.Yaw != -1.25f) {
        fail("updated pose was not returned correctly");
        goto cleanup;
    }

    report_name(0);
    report_id(0);
    if (get_version() == NULL || get_provider() == NULL
        || strcmp(get_provider(), "FreeTrack") != 0) {
        fail("client metadata does not identify the FreeTrack provider");
        goto cleanup;
    }

    printf("FreeTrack client ABI test passed (%s, provider %s).\n",
           get_version(), get_provider());
    result = 0;

cleanup:
    if (probe_thread != NULL) {
        WaitForSingleObject(probe_thread, INFINITE);
        CloseHandle(probe_thread);
    }
    if (client != NULL) {
        FreeLibrary(client);
    }
    if (mutex_handle != NULL) {
        CloseHandle(mutex_handle);
    }
    if (view != NULL) {
        UnmapViewOfFile(view);
    }
    if (map_handle != NULL) {
        CloseHandle(map_handle);
    }
    return result;
}
