"""Preset validation and serial protocol for supported runtime sensor settings."""

DEFAULT_SENSOR_SETTINGS = {
    "gyro_range": 1000,
    "accel_range": 2,
    "sensor_rate": 1000,
    "gyro_lpf": 42,
    "accel_lpf": 41,
    "output_rate": 250,
}

SENSOR_SETTING_PRESETS = {
    "gyro_range": (250, 500, 1000, 2000),
    "accel_range": (2, 4, 8, 16),
    "sensor_rate": (100, 125, 200, 250, 333, 500, 1000),
    "gyro_lpf": (5, 10, 20, 42, 98, 188, 250),
    "accel_lpf": (5, 10, 20, 41, 92, 184, 460),
    "output_rate": (100, 125, 200, 250, 333, 500),
}


def normalize_sensor_settings(values):
    """Return valid saved settings, using defaults for missing or invalid values."""
    normalized = {}
    for key, presets in SENSOR_SETTING_PRESETS.items():
        try:
            value = int(values.get(key, DEFAULT_SENSOR_SETTINGS[key]))
        except (AttributeError, TypeError, ValueError, OverflowError):
            value = DEFAULT_SENSOR_SETTINGS[key]
        if value not in presets:
            value = DEFAULT_SENSOR_SETTINGS[key]
        normalized[key] = value
    return normalized


def sensor_settings_command(command, values=None):
    """Serialize a validated GUI request into the firmware command protocol."""
    if command == "GET":
        return b"@SENSOR,GET\n"
    if command != "SET":
        raise ValueError(f"Unsupported sensor command: {command}")

    if not isinstance(values, dict):
        raise ValueError("Sensor settings must be a mapping")
    expected_keys = set(SENSOR_SETTING_PRESETS)
    if set(values) != expected_keys:
        raise ValueError("Sensor settings must include exactly the supported fields")
    settings = {}
    for key, presets in SENSOR_SETTING_PRESETS.items():
        try:
            value = int(values[key])
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(f"Invalid {key} value") from error
        if value not in presets:
            raise ValueError(f"Unsupported {key} preset: {value}")
        settings[key] = value
    if settings["output_rate"] > settings["sensor_rate"]:
        raise ValueError("Output rate cannot exceed the sensor update rate")
    ordered_values = (
        settings["gyro_range"],
        settings["accel_range"],
        settings["sensor_rate"],
        settings["gyro_lpf"],
        settings["accel_lpf"],
        settings["output_rate"],
    )
    return (
        "@SENSOR,SET," + ",".join(str(value) for value in ordered_values) + "\n"
    ).encode("ascii")


def parse_sensor_settings_response(line):
    """Parse a firmware settings/error reply; reject malformed or unsupported data."""
    if not isinstance(line, str) or not line.startswith("!SENSOR,"):
        raise ValueError("Not a sensor settings response")
    if line.startswith("!SENSOR,ERROR,"):
        raise ValueError(line[len("!SENSOR,ERROR,"):].strip() or "Sensor rejected command")
    prefix = "!SENSOR,SETTINGS,"
    if not line.startswith(prefix):
        raise ValueError("Unknown sensor response")

    fields = {}
    for item in line[len(prefix):].strip().split(","):
        key, separator, value = item.partition("=")
        if not separator or key in fields:
            raise ValueError("Malformed sensor settings response")
        try:
            fields[key] = int(value)
        except ValueError as error:
            raise ValueError("Non-integer sensor setting") from error

    expected = set(SENSOR_SETTING_PRESETS)
    if set(fields) != expected:
        raise ValueError("Incomplete sensor settings response")
    if any(fields[key] not in presets for key, presets in SENSOR_SETTING_PRESETS.items()):
        raise ValueError("Device reported an unsupported sensor setting")
    if fields["output_rate"] > fields["sensor_rate"]:
        raise ValueError("Device reported an output rate above the sensor rate")
    return fields
