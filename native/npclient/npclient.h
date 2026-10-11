#ifndef ORIENTA_NPCLIENT_H
#define ORIENTA_NPCLIENT_H

#include <stdint.h>
#include <windows.h>

#ifdef ORIENTA_NP_SMOKE_TEST
#define ORIENTA_NP_MAPPING_NAME L"Local\\Orienta_NPClient_Smoke"
#define ORIENTA_NP_MUTEX_NAME L"Local\\Orienta_NPClient_Smoke_Mutex"
#else
#define ORIENTA_NP_MAPPING_NAME L"FT_SharedMem"
#define ORIENTA_NP_MUTEX_NAME L"FT_Mutext"
#endif

#define NPCLIENT_STATUS_OK 0
#define NPCLIENT_STATUS_DISABLED 1
#define NPCLIENT_AXIS_MAX 16383

#pragma pack(push, 4)
typedef struct OrientaFTData {
    uint32_t DataID;
    int32_t CamWidth;
    int32_t CamHeight;
    float Yaw;
    float Pitch;
    float Roll;
    float X;
    float Y;
    float Z;
    float RawYaw;
    float RawPitch;
    float RawRoll;
    float RawX;
    float RawY;
    float RawZ;
    float X1;
    float Y1;
    float X2;
    float Y2;
    float X3;
    float Y3;
    float X4;
    float Y4;
} OrientaFTData;

typedef struct OrientaFTHeap {
    OrientaFTData data;
    int32_t GameID;
    uint8_t table[8];
    int32_t GameID2;
} OrientaFTHeap;

typedef struct OrientaNPData {
    int16_t status;
    int16_t frame;
    uint32_t checksum;
    float roll;
    float pitch;
    float yaw;
    float tx;
    float ty;
    float tz;
    float padding[9];
} OrientaNPData;

typedef struct OrientaNPClientSignature {
    char DllSignature[200];
    char AppSignature[200];
} OrientaNPClientSignature;
#pragma pack(pop)

#if defined(_MSC_VER)
#define ORIENTA_NP_EXPORT __declspec(dllexport)
#else
#define ORIENTA_NP_EXPORT __attribute__((dllexport))
#endif

#ifdef __cplusplus
extern "C" {
#endif

ORIENTA_NP_EXPORT int WINAPI NPPriv_ClientNotify(void);
ORIENTA_NP_EXPORT int WINAPI NPPriv_GetLastError(void);
ORIENTA_NP_EXPORT int WINAPI NPPriv_SetData(void);
ORIENTA_NP_EXPORT int WINAPI NPPriv_SetLastError(void);
ORIENTA_NP_EXPORT int WINAPI NPPriv_SetParameter(void);
ORIENTA_NP_EXPORT int WINAPI NPPriv_SetSignature(void);
ORIENTA_NP_EXPORT int WINAPI NPPriv_SetVersion(void);
ORIENTA_NP_EXPORT int WINAPI NP_GetData(OrientaNPData *data);
ORIENTA_NP_EXPORT int WINAPI NP_GetParameter(int parameter, int value);
ORIENTA_NP_EXPORT int WINAPI NP_GetSignature(OrientaNPClientSignature *signature);
ORIENTA_NP_EXPORT int WINAPI NP_QueryVersion(uint16_t *version);
ORIENTA_NP_EXPORT int WINAPI NP_ReCenter(void);
ORIENTA_NP_EXPORT int WINAPI NP_RegisterProgramProfileID(uint16_t program_id);
ORIENTA_NP_EXPORT int WINAPI NP_RegisterWindowHandle(HWND window);
ORIENTA_NP_EXPORT int WINAPI NP_RequestData(uint16_t request);
ORIENTA_NP_EXPORT int WINAPI NP_SetParameter(int parameter, int value);
ORIENTA_NP_EXPORT int WINAPI NP_StartCursor(void);
ORIENTA_NP_EXPORT int WINAPI NP_StartDataTransmission(void);
ORIENTA_NP_EXPORT int WINAPI NP_StopCursor(void);
ORIENTA_NP_EXPORT int WINAPI NP_StopDataTransmission(void);
ORIENTA_NP_EXPORT int WINAPI NP_UnregisterWindowHandle(void);

#ifdef __cplusplus
}
#endif

#endif
