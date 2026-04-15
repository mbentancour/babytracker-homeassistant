"""Constants for the BabyTracker integration."""
from datetime import timedelta

DOMAIN = "babytracker"

# Config entry data keys
CONF_URL = "url"
CONF_TOKEN = "token"
CONF_VERIFY_SSL = "verify_ssl"

# Polling interval — BabyTracker pushes activity events via webhook, so the
# poll is only a safety net for (a) missed pushes, (b) derived sensors like
# "hours since last feeding" that need periodic recalculation, and (c) the
# today-totals rollover at midnight. Ten minutes is generous for all three.
# If webhook registration fails at setup, polling at this rate is what keeps
# the integration functional — still usable, just with up to 10-min lag.
DEFAULT_SCAN_INTERVAL = timedelta(minutes=10)

# Event names fired on the HA event bus when BabyTracker activity is detected
EVENT_NEW_FEEDING = "babytracker_new_feeding"
EVENT_NEW_SLEEP = "babytracker_new_sleep"
EVENT_NEW_DIAPER = "babytracker_new_diaper"
EVENT_NEW_TEMPERATURE = "babytracker_new_temperature"
EVENT_NEW_MEDICATION = "babytracker_new_medication"
EVENT_TIMER_STARTED = "babytracker_timer_started"
EVENT_TIMER_STOPPED = "babytracker_timer_stopped"

# Per-child sensor keys
SENSOR_LAST_FEEDING = "last_feeding"
SENSOR_LAST_SLEEP = "last_sleep"
SENSOR_LAST_DIAPER = "last_diaper"
SENSOR_LAST_TEMPERATURE = "last_temperature"
SENSOR_LAST_MEDICATION = "last_medication"
SENSOR_FEEDINGS_TODAY = "feedings_today"
SENSOR_FEEDING_VOLUME_TODAY = "feeding_volume_today"
SENSOR_SLEEP_HOURS_TODAY = "sleep_hours_today"
SENSOR_DIAPERS_TODAY = "diapers_today"
SENSOR_DIAPERS_WET_TODAY = "diapers_wet_today"
SENSOR_DIAPERS_SOLID_TODAY = "diapers_solid_today"
SENSOR_ACTIVE_TIMER = "active_timer"
# Derived "time since" sensors — numeric counterparts of the timestamp ones.
# Timestamps are great for Lovelace's "x minutes ago" rendering; numerics are
# easier to use in automation conditions like "if > 4 hours".
SENSOR_HOURS_SINCE_FEEDING = "hours_since_feeding"
SENSOR_HOURS_SINCE_SLEEP = "hours_since_sleep"
SENSOR_HOURS_SINCE_DIAPER = "hours_since_diaper"
# Age sensors — driven by child.birth_date.
SENSOR_AGE_DAYS = "age_days"
SENSOR_AGE_WEEKS = "age_weeks"
SENSOR_AGE_MONTHS = "age_months"
# Latest growth measurements.
SENSOR_LATEST_WEIGHT = "latest_weight"
SENSOR_LATEST_HEIGHT = "latest_height"
SENSOR_LATEST_HEAD_CIRCUMFERENCE = "latest_head_circumference"
SENSOR_LATEST_BMI = "latest_bmi"
# Live timer elapsed — seconds since the active timer started, updated every
# coordinator refresh. Handy for "feeding is at 12 minutes" dashboards.
SENSOR_ACTIVE_TIMER_DURATION = "active_timer_duration"

# Integration-level (not per-child) sensors.
SENSOR_BACKUP_LAST_SUCCESS = "backup_last_success"
SENSOR_BACKUP_COUNT = "backup_count"

# Service names
SERVICE_CREATE_BACKUP = "create_backup"
