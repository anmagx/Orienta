#include <Wire.h>
#include <7Semi_ICM20948.h>

#define IMU_ADDRESS 0x68

const unsigned long SAMPLE_INTERVAL_US = 4000; // 250 Hz

const uint8_t ICM_REG_BANK_SEL = 0x7F;
const uint8_t ICM_USER_CTRL = 0x03;
const uint8_t ICM_INT_PIN_CFG = 0x0F;
const uint8_t AK09916_ADDRESS = 0x0C;
const uint8_t AK09916_ST1 = 0x10;
const uint8_t AK09916_HXL = 0x11;
const uint8_t AK09916_CNTL2 = 0x31;
const uint8_t AK09916_MODE_100HZ = 0x08;

ICM20948_7Semi imu;
float mx = 0.0f;
float my = 0.0f;
float mz = 0.0f;

bool writeRegister(uint8_t address, uint8_t reg, uint8_t value) {
  Wire.beginTransmission(address);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool readRegisters(uint8_t address, uint8_t reg, uint8_t *data, size_t length) {
  Wire.beginTransmission(address);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) {
    return false;
  }
  if (Wire.requestFrom((int)address, (int)length) != (int)length) {
    return false;
  }
  for (size_t i = 0; i < length; i++) {
    if (!Wire.available()) {
      return false;
    }
    data[i] = Wire.read();
  }
  return true;
}

bool startMagnetometer() {
  if (!writeRegister(IMU_ADDRESS, ICM_REG_BANK_SEL, 0x00)) {
    return false;
  }

  uint8_t userControl = 0;
  if (!readRegisters(IMU_ADDRESS, ICM_USER_CTRL, &userControl, 1)) {
    return false;
  }
  userControl &= (uint8_t)~0x20; // Disable the ICM internal I2C master.
  if (!writeRegister(IMU_ADDRESS, ICM_USER_CTRL, userControl)) {
    return false;
  }

  uint8_t pinConfig = 0;
  if (!readRegisters(IMU_ADDRESS, ICM_INT_PIN_CFG, &pinConfig, 1)) {
    return false;
  }
  if (!writeRegister(IMU_ADDRESS, ICM_INT_PIN_CFG, pinConfig | 0x02)) {
    return false; // Enable host I2C bypass to the AK09916.
  }

  return writeRegister(
      AK09916_ADDRESS,
      AK09916_CNTL2,
      AK09916_MODE_100HZ
  );
}

bool updateMagnetometer() {
  uint8_t status = 0;
  if (!readRegisters(AK09916_ADDRESS, AK09916_ST1, &status, 1)) {
    return false;
  }
  if ((status & 0x01) == 0) {
    return true; // Keep the latest 100 Hz sample for this 250 Hz row.
  }

  uint8_t data[7];
  if (!readRegisters(AK09916_ADDRESS, AK09916_HXL, data, sizeof(data))) {
    return false;
  }

  mx = (int16_t)(((uint16_t)data[1] << 8) | data[0]) * 0.15f;
  my = (int16_t)(((uint16_t)data[3] << 8) | data[2]) * 0.15f;
  mz = (int16_t)(((uint16_t)data[5] << 8) | data[4]) * 0.15f;
  return true;
}

void setup() {
  Serial.begin(500000);
  Wire.begin(7, 6);
  Wire.setClock(400000);

  if (!imu.begin(Wire, IMU_ADDRESS)) {
    Serial.println(F("ERROR: begin(I2C) failed"));
    while (1) delay(200);
  }
  if (!imu.applyBasicDefaults()) {
    Serial.println(F("ERROR: applyBasicDefaults() failed"));
    while (1) delay(200);
  }
  if (!startMagnetometer()) {
    Serial.println(F("ERROR: AK09916 initialization failed"));
    while (1) delay(200);
  }

  uint8_t who = imu.readWhoAmI();
  Serial.print(F("WHO_AM_I = 0x"));
  Serial.println(who, HEX);
  delay(100);
}

void loop() {
  static bool started = false;
  static unsigned long nextSampleUs;
  static unsigned long lastMicros;
  static unsigned long elapsedSec = 0;
  static unsigned long elapsedUs = 0;

  unsigned long now = micros();
  if (!started) {
    started = true;
    lastMicros = now;
    nextSampleUs = now;
  }

  if ((long)(now - nextSampleUs) < 0) {
    return;
  }
  nextSampleUs += SAMPLE_INTERVAL_US;
  if ((long)(now - nextSampleUs) >= (long)SAMPLE_INTERVAL_US) {
    nextSampleUs = now + SAMPLE_INTERVAL_US;
  }

  elapsedUs += now - lastMicros;
  lastMicros = now;
  while (elapsedUs >= 1000000UL) {
    elapsedUs -= 1000000UL;
    elapsedSec++;
  }
  char timeStr[20];
  snprintf(timeStr, sizeof(timeStr), "%lu.%06lu", elapsedSec, elapsedUs);

  float ax, ay, az;
  float gx, gy, gz;
  if (!imu.readAccel(ax, ay, az) || !imu.readGyro(gx, gy, gz)) {
    Serial.println(F("ERROR: accelerometer/gyroscope read failed"));
    return;
  }
  if (!updateMagnetometer()) {
    Serial.println(F("ERROR: AK09916 read failed"));
    return;
  }

  String output = String(timeStr) + "," +
                  String(ax, 4) + "," +
                  String(ay, 4) + "," +
                  String(az, 4) + "," +
                  String(gx, 4) + "," +
                  String(gy, 4) + "," +
                  String(gz, 4) + "," +
                  String(mx, 2) + "," +
                  String(my, 2) + "," +
                  String(mz, 2);
  Serial.println(output);
}
