"""
System-wide configuration constants for orienta.

These are DEFAULT values used for initialization. Many can be overridden
at runtime via GUI controls and saved to config.cfg as user preferences.

Do NOT store runtime state here - this module only defines initial defaults.
"""

# ============================================================================
# Application Information
# ============================================================================
APP_NAME = "orienta"
APP_VERSION = "0.12"

# ============================================================================
# GUI Timing and Updates  
# ============================================================================
GUI_UPDATE_INTERVAL_MS = 16          # How often GUI updates displays (milliseconds) - ~60 FPS for real-time
WORKER_QUEUE_CHECK_INTERVAL_MS = 25   # How often GUI checks queues (milliseconds) - balanced for responsiveness

# Worker timing constants
FUSION_LOOP_SLEEP_MS = 1    # Minimal sleep in fusion loop (was hardcoded)
FUSION_DT_MAX_THRESHOLD = 0.1  # Max delta time before reset (was hardcoded)

# ============================================================================
# GUI Backend (PyQt5 only)
# ============================================================================
# Application uses PyQt5 for all GUI functionality

# ============================================================================
# Serial Communication
# ============================================================================
DEFAULT_SERIAL_PORT = 'COM3'
DEFAULT_SERIAL_BAUD = 115200
SERIAL_RETRY_DELAY = 2.0  # seconds between connection attempts
SERIAL_TIMEOUT = 1.0  # seconds

# ============================================================================
# Queue Configuration  
# ============================================================================
QUEUE_SIZE_DATA = 300  # For data pipelines (serial, euler) - 2.5s buffer at 120Hz
QUEUE_SIZE_DISPLAY = 60  # For display-only queues - 500ms buffer for smooth updates
QUEUE_SIZE_CONTROL = 10  # For control command queues

# Queue monitoring thresholds
QUEUE_HIGH_WATERMARK = 0.8  # Trigger adaptive sampling when queue is 80% full
QUEUE_CRITICAL_WATERMARK = 0.9  # Critical queue level
QUEUE_MONITOR_INTERVAL_S = 5.0  # How often to check queue health
QUEUE_WARNING_THRESHOLD = 0.7   # Log warning when queue >70% full
QUEUE_DROP_COUNT_THRESHOLD = 10  # Log warning after this many drops

# ============================================================================
# Timeout Values
# ============================================================================
QUEUE_PUT_TIMEOUT = 0.001  # seconds - very fast timeout for real-time performance
QUEUE_GET_TIMEOUT = 0.5  # seconds
WORKER_JOIN_TIMEOUT = 2.0  # seconds to wait for worker shutdown

# GUI component timeouts
THRESH_DEBOUNCE_MS = 150  # milliseconds - debounce slider changes in GUI panels

# Error recovery timeouts
WORKER_RESTART_DELAY = 1.0  # seconds to wait before restarting failed worker
MAX_WORKER_RESTART_ATTEMPTS = 3  # maximum restart attempts per worker

# ============================================================================
# Fusion / Complementary Filter
# ============================================================================
ACCEL_THRESHOLD = 0.15  # g-units: threshold for stationary detection
DEFAULT_CENTER_THRESHOLD = 5.0  # degrees: threshold for "near center" detection
ALPHA_DRIFT_CORRECTION = 0.99  # Alpha for drift correction when stationary

# Accelerometer tilt correction (pitch/roll). The gyro provides all short-term
# motion; the accelerometer only pulls slow gyro drift back to level. Time
# constants are in seconds and independent of the sample rate. Short values
# let head translation/vibration leak into the output as jitter.
ACCEL_TAU_PITCH = 1.0
ACCEL_TAU_ROLL = 1.0
ACCEL_TAU_RANGE = (0.2, 10.0)
ACCEL_TAU_STEP = 0.1
# 2nd-order low-pass cutoff (Hz) applied to the accel-vs-gyro tilt error.
# Filtering the error instead of raw accel adds no lag to real head motion.
ACCEL_INNOVATION_CUTOFF_HZ = 1.5
# Soft accel trust: Gaussian width (g) of the |accel| - 1g deviation, and the
# Gaussian width (deg/s) of trust vs. head rotation rate. Turning moves an
# off-axis sensor in a circle, so centripetal/tangential acceleration reads as
# false tilt. The rate is peak-held and decays with ACCEL_TRUST_HOLD_S so
# trust stays low through turn reversals, where the rate briefly crosses zero
# but the angular acceleration peaks.
ACCEL_TRUST_SIGMA_G = 0.05
ACCEL_TRUST_GYRO_RATE_DPS = 30.0
ACCEL_TRUST_HOLD_S = 0.5
# Integral gain (1/s^2) of the online gyro bias estimate driven by the tilt
# error; tracks residual pitch/roll bias left after manual calibration.
# Learning pauses while center assist pulls the pose (that error is not gyro
# bias) and when the tilt error exceeds GYRO_BIAS_TRACKING_MAX_ERROR_DEG
# (resets, jumps), so the estimate cannot wind up into a drift source.
GYRO_BIAS_TRACKING_GAIN = 0.05
GYRO_BIAS_TRACKING_LIMIT_DPS = 0.5
GYRO_BIAS_TRACKING_MAX_ERROR_DEG = 1.5

# Time delta validation
DT_MIN = 0.001  # seconds: reject dt smaller than this (likely duplicate)
DT_MAX = 0.1  # seconds: reject dt larger than this (likely gap/reset)

# Stationarity detection
# Gyro magnitude threshold (deg/s) below which we consider the device "quiet"
STATIONARY_GYRO_THRESHOLD = 5.0
# Debounce time (seconds) the stationary condition must persist before
# declaring the device stationary (prevents jitter/false positives)
STATIONARY_DEBOUNCE_S = 0.15

# Gradual drift correction constants
DRIFT_TRANSITION_CURVE = "cosine"    # Center-assist engagement curve ("exponential", "cosine", "linear", "quadratic")
DRIFT_UI_THRESHOLD = 0.1             # Minimum drift strength to show UI indicator (0.0-1.0)
DRIFT_SMOOTHING_TIME = 2.0           # Center-assist engagement time and sustained time constant (seconds)

# ============================================================================
# Gyro bias calibration
# ============================================================================
# Number of stationary X/Y/Z samples for manually triggered gyro bias calibration.
GYRO_BIAS_CAL_SAMPLES = 400
# Number of accel samples to collect for a quick level (pitch/roll) calibration.
# Level calibration requires far fewer samples than gyro bias; 10-20 is typically enough.
LEVEL_CAL_SAMPLES = 20



# ============================================================================
# Network / UDP
# ============================================================================
DEFAULT_UDP_IP = '127.0.0.1'
DEFAULT_UDP_PORT = 4243
DEFAULT_OUTPUT_PROTOCOL = 'udp'
OUTPUT_PROTOCOL_UDP = 'udp'
OUTPUT_PROTOCOL_FREETRACK = 'freetrack'
OUTPUT_PROTOCOL_NPCLIENT = 'npclient'

# UDP output rate limiting
DEFAULT_OUTPUT_RATE_HZ = 120
OUTPUT_RATE_MIN_HZ = 10
OUTPUT_RATE_MAX_HZ = 250

# Native-output filter defaults and slider ranges
DEFAULT_FILTER_ENABLED = True
FILTER_MIN_CUTOFF_HZ_DEFAULT = 1.0
FILTER_MIN_CUTOFF_HZ_RANGE = (0.1, 20.0)
FILTER_MIN_CUTOFF_HZ_STEP = 0.1
FILTER_BETA_DEFAULT = 0.03
FILTER_BETA_RANGE = (0.0, 5.0)
FILTER_BETA_STEP = 0.01
FILTER_DERIVATIVE_CUTOFF_HZ_DEFAULT = 1.0
FILTER_DERIVATIVE_CUTOFF_HZ_RANGE = (0.1, 20.0)
FILTER_DERIVATIVE_CUTOFF_HZ_STEP = 0.1
EMA_CUTOFF_HZ_DEFAULT = 2.0
EMA_CUTOFF_HZ_RANGE = (0.1, 20.0)
EMA_CUTOFF_HZ_STEP = 0.1
HYSTERESIS_THRESHOLD_DEG_DEFAULT = 0.25
HYSTERESIS_THRESHOLD_DEG_RANGE = (0.05, 1.0)
HYSTERESIS_THRESHOLD_DEG_STEP = 0.05
HYSTERESIS_RELEASE_PERCENT_DEFAULT = 50.0
HYSTERESIS_RELEASE_PERCENT_RANGE = (10.0, 90.0)
HYSTERESIS_RELEASE_PERCENT_STEP = 5.0
CRITICALLY_DAMPED_RESPONSE_HZ_DEFAULT = 5.0
CRITICALLY_DAMPED_RESPONSE_HZ_RANGE = (0.5, 20.0)
CRITICALLY_DAMPED_RESPONSE_HZ_STEP = 0.5
ACCELA_STYLE_RESPONSE_HZ_DEFAULT = 2.0
ACCELA_STYLE_RESPONSE_HZ_RANGE = (0.5, 15.0)
ACCELA_STYLE_RESPONSE_HZ_STEP = 0.5
ACCELA_STYLE_ACCELERATION_GAIN_DEFAULT = 3.0
ACCELA_STYLE_ACCELERATION_GAIN_RANGE = (0.0, 5.0)
ACCELA_STYLE_ACCELERATION_GAIN_STEP = 0.1
ACCELA_STYLE_ACCELERATION_SCALE_DEG_DEFAULT = 2.0
ACCELA_STYLE_ACCELERATION_SCALE_DEG_RANGE = (0.25, 10.0)
ACCELA_STYLE_ACCELERATION_SCALE_DEG_STEP = 0.25
ACCELA_STYLE_DEADZONE_DEG_DEFAULT = 0.03
ACCELA_STYLE_DEADZONE_DEG_RANGE = (0.0, 0.5)
ACCELA_STYLE_DEADZONE_DEG_STEP = 0.01
# Gyro-assisted filter: classifies motion from gyro-derived Euler rates
# (published with each pose) instead of guessing from angle changes.
GYRO_ADAPTIVE_REST_HZ_DEFAULT = 1.0
GYRO_ADAPTIVE_REST_HZ_RANGE = (0.2, 5.0)
GYRO_ADAPTIVE_REST_HZ_STEP = 0.1
GYRO_ADAPTIVE_MOTION_THRESHOLD_DPS_DEFAULT = 2.0
GYRO_ADAPTIVE_MOTION_THRESHOLD_DPS_RANGE = (0.5, 10.0)
GYRO_ADAPTIVE_MOTION_THRESHOLD_DPS_STEP = 0.5
GYRO_ADAPTIVE_DEADZONE_DEG_DEFAULT = 0.05
GYRO_ADAPTIVE_DEADZONE_DEG_RANGE = (0.0, 0.5)
GYRO_ADAPTIVE_DEADZONE_DEG_STEP = 0.01
GYRO_ADAPTIVE_MOVING_HZ = 25.0          # near pass-through while moving
GYRO_ADAPTIVE_RATE_LOWPASS_HZ = 1.5     # 2-pole; averages tremor out of the rate
GYRO_ADAPTIVE_FAST_RATE_MULTIPLIER = 6.0  # snap detection on the raw rate peak
GYRO_ADAPTIVE_RATE_RELEASE_S = 0.15     # peak-rate hold through short pauses
GYRO_ADAPTIVE_TREMOR_MULTIPLIER = 4.0   # snap threshold >= k * learned tremor rms
GYRO_ADAPTIVE_TREMOR_TAU_S = 2.0
GYRO_ADAPTIVE_CATCH_UP_DEG = (1.0, 4.0)  # open fully for large ungyro'd jumps
# While another axis moves, an axis's motion threshold drops to this fraction,
# so the minor component of a diagonal turn is not held back. Noise on an idle
# axis still averages out and keeps it smoothed.
GYRO_ADAPTIVE_CROSS_AXIS_THRESHOLD_FLOOR = 0.5
# Ignore brief counter-rotation before treating it as an intentional reversal.
GYRO_ADAPTIVE_REVERSAL_CONFIRM_S = 0.10
CUSTOM_INPUT_LIMIT_DEG_RANGE = (1.0, 180.0)
CUSTOM_INPUT_LIMIT_DEG_STEP = 0.1
INPUT_LIMIT_CALIBRATION_POSE_STALE_S = 0.5

# ============================================================================
# GUI / Display
# ============================================================================
GUI_POLL_INTERVAL_MS = 100  # milliseconds
MAX_TEXT_BUFFER_LINES = 500  # lines to keep in message/serial displays
FPS_REPORT_INTERVAL = 1.0  # seconds between FPS updates
THRESH_DEBOUNCE_MS = 150  # milliseconds to debounce threshold slider

# Orientation visualization
VISUALIZATION_RANGE = 25.0  # degrees: +/- range for pitch/yaw axes
VISUALIZATION_SIZE = 160  # pixels: width and height of visualization widget

# Theme settings
DEFAULT_THEME = 'light'  # 'light', 'dark'
THEMES_ENABLED = True

# ============================================================================
# Logging
# ============================================================================
LOG_FILE_NAME = 'orienta.log'
LOG_FILE_MAX_SIZE = 5 * 1024 * 1024  # 5 MB
LOG_QUEUE_TIMEOUT = 0.5  # seconds

# ============================================================================
# Preferences File
# ============================================================================
PREFS_FILE_NAME = 'config.cfg'
