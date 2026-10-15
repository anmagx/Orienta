#include "FastIMU.h"
#include <Wire.h>

#define IMU_ADDRESS 0x68    //Change to the address of the IMU
#define PERFORM_CALIBRATION //Comment to disable startup calibration
MPU6500 IMU;               //Change to the name of any supported IMU! 
#define LED_PIN 3

// Based on FastIMU library (Install from arduino IDE)
// Currently supported IMUS: MPU9255 MPU9250 MPU6886 MPU6500 MPU6050 ICM20689 ICM20690 BMI055 BMX055 BMI160 LSM6DS3 LSM6DSL QMI8658

calData calib = { 0 };  //Calibration data
AccelData accelData;    //Sensor data
GyroData gyroData;

void setup() {
  Wire.begin();
  Wire.setClock(400000); //400khz clock
  Serial.begin(500000);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);  // LED on
  while (!Serial) {
    ;
  }

  int err = IMU.init(calib, IMU_ADDRESS);
  if (err != 0) {
    Serial.print("Error initializing IMU: ");
    Serial.println(err);
    while (true) {
      ;
    }
  }
  
#ifdef PERFORM_CALIBRATION
  Serial.println("FastIMU calibration & data example");
  Serial.println("Keep IMU level.");
  delay(2000);
  IMU.calibrateAccelGyro(&calib);
  Serial.println("Calibration done!");
  Serial.println("Accel biases X/Y/Z: ");
  Serial.print(calib.accelBias[0]);
  Serial.print(", ");
  Serial.print(calib.accelBias[1]);
  Serial.print(", ");
  Serial.println(calib.accelBias[2]);
  Serial.println("Gyro biases X/Y/Z: ");
  Serial.print(calib.gyroBias[0]);
  Serial.print(", ");
  Serial.print(calib.gyroBias[1]);
  Serial.print(", ");
  Serial.println(calib.gyroBias[2]);

  delay(1000);
  IMU.init(calib, IMU_ADDRESS);
#endif

  err = IMU.setGyroRange(250);      //USE THESE TO SET THE RANGE, IF AN INVALID RANGE IS SET IT WILL RETURN -1
  err = IMU.setAccelRange(2);       //THESE TWO SET THE GYRO RANGE TO ±500 DPS AND THE ACCELEROMETER RANGE TO ±2g
  
  if (err != 0) {
    Serial.print("Error Setting range: ");
    Serial.println(err);
    while (true) {
      ;
    }
  }
}

const unsigned long SAMPLE_INTERVAL_US = 4000; // 250Hz

void loop() {
  // micros() gives the host exact per-sample dt for gyro integration; millis()
  // quantized dt to 1ms (+-25% of a 4ms period), which showed up as jitter.
  static bool started = false;
  static unsigned long nextSampleUs;
  static unsigned long lastMicros;
  static unsigned long elapsedSec = 0;  // seconds + microseconds never wrap,
  static unsigned long elapsedUs = 0;   // unlike raw micros() (~71 minutes)

  unsigned long now = micros();
  if (!started) {
    started = true;
    lastMicros = now;
    nextSampleUs = now;
  }

  // Fixed-cadence schedule: advancing by the interval (instead of
  // "last = now") keeps the sample rate exact without cumulative drift.
  if ((long)(now - nextSampleUs) < 0) {
    return;
  }
  nextSampleUs += SAMPLE_INTERVAL_US;
  if ((long)(now - nextSampleUs) >= (long)SAMPLE_INTERVAL_US) {
    nextSampleUs = now + SAMPLE_INTERVAL_US;  // fell far behind: resync
  }

  elapsedUs += now - lastMicros;
  lastMicros = now;
  while (elapsedUs >= 1000000UL) {
    elapsedUs -= 1000000UL;
    elapsedSec++;
  }
  char timeStr[20];
  snprintf(timeStr, sizeof(timeStr), "%lu.%06lu", elapsedSec, elapsedUs);

  IMU.update();
  IMU.getAccel(&accelData);
  IMU.getGyro(&gyroData);

  // Use String concatenation - Arduino compatible and faster than multiple prints
  String output = String(timeStr) + "," + 
                  String(accelData.accelX, 4) + "," +
                  String(accelData.accelY, 4) + "," +
                  String(accelData.accelZ, 4) + "," +
                  String(gyroData.gyroX, 4) + "," +
                  String(gyroData.gyroY, 4) + "," +
                  String(gyroData.gyroZ, 4);
  Serial.println(output);
}
