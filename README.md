# Orienta

![Orienta logo](img/orienta_logo.png)

Orienta is an OpenTrack-compatible 3DOF head-tracking application for Windows.
It reads accelerometer and gyroscope data from a serial-connected IMU, estimates
yaw, pitch, and roll with a complementary filter, and sends orientation over UDP into opentrack.

## Features

- Real-time serial IMU acquisition with support for a wide range of sensors
- Quaternion-based complementary-filter implementation
- Gyro-bias calibration and correction
- Smooth autocentering when stationary and near center
- PyQt5 interface with light and dark themes.

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

- Windows Operating System
- Python 3.8 through 3.14
- NumPy
- PySerial
- PyQt5
- Keyboard
- Pygame

### Hardware and serial format

Any device that emits numeric CSV values on a serial port is supported.
The bundled firmware is an Arduino Nano example using a FastIMU-supported
MPU6500.

```text
time_milliseconds,accel_x,accel_y,accel_z,gyro_x,gyro_y,gyro_z
```

Accelerometer values are expected in g units and gyroscope values in degrees
per second. Configure the serial port and baud rate in the application to
match the device.

## Usage

You will obviously need to find a way to mount your sensor on your head. In the example I use a 3D-printed case for the sensor itself and the arduino nano that both clip to my headset. Many, if not most IMUs will want to be mounted a specific up-direction to give accurate accelerometer readings - tilted on the side will probably not work, but flipped 180° is fine. The app allows for flipping axis in the preferences section. 

1. Flash the supplied Arduino sketch, or connect a compatible IMU source.
2. Select its serial port and baud rate, then start the serial reader.
3. Keep the device still and level while calibrating gyro bias.
4. Mount the device, reset orientation as needed, and enable UDP output.
5. Configure OpenTrack to receive UDP packets at the selected address and port.

Orienta always sends zero translation and orientation as yaw, pitch, and roll.

## License

Orienta is distributed under the [MIT License](LICENSE).
