@echo off
setlocal
set "PYTHON=%~dp0dev\.venv\Scripts\python.exe"
set "SOURCE=%~dp0..\native\freetrack_client"
set "NPCLIENT_SOURCE=%~dp0..\native\npclient"
set "OUTPUT=%~dp0dist\freetrack_client"
set "NPCLIENT_OUTPUT=%~dp0dist\npclient"
set "TEST_OUTPUT=%~dp0work\freetrack_client"
set "NPCLIENT_TEST_OUTPUT=%~dp0work\npclient"

if not exist "%PYTHON%" (
    echo Virtual environment not found. Run build\dev\setup_dev.bat first.
    exit /b 1
)

if not exist "%OUTPUT%" mkdir "%OUTPUT%" || exit /b 1
if not exist "%NPCLIENT_OUTPUT%" mkdir "%NPCLIENT_OUTPUT%" || exit /b 1
if not exist "%TEST_OUTPUT%" mkdir "%TEST_OUTPUT%" || exit /b 1
if not exist "%NPCLIENT_TEST_OUTPUT%" mkdir "%NPCLIENT_TEST_OUTPUT%" || exit /b 1

"%PYTHON%" -m ziglang cc -target x86_64-windows-gnu -std=c11 -O2 -shared -o "%OUTPUT%\FreeTrackClient64.dll" "%SOURCE%\freetrack_client.c" || exit /b 1
"%PYTHON%" -m ziglang cc -target x86-windows-gnu -std=c11 -O2 -shared -o "%OUTPUT%\FreeTrackClient.dll" "%SOURCE%\freetrack_client.c" "%SOURCE%\freetrack_client_x86.def" || exit /b 1

"%PYTHON%" -m ziglang cc -target x86_64-windows-gnu -std=c11 -O2 -DORIENTA_FT_SMOKE_TEST -shared -o "%TEST_OUTPUT%\FreeTrackClientSmoke64.dll" "%SOURCE%\freetrack_client.c" || exit /b 1
"%PYTHON%" -m ziglang cc -target x86-windows-gnu -std=c11 -O2 -DORIENTA_FT_SMOKE_TEST -shared -o "%TEST_OUTPUT%\FreeTrackClientSmoke.dll" "%SOURCE%\freetrack_client.c" "%SOURCE%\freetrack_client_x86.def" || exit /b 1
"%PYTHON%" -m ziglang cc -target x86_64-windows-gnu -std=c11 -O2 -DORIENTA_FT_SMOKE_TEST -o "%TEST_OUTPUT%\client_smoke_test64.exe" "%SOURCE%\client_smoke_test.c" || exit /b 1
"%PYTHON%" -m ziglang cc -target x86-windows-gnu -std=c11 -O2 -DORIENTA_FT_SMOKE_TEST -o "%TEST_OUTPUT%\client_smoke_test.exe" "%SOURCE%\client_smoke_test.c" || exit /b 1

"%TEST_OUTPUT%\client_smoke_test64.exe" "%TEST_OUTPUT%\FreeTrackClientSmoke64.dll" || exit /b 1
"%TEST_OUTPUT%\client_smoke_test.exe" "%TEST_OUTPUT%\FreeTrackClientSmoke.dll" || exit /b 1

"%PYTHON%" -m ziglang cc -target x86_64-windows-gnu -std=c11 -O2 -shared -o "%NPCLIENT_OUTPUT%\NPClient64.dll" "%NPCLIENT_SOURCE%\npclient.c" || exit /b 1
"%PYTHON%" -m ziglang cc -target x86-windows-gnu -std=c11 -O2 -shared -o "%NPCLIENT_OUTPUT%\NPClient.dll" "%NPCLIENT_SOURCE%\npclient.c" "%NPCLIENT_SOURCE%\npclient_x86.def" || exit /b 1

"%PYTHON%" -m ziglang cc -target x86_64-windows-gnu -std=c11 -O2 -DORIENTA_NP_SMOKE_TEST -shared -o "%NPCLIENT_TEST_OUTPUT%\NPClientSmoke64.dll" "%NPCLIENT_SOURCE%\npclient.c" || exit /b 1
"%PYTHON%" -m ziglang cc -target x86-windows-gnu -std=c11 -O2 -DORIENTA_NP_SMOKE_TEST -shared -o "%NPCLIENT_TEST_OUTPUT%\NPClientSmoke.dll" "%NPCLIENT_SOURCE%\npclient.c" "%NPCLIENT_SOURCE%\npclient_x86.def" || exit /b 1
"%PYTHON%" -m ziglang cc -target x86_64-windows-gnu -std=c11 -O2 -DORIENTA_NP_SMOKE_TEST -o "%NPCLIENT_TEST_OUTPUT%\client_smoke_test64.exe" "%NPCLIENT_SOURCE%\client_smoke_test.c" || exit /b 1
"%PYTHON%" -m ziglang cc -target x86-windows-gnu -std=c11 -O2 -DORIENTA_NP_SMOKE_TEST -o "%NPCLIENT_TEST_OUTPUT%\client_smoke_test.exe" "%NPCLIENT_SOURCE%\client_smoke_test.c" || exit /b 1

"%NPCLIENT_TEST_OUTPUT%\client_smoke_test64.exe" "%NPCLIENT_TEST_OUTPUT%\NPClientSmoke64.dll" || exit /b 1
"%NPCLIENT_TEST_OUTPUT%\client_smoke_test.exe" "%NPCLIENT_TEST_OUTPUT%\NPClientSmoke.dll" || exit /b 1

echo FreeTrack and NPClient game-client DLLs built and ABI-tested.
