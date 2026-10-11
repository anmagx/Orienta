#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "npclient.h"

typedef int (WINAPI *GetDataFunction)(OrientaNPData *);
typedef int (WINAPI *RegisterProgramFunction)(uint16_t);
typedef int (WINAPI *QueryVersionFunction)(uint16_t *);
typedef int (WINAPI *GetSignatureFunction)(OrientaNPClientSignature *);

typedef struct MutexProbe {
    DWORD wait_result;
} MutexProbe;

static DWORD WINAPI probe_mutex(void *context)
{
    MutexProbe *probe = (MutexProbe *)context;
    HANDLE mutex = OpenMutexW(
        SYNCHRONIZE | MUTEX_MODIFY_STATE,
        FALSE,
        ORIENTA_NP_MUTEX_NAME
    );
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
    fprintf(stderr, "NPClient smoke test failed: %s (error %lu)\n",
            message, (unsigned long)GetLastError());
    return 1;
}

static int near_value(float actual, float expected)
{
    return fabsf(actual - expected) < 0.1f;
}

static void decode_pose(OrientaNPData *data, const uint8_t table[8])
{
    uint8_t *bytes = (uint8_t *)data;
    uint8_t rolling = 0x88;
    size_t remaining = sizeof(*data);
    size_t table_index = 0;

    while (remaining != 0) {
        size_t index = --remaining;
        uint8_t value = bytes[index] ^ table[table_index] ^ rolling;
        bytes[index] = value;
        rolling = (uint8_t)(rolling + index + value);
        table_index = (table_index + 1) % 8;
    }
}

int main(int argc, char **argv)
{
    static const char *required_exports[] = {
        "NPPriv_ClientNotify",
        "NPPriv_GetLastError",
        "NPPriv_SetData",
        "NPPriv_SetLastError",
        "NPPriv_SetParameter",
        "NPPriv_SetSignature",
        "NPPriv_SetVersion",
        "NP_GetData",
        "NP_GetParameter",
        "NP_GetSignature",
        "NP_QueryVersion",
        "NP_ReCenter",
        "NP_RegisterProgramProfileID",
        "NP_RegisterWindowHandle",
        "NP_RequestData",
        "NP_SetParameter",
        "NP_StartCursor",
        "NP_StartDataTransmission",
        "NP_StopCursor",
        "NP_StopDataTransmission",
        "NP_UnregisterWindowHandle",
    };
    HANDLE mapping_handle = NULL;
    HANDLE mutex_handle = NULL;
    HANDLE probe_thread = NULL;
    HMODULE client = NULL;
    OrientaFTHeap *heap = NULL;
    GetDataFunction get_data;
    RegisterProgramFunction register_program;
    QueryVersionFunction query_version;
    GetSignatureFunction get_signature;
    OrientaNPData data;
    OrientaNPClientSignature signature;
    MutexProbe probe = {WAIT_FAILED};
    uint16_t version = 0;
    DWORD wait_result;
    size_t index;
    int result = 1;

    if (argc != 2) {
        fprintf(stderr, "Usage: %s <NPClient DLL path>\n", argv[0]);
        return 2;
    }

    SetLastError(ERROR_SUCCESS);
    mapping_handle = CreateFileMappingW(
        INVALID_HANDLE_VALUE,
        NULL,
        PAGE_READWRITE,
        0,
        sizeof(OrientaFTHeap),
        ORIENTA_NP_MAPPING_NAME
    );
    if (mapping_handle == NULL) {
        return fail("could not create FreeTrack shared memory");
    }
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
        fail("NPClient smoke-test mapping already exists");
        goto cleanup;
    }

    heap = (OrientaFTHeap *)MapViewOfFile(
        mapping_handle,
        FILE_MAP_ALL_ACCESS,
        0,
        0,
        sizeof(OrientaFTHeap)
    );
    if (heap == NULL) {
        fail("could not map FreeTrack shared memory");
        goto cleanup;
    }
    memset(heap, 0, sizeof(*heap));

    mutex_handle = CreateMutexW(NULL, FALSE, ORIENTA_NP_MUTEX_NAME);
    if (mutex_handle == NULL) {
        fail("could not create the FreeTrack mutex");
        goto cleanup;
    }
    wait_result = WaitForSingleObject(mutex_handle, 1000);
    if (wait_result != WAIT_OBJECT_0) {
        fail("could not initialize the shared pose");
        goto cleanup;
    }
    heap->data.DataID = 1;
    heap->data.CamWidth = 100;
    heap->data.CamHeight = 250;
    heap->data.Yaw = 1.57079632679f;
    heap->data.Pitch = -0.78539816339f;
    heap->data.Roll = 0.5f;
    heap->data.X = 250.0f;
    heap->data.Y = -125.0f;
    heap->data.Z = 500.0f;
    ReleaseMutex(mutex_handle);

    client = LoadLibraryA(argv[1]);
    if (client == NULL) {
        fail("could not load NPClient DLL");
        goto cleanup;
    }
    for (index = 0; index < sizeof(required_exports) / sizeof(required_exports[0]); index++) {
        if (GetProcAddress(client, required_exports[index]) == NULL) {
            fprintf(stderr, "Missing NPClient export: %s\n", required_exports[index]);
            goto cleanup;
        }
    }

    get_data = (GetDataFunction)GetProcAddress(client, "NP_GetData");
    register_program = (RegisterProgramFunction)GetProcAddress(
        client,
        "NP_RegisterProgramProfileID"
    );
    query_version = (QueryVersionFunction)GetProcAddress(client, "NP_QueryVersion");
    get_signature = (GetSignatureFunction)GetProcAddress(client, "NP_GetSignature");

    if (query_version(&version) != 0 || version != 0x0500) {
        fail("NP_QueryVersion returned an unexpected version");
        goto cleanup;
    }
    if (get_signature(&signature) != 0
        || strcmp(
            signature.DllSignature,
            "precise head tracking\n"
            " put your head into the game\n"
            " now go look around\n\n"
            " Copyright EyeControl Technologies"
        ) != 0
        || strcmp(
            signature.AppSignature,
            "hardware camera\n"
            " software processing data\n"
            " track user movement\n\n"
            " Copyright EyeControl Technologies"
        ) != 0) {
        fail("NP_GetSignature did not return the LinuxTrack compatibility payload");
        goto cleanup;
    }
    if (register_program(1901) != 0 || heap->GameID != 1901) {
        fail("NP_RegisterProgramProfileID did not update shared state");
        goto cleanup;
    }

    memset(&data, 0, sizeof(data));
    if (get_data(&data) != NPCLIENT_STATUS_OK
        || data.status != NPCLIENT_STATUS_OK
        || !near_value(data.yaw, 8191.5f)
        || !near_value(data.pitch, -4095.75f)
        || !near_value(
            data.roll,
            (float)(0.5 * NPCLIENT_AXIS_MAX / 3.14159265358979323846)
        )
        || !near_value(data.tx, 8191.5f)
        || !near_value(data.ty, -4095.75f)
        || !near_value(data.tz, 16383.0f)
        || data.checksum != 0xf3cdf96fu) {
        fail("NP_GetData did not return the scaled FreeTrack pose");
        goto cleanup;
    }

    probe_thread = CreateThread(NULL, 0, probe_mutex, &probe, 0, NULL);
    if (probe_thread == NULL) {
        fail("could not create the shared-mutex probe thread");
        goto cleanup;
    }
    WaitForSingleObject(probe_thread, INFINITE);
    CloseHandle(probe_thread);
    probe_thread = NULL;
    if (probe.wait_result != WAIT_OBJECT_0
        && probe.wait_result != WAIT_ABANDONED) {
        fail("NP_GetData did not release the FreeTrack mutex");
        goto cleanup;
    }

    wait_result = WaitForSingleObject(mutex_handle, 1000);
    if (wait_result != WAIT_OBJECT_0) {
        fail("could not update the published pose");
        goto cleanup;
    }
    heap->data.Yaw = 0.25f;
    heap->data.Pitch = 0.125f;
    heap->data.Roll = -0.5f;
    heap->data.X = -50.0f;
    ReleaseMutex(mutex_handle);
    if (get_data(&data) != NPCLIENT_STATUS_OK
        || !near_value(
            data.yaw,
            (float)(0.25 * NPCLIENT_AXIS_MAX / 3.14159265358979323846)
        )
        || !near_value(
            data.pitch,
            (float)(0.125 * NPCLIENT_AXIS_MAX / 3.14159265358979323846)
        )
        || !near_value(
            data.roll,
            (float)(-0.5 * NPCLIENT_AXIS_MAX / 3.14159265358979323846)
        )
        || !near_value(data.tx, -1638.3f)) {
        fail("NP_GetData did not return an updated shared-memory pose");
        goto cleanup;
    }

    wait_result = WaitForSingleObject(mutex_handle, 1000);
    if (wait_result != WAIT_OBJECT_0) {
        fail("could not clear the published pose");
        goto cleanup;
    }
    memset(&heap->data, 0, sizeof(heap->data));
    heap->data.CamWidth = 100;
    heap->data.CamHeight = 250;
    ReleaseMutex(mutex_handle);
    if (get_data(&data) != NPCLIENT_STATUS_OK
        || data.status != NPCLIENT_STATUS_OK
        || data.yaw != 0.0f || data.pitch != 0.0f || data.roll != 0.0f) {
        fail("a valid centered pose was reported as disabled");
        goto cleanup;
    }

    WaitForSingleObject(mutex_handle, INFINITE);
    heap->data.CamWidth = 0;
    heap->data.CamHeight = 0;
    ReleaseMutex(mutex_handle);
    if (get_data(&data) != NPCLIENT_STATUS_DISABLED
        || data.status != NPCLIENT_STATUS_DISABLED) {
        fail("uninitialized shared memory was not reported as disabled");
        goto cleanup;
    }

    {
        static const uint8_t dcs_table[8] = {
            0x5f, 0xd1, 0xdc, 0xb6, 0x65, 0x2f, 0x57, 0x5a
        };
        double unencrypted_yaw = 0.25 * NPCLIENT_AXIS_MAX
            / 3.14159265358979323846;

        wait_result = WaitForSingleObject(mutex_handle, 1000);
        if (wait_result != WAIT_OBJECT_0) {
            fail("could not prepare the game response-table test");
            goto cleanup;
        }
        heap->GameID = 1003;
        heap->GameID2 = 1003;
        heap->data.CamWidth = 100;
        heap->data.CamHeight = 250;
        memcpy(heap->table, dcs_table, sizeof(dcs_table));
        heap->data.Yaw = 0.25f;
        heap->data.Pitch = 0.125f;
        heap->data.Roll = -0.5f;
        ReleaseMutex(mutex_handle);

        if (get_data(&data) != NPCLIENT_STATUS_OK) {
            fail("NP_GetData did not apply the game response-table transform");
            goto cleanup;
        }
        decode_pose(&data, dcs_table);
        if (data.status != NPCLIENT_STATUS_OK
            || !near_value(data.yaw, (float)unencrypted_yaw)
            || !near_value(data.pitch, (float)(unencrypted_yaw / 2))
            || !near_value(data.roll, (float)(-2 * unencrypted_yaw))) {
            fail("encrypted pose did not decode back to the published orientation");
            goto cleanup;
        }
    }

    FreeLibrary(client);
    client = NULL;
    UnmapViewOfFile(heap);
    heap = NULL;
    CloseHandle(mapping_handle);
    mapping_handle = NULL;
    CloseHandle(mutex_handle);
    mutex_handle = NULL;
    {
        HANDLE retained = OpenFileMappingW(FILE_MAP_READ, FALSE, ORIENTA_NP_MAPPING_NAME);
        if (retained != NULL) {
            CloseHandle(retained);
            fail("NPClient DLL retained shared-memory handles after unloading");
            goto cleanup;
        }
        if (GetLastError() != ERROR_FILE_NOT_FOUND) {
            fail("could not verify shared-memory handle cleanup");
            goto cleanup;
        }
    }

    printf("NPClient ABI and shared-pose smoke tests passed (version 0x%04x).\n",
           version);
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
    if (heap != NULL) {
        UnmapViewOfFile(heap);
    }
    if (mapping_handle != NULL) {
        CloseHandle(mapping_handle);
    }
    return result;
}
