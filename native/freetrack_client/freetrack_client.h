#ifndef ORIENTA_FREETRACK_CLIENT_H
#define ORIENTA_FREETRACK_CLIENT_H

#include <stdint.h>
#include <windows.h>

#ifdef ORIENTA_FT_SMOKE_TEST
#define ORIENTA_FT_MAPPING_NAME L"Local\\Orienta_FreeTrackClient_Smoke"
#define ORIENTA_FT_MUTEX_NAME L"Local\\Orienta_FreeTrackClient_Smoke_Mutex"
#else
#define ORIENTA_FT_MAPPING_NAME L"FT_SharedMem"
#define ORIENTA_FT_MUTEX_NAME L"FT_Mutext"
#endif

#pragma pack(push, 4)
typedef struct OrientaFreeTrackData {
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
} OrientaFreeTrackData;
#pragma pack(pop)

#if defined(_MSC_VER)
#define ORIENTA_FT_EXPORT __declspec(dllexport)
#else
#define ORIENTA_FT_EXPORT __attribute__((dllexport))
#endif

#ifdef __cplusplus
extern "C" {
#endif

ORIENTA_FT_EXPORT BOOL WINAPI FTGetData(OrientaFreeTrackData *data);
ORIENTA_FT_EXPORT void WINAPI FTReportName(int32_t game_identifier);
ORIENTA_FT_EXPORT void WINAPI FTReportID(int32_t game_identifier);
ORIENTA_FT_EXPORT const char *WINAPI FTGetDllVersion(void);
ORIENTA_FT_EXPORT const char *WINAPI FTProvider(void);

#ifdef __cplusplus
}
#endif

#endif
