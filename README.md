# Orienta

![Orienta logo](img/orienta_logo.png)

Orienta is an OpenTrack-compatible 3DOF head-tracking application for Windows.
It reads accelerometer and gyroscope data from a serial-connected IMU, estimates
yaw, pitch, and roll with a complementary filter, and sends orientation over UDP.

## Features

- Real-time serial IMU acquisition.
- Euler and quaternion complementary-filter implementations.
- Gyro-bias and level calibration.
- Configurable stationary drift correction.
- Configurable axis inversion and keyboard/gamepad shortcuts.
- UDP output compatible with OpenTrack.
- PyQt5 interface with light and dark themes.
- Optional diagnostics plots.

## Installation

`install.bat` creates a virtual environment, installs dependencies, and can
create a desktop shortcut.

```powershell
git clone https://github.com/anmagx/Orienta
cd Orienta
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python orienta.py
```

## Requirements

### Software

- Windows
- Python 3.8 through 3.13
- NumPy
- PySerial
- PyQt5
- Keyboard
- Pygame

### Hardware and serial format

Any device that emits seven numeric CSV values on a serial port is supported.
The bundled firmware is an Arduino Nano example using a FastIMU-supported
MPU6500.

```text
time_seconds,accel_x,accel_y,accel_z,gyro_x,gyro_y,gyro_z
```

Accelerometer values are expected in g units and gyroscope values in degrees
per second. Configure the serial port and baud rate in the application to
match the device.

## Usage

1. Flash the supplied Arduino sketch, or connect a compatible IMU source.
2. Select its serial port and baud rate, then start the serial reader.
3. Keep the device still and level while calibrating gyro bias.
4. Mount the device, reset orientation as needed, and enable UDP output.
5. Configure OpenTrack to receive UDP packets at the selected address and port.

Orienta always sends zero translation and orientation as yaw, pitch, and roll.

## License

Orienta is distributed under the [MIT License](LICENSE).
