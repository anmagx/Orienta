#include <FastIMU.h>
#include <Wire.h>
#include <string.h>

const uint8_t IMU_ADDRESS = 0x68;
const uint8_t I2C_SDA_PIN = 6;
const uint8_t I2C_SCL_PIN = 7;
const uint32_t SENSOR_TIMEOUT_US = 100000;
const uint8_t MPU_SMPLRT_DIV = 0x19;
const uint8_t MPU_CONFIG = 0x1A;
const uint8_t GYRO_CONFIG = 0x1B;
const uint8_t ACCEL_CONFIG = 0x1C;
const uint8_t ACCEL_CONFIG2 = 0x1D;

MPU6500 IMU;
calData calib = {0};
AccelData accelData = {0};
GyroData gyroData = {0};
uint32_t lastFreshUs = 0;
uint32_t sampleIntervalUs = 4000;
uint32_t nextSampleUs = 0;
bool scheduleStarted = false;
char commandBuffer[80];
size_t commandLength = 0;
bool commandOverflow = false;

void fail(const __FlashStringHelper *message) {
  Serial.print(F("ERROR: "));
  Serial.println(message);
  while (true) {
    delay(200);
  }
}

bool readMpuRegisters(uint8_t startRegister, uint8_t *data, size_t length) {
  Wire.beginTransmission(IMU_ADDRESS);
  Wire.write(startRegister);
  if (Wire.endTransmission(false) != 0) {
    return false;
  }
  if (Wire.requestFrom((int)IMU_ADDRESS, (int)length) != (int)length) {
    return false;
  }
  for (size_t i = 0; i < length; ++i) {
    if (!Wire.available()) {
      return false;
    }
    data[i] = (uint8_t)Wire.read();
  }
  return true;
}

bool isOneOf(int value, const int *choices, size_t count) {
  for (size_t i = 0; i < count; ++i) {
    if (value == choices[i]) {
      return true;
    }
  }
  return false;
}

int gyroRangeFromRegister(uint8_t config) {
  const int ranges[] = {250, 500, 1000, 2000};
  return ranges[(config >> 3) & 0x03];
}

int accelRangeFromRegister(uint8_t config) {
  const int ranges[] = {2, 4, 8, 16};
  return ranges[(config >> 3) & 0x03];
}

int gyroLpfFromRegister(uint8_t config) {
  const int cutoffs[] = {250, 188, 98, 42, 20, 10, 5};
  uint8_t index = config & 0x07;
  return index < 7 ? cutoffs[index] : -1;
}

int accelLpfFromRegister(uint8_t config) {
  const int cutoffs[] = {460, 184, 92, 41, 20, 10, 5};
  uint8_t index = config & 0x0F;
  return index < 7 ? cutoffs[index] : -1;
}

bool readSensorSettings(int outputRate, int &gyroRange, int &accelRange,
                        int &sensorRate, int &gyroLpf, int &accelLpf) {
  uint8_t registers[5];
  if (!readMpuRegisters(MPU_SMPLRT_DIV, registers, sizeof(registers))) {
    return false;
  }

  sensorRate = 1000 / ((int)registers[0] + 1);
  gyroLpf = gyroLpfFromRegister(registers[1]);
  gyroRange = gyroRangeFromRegister(registers[2]);
  accelRange = accelRangeFromRegister(registers[3]);
  accelLpf = accelLpfFromRegister(registers[4]);
  return gyroLpf > 0 && accelLpf > 0 && outputRate > 0;
}

void reportSensorSettings() {
  int gyroRange, accelRange, sensorRate, gyroLpf, accelLpf;
  int outputRate = (int)(1000000UL / sampleIntervalUs);
  if (!readSensorSettings(outputRate, gyroRange, accelRange, sensorRate,
                          gyroLpf, accelLpf)) {
    Serial.println(F("!SENSOR,ERROR,Could not read sensor configuration"));
    return;
  }

  Serial.print(F("!SENSOR,SETTINGS,gyro_range="));
  Serial.print(gyroRange);
  Serial.print(F(",accel_range="));
  Serial.print(accelRange);
  Serial.print(F(",sensor_rate="));
  Serial.print(sensorRate);
  Serial.print(F(",gyro_lpf="));
  Serial.print(gyroLpf);
  Serial.print(F(",accel_lpf="));
  Serial.print(accelLpf);
  Serial.print(F(",output_rate="));
  Serial.println(outputRate);
}

void reportSensorError(const __FlashStringHelper *message) {
  Serial.print(F("!SENSOR,ERROR,"));
  Serial.println(message);
}

bool applySensorSettings(int gyroRange, int accelRange, int sensorRate,
                         int gyroLpf, int accelLpf, int outputRate) {
  const int gyroRanges[] = {250, 500, 1000, 2000};
  const int accelRanges[] = {2, 4, 8, 16};
  const int sensorRates[] = {100, 125, 200, 250, 333, 500, 1000};
  const int gyroLpfs[] = {5, 10, 20, 42, 98, 188, 250};
  const int accelLpfs[] = {5, 10, 20, 41, 92, 184, 460};
  const int outputRates[] = {100, 125, 200, 250, 333, 500};

  if (!isOneOf(gyroRange, gyroRanges, sizeof(gyroRanges) / sizeof(int)) ||
      !isOneOf(accelRange, accelRanges, sizeof(accelRanges) / sizeof(int)) ||
      !isOneOf(sensorRate, sensorRates, sizeof(sensorRates) / sizeof(int)) ||
      !isOneOf(gyroLpf, gyroLpfs, sizeof(gyroLpfs) / sizeof(int)) ||
      !isOneOf(accelLpf, accelLpfs, sizeof(accelLpfs) / sizeof(int)) ||
      !isOneOf(outputRate, outputRates, sizeof(outputRates) / sizeof(int))) {
    reportSensorError(F("Unsupported setting preset"));
    return false;
  }
  if (outputRate > sensorRate) {
    reportSensorError(F("Output rate exceeds sensor update rate"));
    return false;
  }

  if (IMU.setGyroRange(gyroRange) != 0 ||
      IMU.setAccelRange(accelRange) != 0 ||
      IMU.setGyroODR(sensorRate) != sensorRate ||
      IMU.setGyroLPF(gyroLpf) != gyroLpf ||
      IMU.setAccelLPF(accelLpf) != accelLpf) {
    reportSensorError(F("Sensor rejected a setting"));
    return false;
  }

  int actualGyroRange, actualAccelRange, actualSensorRate;
  int actualGyroLpf, actualAccelLpf;
  if (!readSensorSettings(outputRate, actualGyroRange, actualAccelRange,
                          actualSensorRate, actualGyroLpf, actualAccelLpf) ||
      actualGyroRange != gyroRange || actualAccelRange != accelRange ||
      actualSensorRate != sensorRate || actualGyroLpf != gyroLpf ||
      actualAccelLpf != accelLpf) {
    reportSensorError(F("Sensor register readback did not match requested settings"));
    return false;
  }

  sampleIntervalUs = 1000000UL / (uint32_t)outputRate;
  if (scheduleStarted) {
    nextSampleUs = micros() + sampleIntervalUs;
  }
  reportSensorSettings();
  return true;
}

void handleCommand(const char *command) {
  if (strcmp(command, "@SENSOR,GET") == 0) {
    reportSensorSettings();
    return;
  }

  int gyroRange, accelRange, sensorRate, gyroLpf, accelLpf, outputRate;
  int consumed = 0;
  int parsed = sscanf(command, "@SENSOR,SET,%d,%d,%d,%d,%d,%d%n",
                       &gyroRange, &accelRange, &sensorRate, &gyroLpf,
                       &accelLpf, &outputRate, &consumed);
  if (parsed != 6 || command[consumed] != '\0') {
    reportSensorError(F("Malformed command"));
    return;
  }
  applySensorSettings(gyroRange, accelRange, sensorRate, gyroLpf, accelLpf,
                     outputRate);
}

void processSerialCommands() {
  while (Serial.available() > 0) {
    char incoming = (char)Serial.read();
    if (incoming == '\n') {
      if (commandOverflow) {
        reportSensorError(F("Command too long"));
      } else if (commandLength > 0) {
        if (commandBuffer[commandLength - 1] == '\r') {
          --commandLength;
        }
        commandBuffer[commandLength] = '\0';
        handleCommand(commandBuffer);
      }
      commandLength = 0;
      commandOverflow = false;
    } else if (!commandOverflow) {
      if (commandLength + 1 < sizeof(commandBuffer)) {
        commandBuffer[commandLength++] = incoming;
      } else {
        commandOverflow = true;
      }
    }
  }
}

void setup() {
  Serial.begin(500000);
  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  Wire.setClock(400000);

  // Leave sensor biases untouched; calibrate the gyro in Orienta after warm-up.
  if (IMU.init(calib, IMU_ADDRESS) != 0) {
    fail(F("MPU6500 initialization failed"));
  }
  if (IMU.setGyroRange(1000) != 0) {
    fail(F("Could not set gyro range to +/-1000 dps"));
  }
  if (IMU.setAccelRange(2) != 0) {
    fail(F("Could not set accelerometer range to +/-2 g"));
  }
  // The MPU6500 shares one output-rate divider between accel and gyro.
  if (IMU.setGyroODR(1000) != 1000) {
    fail(F("Could not set sensor output rate to 1000 Hz"));
  }
  if (IMU.setGyroLPF(42) != 42) {
    fail(F("Could not set gyro low-pass filter to 42 Hz"));
  }
  if (IMU.setAccelLPF(41) != 41) {
    fail(F("Could not set accelerometer low-pass filter to 41 Hz"));
  }
  reportSensorSettings();

  lastFreshUs = micros();
}

void loop() {
  static bool haveSample = false;
  static uint32_t lastSampleTimestamp = 0;
  static uint32_t elapsedSec = 0;
  static uint32_t elapsedUs = 0;

  processSerialCommands();

  uint32_t now = micros();
  if (!scheduleStarted) {
    scheduleStarted = true;
    nextSampleUs = now;
  }
  if ((int32_t)(now - nextSampleUs) < 0) {
    return;
  }
  nextSampleUs += sampleIntervalUs;
  if ((int32_t)(now - nextSampleUs) >= (int32_t)sampleIntervalUs) {
    nextSampleUs = now + sampleIntervalUs;
  }

  IMU.update();
  IMU.getAccel(&accelData);
  IMU.getGyro(&gyroData);

  // FastIMU retains both values and timestamps when no new data is ready.
  if ((haveSample && gyroData.timestamp == lastSampleTimestamp) ||
      accelData.timestamp != gyroData.timestamp) {
    if ((uint32_t)(micros() - lastFreshUs) >= SENSOR_TIMEOUT_US) {
      fail(F("No fresh synchronized IMU samples for 100 ms"));
    }
    return;
  }
  lastFreshUs = micros();
  if (haveSample) {
    // Unsigned subtraction unwraps the library's 32-bit micros() timestamp.
    elapsedUs += (uint32_t)(gyroData.timestamp - lastSampleTimestamp);
    elapsedSec += elapsedUs / 1000000UL;
    elapsedUs %= 1000000UL;
  }
  haveSample = true;
  lastSampleTimestamp = gyroData.timestamp;

  char timeStr[24];
  snprintf(timeStr, sizeof(timeStr), "%lu.%06lu",
           (unsigned long)elapsedSec, (unsigned long)elapsedUs);

  // Direct printing avoids per-frame String allocations.
  Serial.print(timeStr);
  Serial.print(',');
  Serial.print(accelData.accelX, 4);
  Serial.print(',');
  Serial.print(accelData.accelY, 4);
  Serial.print(',');
  Serial.print(accelData.accelZ, 4);
  Serial.print(',');
  Serial.print(gyroData.gyroX, 4);
  Serial.print(',');
  Serial.print(gyroData.gyroY, 4);
  Serial.print(',');
  Serial.println(gyroData.gyroZ, 4);
}
