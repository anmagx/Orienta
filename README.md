# Orienta

![Orienta logo](src/img/orienta_banner.png)


Orienta is a 3-DOF head-tracking application for Windows.
It reads accelerometer and gyroscope data from a serial-connected IMU, estimates
yaw, pitch, and roll with a complementary filter, and can send orientation data
to opentrack over UDP or directly to compatible games using FreeTrack 2.0 or
NPClient.

![Orienta logo](src/img/orienta_screenshot.png)

## Why? 

The idea is to be able to track your head movements in games. Some huge names in the business are trackIR and Tobii, but their complete kits can be quite expensive, making the cost of entry into immersive simulation games quite high. 

While you can build your own kit, you would typically need an IR-sensitive camera with a high frame rate, carefully consider camera distance to head, design a clip for holding the 3 LEDs - it's possible, but can be tricky to get good results with. 

The space between these options is where Orienta could work. While there are some shortcomings to using an IMU, the arguments for it are compelling: You can build your own for probably under 15$, are completely free of any camera or viewing angle calculations, it works the same whether your room is brightly lit or pitch black, and can track your head movements at over 240hz, giving you a buttery smooth experience. 

The only diy required is soldering up your sensor and your board and mounting it to your headset. Example included further below. 

## Features

- Real-time serial IMU acquisition with support for a wide range of sensors
- Quaternion-based complementary-filter implementation
- Gyro and accelerometer bias calibration and correction
- Optional, off-by-default stationary yaw gyro-bias adaptation
- Smooth autocentering when stationary and near center
- UDP output for opentrack, direct FreeTrack 2.0 output, and NPClient output
  for TrackIR-compatible games
- PyQt5 interface with lightand dark themes. 
- Pygame-supported global keyboard and gamepad shortcuts


## Installation

### Install script for developing

`build\dev\setup_dev.bat` creates a virtual environment, installs application
and native-build dependencies, builds/tests the x86 and x64 FreeTrack and
NPClient clients, and creates a launcher and optional desktop shortcut.

### Manual installation

```powershell
git clone https://github.com/anmagx/Orienta
cd Orienta\build\dev\
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
cd ..\..\
build\build_game_clients.bat
python orienta.py
```

### Portable executable

After running `build\dev\setup_dev.bat`, build a single-file, windowed executable with:

```powershell
build\build_exe.bat
```

The script installs the pinned build requirements, builds the x86 and x64
FreeTrack and NPClient client DLLs using
`build\build_game_clients.bat`, and creates
`build\dist\Orienta-<version>.exe` from `build\orienta.spec`, using the
`APP_VERSION` in `src\config\config.py` for both the filename and Windows
file/product version. The executable includes the application icon, logo,
themes, FreeTrack and NPClient game-client DLLs, and the FaceTrackNoIR game
response catalog with its permission notice. Preferences, logs, and staged
client files are stored outside the executable, as described in
[User data](#user-data).

### Windows installer

The installer defaults to `C:\Programs\Orienta`, with a directory page so the
user can choose another location. It stages both architectures of the
FreeTrack and NPClient DLLs under `%LOCALAPPDATA%\Orienta` and offers Start
Menu and desktop shortcut choices. Uninstall restores per-user game-client registrations, then removes `%LOCALAPPDATA%\Orienta`,
including preferences and logs.

## Requirements

### Software

- Windows Operating System
- Python
- PySerial
- PyQt5
- Keyboard
- Pygame
- PyInstaller
- Zig (installed automatically from the pinned build requirements)

### Hardware and serial format

Any device that emits numeric CSV values on a serial port is supported.
The bundled firmware is an ESP32-C3 Super Mini example using a FastIMU-supported
MPU6500, configured to run at 500000 baud. The sensor data must be structured as follows:

```text
time_seconds,accel_x,accel_y,accel_z,gyro_x,gyro_y,gyro_z
```

Accelerometer values are expected in g units and gyroscope values in degrees
per second. Configure the serial port and baud rate in the application to
match your device.

Magnetometers are currently not supported. 

### MPU6500 tracking sketch

[MPU6500_tracking](arduino/MPU6500_tracking/MPU6500_tracking.ino) is a standalone
sketch using FastIMU 1.3.0's rate, filter, and sample-timestamp APIs. Open it
from its own folder in the Arduino IDE; the original sketches are unchanged.
It uses 500000 baud, a 1000 Hz internal accel/gyro rate, 250 Hz polling/output,
a +/-1000 degrees/second gyro range, and a +/-2 g accelerometer range. The
hardware low-pass filters remain at 42 Hz for gyro and 41 Hz for accel.

It does not perform startup gyro or accelerometer calibration. After warm-up,
place the sensor on a level surface with its +Z axis facing up, then trigger
Orienta's **Sensor Bias Calibration** and keep the sensor still until it
completes. This estimates gyro offsets and accelerometer offsets for that
known-level pose. It does not estimate accelerometer scale or mounting
orientation. The calibration is kept in memory for the current session and stored in preferences. 

The sketch uses I2C SDA pin 6 and SCL pin 7 at address `0x68`, configured by
`I2C_SDA_PIN` and `I2C_SCL_PIN` near the top of the sketch. Change those
constants to match your board wiring. It emits the same seven-field CSV format,
using FastIMU read timestamps extended across microsecond-counter rollover, and
skips unchanged samples.
These timestamps mark register reads, not hardware conversion times. Initialization
or configuration errors, or 100 ms without fresh synchronized samples, print an
error and stop acquisition until reset. FastIMU does not expose I2C transfer
status through `update()`, so this freshness check cannot detect every bus fault.

The sketch also accepts runtime commands from Orienta's **Sensor settings**
dialog. Range, sensor rate, low-pass cutoff, and output rate changes are applied
without reflashing; the dialog requests and displays the values read back from
the MPU6500 registers. Settings chosen in the dialog are stored in Orienta's
preferences and are not silently assumed to be the hardware's current state.

## Usage

You will obviously need to find a way to mount your sensor on your head. In the example I use a 3D-printed case for the sensor itself and the arduino nano that both clip to my headset. Many, if not most IMUs will want to be mounted a specific up-direction to give accurate accelerometer readings - tilted on the side or on it's head will probably not work, but flipped 180° is fine. The app allows for flipping axis in the preferences section. 

1. Flash the supplied Arduino sketch, or connect a compatible IMU source.
2. Click **Detect sensor** to probe available serial ports for firmware that
   reports a valid Orienta sensor configuration. Detection selects the port
   and baud rate but does not start tracking; you can also select them manually.
   Click **Start Serial** when the selected port and baud are correct. 
   A saved sensor-settings profile is applied after the first valid sensor
   sample on connection and reconnect. Without saved settings, Orienta leaves
   the device configuration unchanged.
3. After the sensor has warmed up, place it on a level surface with the
   sensor's +Z axis facing up. Trigger **Calibrate Sensor Biases** and keep it
   physically still until calibration completes. Orienta collects stable gyro
   and accelerometer samples while tracking continues; the gyro and
   accelerometer offsets are applied together only after both estimates pass
   stability checks. If calibration fails or times out after 30 seconds, the
   previous estimates are retained. This single-pose procedure estimates
   offsets only; it does not calibrate accelerometer scale or sensor mounting.
4. Mount the device on the headset and look forward in the desired resting
   pose. Reset Orientation recenters yaw, pitch, and roll to that pose; it does
   not change sensor-bias calibration.
5. In the Connection panel's Output section, select UDP (opentrack),
   FreeTrack 2.0 (Direct), or NPClient (TrackIR-compatible), then start output.
6. For UDP, configure OpenTrack to receive packets at the selected address and
   port. For direct FreeTrack or NPClient, enable output before starting the
   game. Orienta stages and registers the selected game-client DLL while output
   is active, then restores the previous registration when output stops. Keep
   Orienta running while the game uses tracking.

Orienta outputs zero translation and orientation as yaw, pitch, and roll - however, you can set static Offsets in the **Native Output settings** menu if you wish. 
Direct FreeTrack output uses Orienta's FreeTrack client DLL for games that load
the FreeTrack interface. NPClient output exposes the same shared-memory poses
through a TrackIR-compatible NPClient interface. Neither direct path requires
the OpenTrack application.

## Feature explanations

### Center assist

When stationary and near center, yaw center assist gently attracts yaw toward
the configured rest pose. It engages only when yaw, pitch, and roll are all
within their configured thresholds, but it changes yaw only; accelerometer
feedback corrects pitch and roll. The selected curve ramps up engagement;
attraction continues afterward rather than stopping at the smoothing time.
Smoothing time controls engagement and sustained correction rate, while
strength scales that rate. Center assist is meant to "catch" your fast head movements, so even with small tracking inaccuracies you end up in your rest pose after a head turn. 

### Calibrate Sensor Biases

This collects a configurable number of IMU samples to determine static accelerometer and gyroscope offsets. 
While calibration runs, the device should ideally be set down, still, level and warmed up. Otherwise the calibration data will be rejected. 

These static offsets are persisted to user preferences and loaded at next startup. 
However, the offsets may change, as they are influenced by external factors such as temperature. If the tracking becomes inaccurate or starts to drift, it is advised to run calibration again. 

### Reset Orientation

Reset Orientation makes your current head pose the output origin. Commonly also known as "Recenter". 

### Disengage Center assist

Center assist can be disengaged. This can be useful for small head movements close to the threshold area. 
It is either a toggleable switch or hold to disengage (Settable in Preferences)

### Hold Orientation

Holds the current pose until it is switched off again. 
It is either a toggleable switch or hold to hold (Settable in Preferences)

### Experimental: Adaptive Yaw Bias Correction

Preferences also offers **Adapt yaw gyro bias during stationary periods
(experimental)**, disabled by default. When enabled, it slowly adapts the
gyro's yaw-axis bias after a sustained low-motion period; it does not directly
recenter or move the current pose. Since an IMU cannot distinguish bias from a
sufficiently slow intentional turn, leave this option off if it makes tracking more inaccurate for you. 

### Output modes

Orienta offers three ways to output the computed yaw, pitch and roll angles: 
* TrackIR-compatible
Uses an NPClient-derived implementation (derived from uglyDwarf's LinuxTrack work - attribution and license terms are included in
`native\npclient`.) that will work with TrackIR-compatible games. 
* Freetrack 2.0
Uses an implementation of the freetrack 2.0 protocol to work with freetrack-compatible games. 
* UDP output
Publishes the angles (with 0 translation) over a configurable UDP port. These can then be used in opentrack. 

For the native output options, additional features for filtering the data, setting input magnitudes and output response curves are available. 
In UDP output mode Orienta expects opentrack (or whatever tracking software you prefer) to handle pose filtering. 

### User data

Preferences and logs are stored in `%LOCALAPPDATA%\Orienta`, for both build/dev
and packaged executable runs. The directory is created automatically:

- `config.cfg` stores user preferences.
- `orienta.log` stores application logs; rotated logs remain in this directory.
- `FreeTrackClient\` contains Orienta's x86/x64 FreeTrack game-reader DLLs,
  staged when direct FreeTrack output is enabled.
- `NPClient\` contains Orienta's x86/x64 NPClient DLLs, staged when NPClient
  output is enabled.
- `NPClient\NPClient.log` records first NPClient calls, shared-memory outcomes,
  and sampled source/converted pose values for diagnostics.
- The bundled FaceTrackNoIR game catalog supplies per-game FreeTrack response
  tables; its attribution and permission notice is included in the package.
- `FreeTrackClient\FreeTrackClient.log` records whether a game loaded the
  FreeTrack client and whether it could read the shared-memory pose.

The NPClient implementation is derived from uglyDwarf's LinuxTrack work under
the MIT license. Attribution and license terms are included in
`native\npclient`.

If `LOCALAPPDATA` is unset, Orienta uses `AppData\Local\Orienta` under your home directory. 

### Demonstration Video

Check out the example device in action here! 

## AI disclaimer
This program was created with heavy (!) assistance from AI, namely GPT5-Mini, GPT6-Luna and Claude Opus 5.5. The core architecture (process manager based design with queues for IPC, preferences-manager based setting and loading of user preferences) was originally written by me for another project some time ago, but that's pretty much as far as my programming expertise was able to take me as far as raw coding goes. Though I work in tech I'm not a programmer by trade. If you were wondering why this is a python project, there's your reason - it's the only programming language I've used regularly over the last 10 years and can at least understand and debug. I've been working on this on and off for about 10 months now, I can hardly call this my own original creation - I view myself as more of a maintainer.

I tried to keep the project structure consistent and sensible, but obviously this project blew up in size quite a bit, and I'm sure there's loads of inacurracies, bad practice and annoyances in here. If anyone wants to contribute, it will be welcomed with open arms, a warm hug, and a glass of milk. 

All this to say, while I've tested this extensively, and use it myself regularly, it is not meant for and probably not suited for critical applications. 

## License

Orienta is distributed under the [MIT License](LICENSE).
