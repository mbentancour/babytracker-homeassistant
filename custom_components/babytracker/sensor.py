"""Sensor platform for BabyTracker."""
from __future__ import annotations

from datetime import date, datetime, timezone

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    SENSOR_ACTIVE_TIMER_DURATION,
    SENSOR_AGE_DAYS,
    SENSOR_AGE_MONTHS,
    SENSOR_AGE_WEEKS,
    SENSOR_BACKUP_COUNT,
    SENSOR_BACKUP_LAST_SUCCESS,
    SENSOR_DIAPERS_SOLID_TODAY,
    SENSOR_DIAPERS_TODAY,
    SENSOR_DIAPERS_WET_TODAY,
    SENSOR_FEEDING_VOLUME_TODAY,
    SENSOR_FEEDINGS_TODAY,
    SENSOR_HOURS_SINCE_DIAPER,
    SENSOR_HOURS_SINCE_FEEDING,
    SENSOR_HOURS_SINCE_SLEEP,
    SENSOR_LAST_DIAPER,
    SENSOR_LAST_FEEDING,
    SENSOR_LAST_MEDICATION,
    SENSOR_LAST_SLEEP,
    SENSOR_LAST_TEMPERATURE,
    SENSOR_LATEST_BMI,
    SENSOR_LATEST_HEAD_CIRCUMFERENCE,
    SENSOR_LATEST_HEIGHT,
    SENSOR_LATEST_WEIGHT,
    SENSOR_SLEEP_HOURS_TODAY,
)
from .coordinator import BabyTrackerCoordinator


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors for every child known to the coordinator, plus one
    pair of sensors per backup destination."""
    coordinator: BabyTrackerCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []
    for child in coordinator.data.children:
        cid = child["id"]
        entities.extend([
            LastFeedingSensor(coordinator, cid),
            LastSleepSensor(coordinator, cid),
            LastDiaperSensor(coordinator, cid),
            LastTemperatureSensor(coordinator, cid),
            LastMedicationSensor(coordinator, cid),
            FeedingsTodaySensor(coordinator, cid),
            FeedingVolumeTodaySensor(coordinator, cid),
            SleepHoursTodaySensor(coordinator, cid),
            DiapersTodaySensor(coordinator, cid),
            DiapersWetTodaySensor(coordinator, cid),
            DiapersSolidTodaySensor(coordinator, cid),
            HoursSinceFeedingSensor(coordinator, cid),
            HoursSinceSleepSensor(coordinator, cid),
            HoursSinceDiaperSensor(coordinator, cid),
            AgeDaysSensor(coordinator, cid),
            AgeWeeksSensor(coordinator, cid),
            AgeMonthsSensor(coordinator, cid),
            LatestWeightSensor(coordinator, cid),
            LatestHeightSensor(coordinator, cid),
            LatestHeadCircumferenceSensor(coordinator, cid),
            LatestBMISensor(coordinator, cid),
            ActiveTimerDurationSensor(coordinator, cid),
        ])
    # One pair of backup-status sensors per destination. The coordinator's
    # backup_status map is populated best-effort; destinations the user
    # hasn't configured (or the token can't see) are simply absent.
    for destination_id in coordinator.data.backup_status:
        entities.extend([
            BackupLastSuccessSensor(coordinator, destination_id),
            BackupCountSensor(coordinator, destination_id),
        ])
    async_add_entities(entities)


class _ChildSensor(CoordinatorEntity[BabyTrackerCoordinator], SensorEntity):
    """Base for per-child sensors. One device per child."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BabyTrackerCoordinator, child_id: int, key: str) -> None:
        super().__init__(coordinator)
        self._child_id = child_id
        self._key = key
        self._attr_unique_id = f"{coordinator.entry_id}_child{child_id}_{key}"

    @property
    def _snapshot(self):
        return self.coordinator.data.snapshots.get(self._child_id)

    @property
    def device_info(self) -> DeviceInfo:
        snap = self._snapshot
        name = snap.child.get("first_name") if snap else f"Child {self._child_id}"
        return DeviceInfo(
            identifiers={(DOMAIN, f"child-{self._child_id}")},
            name=name,
            manufacturer="BabyTracker",
            model="Child",
        )


class LastFeedingSensor(_ChildSensor):
    _attr_translation_key = SENSOR_LAST_FEEDING
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, child_id):
        super().__init__(coordinator, child_id, SENSOR_LAST_FEEDING)
        self._attr_name = "Last feeding"

    @property
    def native_value(self):
        snap = self._snapshot
        return _parse_iso(snap.last_feeding.get("start")) if snap and snap.last_feeding else None

    @property
    def extra_state_attributes(self):
        snap = self._snapshot
        if not snap or not snap.last_feeding:
            return None
        f = snap.last_feeding
        return {
            "type": f.get("type"),
            "method": f.get("method"),
            "amount": f.get("amount"),
            "end": f.get("end"),
        }


class LastSleepSensor(_ChildSensor):
    _attr_translation_key = SENSOR_LAST_SLEEP
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, child_id):
        super().__init__(coordinator, child_id, SENSOR_LAST_SLEEP)
        self._attr_name = "Last sleep"

    @property
    def native_value(self):
        snap = self._snapshot
        return _parse_iso(snap.last_sleep.get("start")) if snap and snap.last_sleep else None

    @property
    def extra_state_attributes(self):
        snap = self._snapshot
        if not snap or not snap.last_sleep:
            return None
        s = snap.last_sleep
        return {"end": s.get("end"), "nap": s.get("nap")}


class LastDiaperSensor(_ChildSensor):
    _attr_translation_key = SENSOR_LAST_DIAPER
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, child_id):
        super().__init__(coordinator, child_id, SENSOR_LAST_DIAPER)
        self._attr_name = "Last diaper"

    @property
    def native_value(self):
        snap = self._snapshot
        return _parse_iso(snap.last_diaper.get("time")) if snap and snap.last_diaper else None

    @property
    def extra_state_attributes(self):
        snap = self._snapshot
        if not snap or not snap.last_diaper:
            return None
        d = snap.last_diaper
        return {"wet": d.get("wet"), "solid": d.get("solid"), "color": d.get("color")}


class FeedingsTodaySensor(_ChildSensor):
    _attr_translation_key = SENSOR_FEEDINGS_TODAY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "feedings"

    def __init__(self, coordinator, child_id):
        super().__init__(coordinator, child_id, SENSOR_FEEDINGS_TODAY)
        self._attr_name = "Feedings today"

    @property
    def native_value(self):
        snap = self._snapshot
        return snap.feedings_today if snap else 0


class FeedingVolumeTodaySensor(_ChildSensor):
    _attr_translation_key = SENSOR_FEEDING_VOLUME_TODAY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "mL"

    def __init__(self, coordinator, child_id):
        super().__init__(coordinator, child_id, SENSOR_FEEDING_VOLUME_TODAY)
        self._attr_name = "Feeding volume today"

    @property
    def native_value(self):
        snap = self._snapshot
        return round(snap.feeding_volume_today, 1) if snap else 0


class SleepHoursTodaySensor(_ChildSensor):
    _attr_translation_key = SENSOR_SLEEP_HOURS_TODAY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "h"

    def __init__(self, coordinator, child_id):
        super().__init__(coordinator, child_id, SENSOR_SLEEP_HOURS_TODAY)
        self._attr_name = "Sleep today"

    @property
    def native_value(self):
        snap = self._snapshot
        return round(snap.sleep_minutes_today / 60, 2) if snap else 0


class DiapersTodaySensor(_ChildSensor):
    _attr_translation_key = SENSOR_DIAPERS_TODAY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "diapers"

    def __init__(self, coordinator, child_id):
        super().__init__(coordinator, child_id, SENSOR_DIAPERS_TODAY)
        self._attr_name = "Diapers today"

    @property
    def native_value(self):
        snap = self._snapshot
        return snap.diapers_today if snap else 0


class DiapersWetTodaySensor(_ChildSensor):
    _attr_translation_key = SENSOR_DIAPERS_WET_TODAY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "diapers"

    def __init__(self, coordinator, child_id):
        super().__init__(coordinator, child_id, SENSOR_DIAPERS_WET_TODAY)
        self._attr_name = "Wet diapers today"

    @property
    def native_value(self):
        snap = self._snapshot
        return snap.diapers_wet_today if snap else 0


class DiapersSolidTodaySensor(_ChildSensor):
    _attr_translation_key = SENSOR_DIAPERS_SOLID_TODAY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "diapers"

    def __init__(self, coordinator, child_id):
        super().__init__(coordinator, child_id, SENSOR_DIAPERS_SOLID_TODAY)
        self._attr_name = "Solid diapers today"

    @property
    def native_value(self):
        snap = self._snapshot
        return snap.diapers_solid_today if snap else 0


class LastTemperatureSensor(_ChildSensor):
    """Sensor that reports the most recent temperature reading.

    State = numeric temperature; the timestamp lives in attributes so the
    entity is usable on charts (numeric) AND for "when was last reading"
    automations (via the attribute)."""

    _attr_translation_key = SENSOR_LAST_TEMPERATURE
    _attr_device_class = SensorDeviceClass.TEMPERATURE

    def __init__(self, coordinator, child_id):
        super().__init__(coordinator, child_id, SENSOR_LAST_TEMPERATURE)
        self._attr_name = "Last temperature"

    @property
    def native_value(self):
        snap = self._snapshot
        if not snap or not snap.last_temperature:
            return None
        return snap.last_temperature.get("temperature")

    @property
    def extra_state_attributes(self):
        snap = self._snapshot
        if not snap or not snap.last_temperature:
            return None
        return {
            "time": snap.last_temperature.get("time"),
            "notes": snap.last_temperature.get("notes"),
        }


class LastMedicationSensor(_ChildSensor):
    """Sensor with the most recent medication name as state.

    Useful for "what was the last thing given" cards. The dose & timestamp
    live in attributes."""

    _attr_translation_key = SENSOR_LAST_MEDICATION

    def __init__(self, coordinator, child_id):
        super().__init__(coordinator, child_id, SENSOR_LAST_MEDICATION)
        self._attr_name = "Last medication"

    @property
    def native_value(self):
        snap = self._snapshot
        if not snap or not snap.last_medication:
            return None
        return snap.last_medication.get("name")

    @property
    def extra_state_attributes(self):
        snap = self._snapshot
        if not snap or not snap.last_medication:
            return None
        m = snap.last_medication
        return {
            "time": m.get("time"),
            "dosage": m.get("dosage"),
            "dosage_unit": m.get("dosage_unit"),
            "notes": m.get("notes"),
        }


# ---------------------------------------------------------------------------
# Derived numeric "time since" sensors — same data as the TIMESTAMP variants
# above, but expressed as hours-ago. Easier in Jinja templates and more
# natural in automation conditions ("if > 4").
# ---------------------------------------------------------------------------


def _hours_since(when: datetime | None) -> float | None:
    if not when:
        return None
    now = datetime.now(when.tzinfo or timezone.utc)
    delta = now - when
    return round(delta.total_seconds() / 3600.0, 2)


class HoursSinceFeedingSensor(_ChildSensor):
    _attr_translation_key = SENSOR_HOURS_SINCE_FEEDING
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "h"

    def __init__(self, coordinator, child_id):
        super().__init__(coordinator, child_id, SENSOR_HOURS_SINCE_FEEDING)
        self._attr_name = "Hours since last feeding"

    @property
    def native_value(self):
        snap = self._snapshot
        if not snap or not snap.last_feeding:
            return None
        return _hours_since(_parse_iso(snap.last_feeding.get("start")))


class HoursSinceSleepSensor(_ChildSensor):
    _attr_translation_key = SENSOR_HOURS_SINCE_SLEEP
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "h"

    def __init__(self, coordinator, child_id):
        super().__init__(coordinator, child_id, SENSOR_HOURS_SINCE_SLEEP)
        self._attr_name = "Hours since last sleep"

    @property
    def native_value(self):
        snap = self._snapshot
        if not snap or not snap.last_sleep:
            return None
        # Use sleep END (i.e. "awake since") rather than start — that's what
        # people actually want for "how long has the baby been awake".
        end = snap.last_sleep.get("end")
        if not end:
            return None
        return _hours_since(_parse_iso(end))


class HoursSinceDiaperSensor(_ChildSensor):
    _attr_translation_key = SENSOR_HOURS_SINCE_DIAPER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "h"

    def __init__(self, coordinator, child_id):
        super().__init__(coordinator, child_id, SENSOR_HOURS_SINCE_DIAPER)
        self._attr_name = "Hours since last diaper"

    @property
    def native_value(self):
        snap = self._snapshot
        if not snap or not snap.last_diaper:
            return None
        return _hours_since(_parse_iso(snap.last_diaper.get("time")))


# ---------------------------------------------------------------------------
# Age sensors — derived from child.birth_date. Stable unless the birth date
# is edited. Unlike the other sensors these don't need coordinator data
# other than the child record itself, but we go through the coordinator for
# consistency (and so that toggling the parent entity works predictably).
# ---------------------------------------------------------------------------


def _parse_birth(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


class AgeDaysSensor(_ChildSensor):
    _attr_translation_key = SENSOR_AGE_DAYS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "d"

    def __init__(self, coordinator, child_id):
        super().__init__(coordinator, child_id, SENSOR_AGE_DAYS)
        self._attr_name = "Age (days)"

    @property
    def native_value(self):
        snap = self._snapshot
        if not snap:
            return None
        born = _parse_birth(snap.child.get("birth_date"))
        if not born:
            return None
        return (date.today() - born).days


class AgeWeeksSensor(_ChildSensor):
    _attr_translation_key = SENSOR_AGE_WEEKS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "wk"

    def __init__(self, coordinator, child_id):
        super().__init__(coordinator, child_id, SENSOR_AGE_WEEKS)
        self._attr_name = "Age (weeks)"

    @property
    def native_value(self):
        snap = self._snapshot
        if not snap:
            return None
        born = _parse_birth(snap.child.get("birth_date"))
        if not born:
            return None
        return (date.today() - born).days // 7


class AgeMonthsSensor(_ChildSensor):
    _attr_translation_key = SENSOR_AGE_MONTHS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "mo"

    def __init__(self, coordinator, child_id):
        super().__init__(coordinator, child_id, SENSOR_AGE_MONTHS)
        self._attr_name = "Age (months)"

    @property
    def native_value(self):
        snap = self._snapshot
        if not snap:
            return None
        born = _parse_birth(snap.child.get("birth_date"))
        if not born:
            return None
        # Calendar-month age — what pediatricians use. "8 months and 3 days"
        # rounds down to 8.
        today = date.today()
        months = (today.year - born.year) * 12 + (today.month - born.month)
        if today.day < born.day:
            months -= 1
        return max(0, months)


# ---------------------------------------------------------------------------
# Latest growth measurements. State is the numeric value; attributes expose
# percentile, timestamp, and any notes so dashboards can show the full card.
# ---------------------------------------------------------------------------


class LatestWeightSensor(_ChildSensor):
    _attr_translation_key = SENSOR_LATEST_WEIGHT
    _attr_device_class = SensorDeviceClass.WEIGHT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "kg"

    def __init__(self, coordinator, child_id):
        super().__init__(coordinator, child_id, SENSOR_LATEST_WEIGHT)
        self._attr_name = "Latest weight"

    @property
    def native_value(self):
        snap = self._snapshot
        if not snap or not snap.latest_weight:
            return None
        return snap.latest_weight.get("weight")

    @property
    def extra_state_attributes(self):
        snap = self._snapshot
        if not snap or not snap.latest_weight:
            return None
        w = snap.latest_weight
        return {"date": w.get("date"), "percentile": w.get("percentile"), "notes": w.get("notes")}


class LatestHeightSensor(_ChildSensor):
    _attr_translation_key = SENSOR_LATEST_HEIGHT
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "cm"

    def __init__(self, coordinator, child_id):
        super().__init__(coordinator, child_id, SENSOR_LATEST_HEIGHT)
        self._attr_name = "Latest height"

    @property
    def native_value(self):
        snap = self._snapshot
        if not snap or not snap.latest_height:
            return None
        return snap.latest_height.get("height")

    @property
    def extra_state_attributes(self):
        snap = self._snapshot
        if not snap or not snap.latest_height:
            return None
        h = snap.latest_height
        return {"date": h.get("date"), "percentile": h.get("percentile"), "notes": h.get("notes")}


class LatestHeadCircumferenceSensor(_ChildSensor):
    _attr_translation_key = SENSOR_LATEST_HEAD_CIRCUMFERENCE
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "cm"

    def __init__(self, coordinator, child_id):
        super().__init__(coordinator, child_id, SENSOR_LATEST_HEAD_CIRCUMFERENCE)
        self._attr_name = "Latest head circumference"

    @property
    def native_value(self):
        snap = self._snapshot
        if not snap or not snap.latest_head_circumference:
            return None
        return snap.latest_head_circumference.get("circumference")

    @property
    def extra_state_attributes(self):
        snap = self._snapshot
        if not snap or not snap.latest_head_circumference:
            return None
        hc = snap.latest_head_circumference
        return {"date": hc.get("date"), "percentile": hc.get("percentile"), "notes": hc.get("notes")}


class LatestBMISensor(_ChildSensor):
    _attr_translation_key = SENSOR_LATEST_BMI
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "kg/m²"

    def __init__(self, coordinator, child_id):
        super().__init__(coordinator, child_id, SENSOR_LATEST_BMI)
        self._attr_name = "Latest BMI"

    @property
    def native_value(self):
        snap = self._snapshot
        if not snap or not snap.latest_bmi:
            return None
        return snap.latest_bmi.get("bmi")

    @property
    def extra_state_attributes(self):
        snap = self._snapshot
        if not snap or not snap.latest_bmi:
            return None
        b = snap.latest_bmi
        return {"date": b.get("date"), "percentile": b.get("percentile")}


# ---------------------------------------------------------------------------
# Live active-timer duration. Updates every coordinator refresh (60s) which
# is coarse for a timer — but fine for dashboards that show "12 minutes in".
# ---------------------------------------------------------------------------


class ActiveTimerDurationSensor(_ChildSensor):
    _attr_translation_key = SENSOR_ACTIVE_TIMER_DURATION
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "s"

    def __init__(self, coordinator, child_id):
        super().__init__(coordinator, child_id, SENSOR_ACTIVE_TIMER_DURATION)
        self._attr_name = "Active timer duration"

    @property
    def native_value(self):
        snap = self._snapshot
        if not snap or not snap.active_timer:
            return 0
        start = _parse_iso(snap.active_timer.get("start"))
        if not start:
            return 0
        now = datetime.now(start.tzinfo or timezone.utc)
        return max(0, int((now - start).total_seconds()))

    @property
    def extra_state_attributes(self):
        snap = self._snapshot
        if not snap or not snap.active_timer:
            return None
        t = snap.active_timer
        return {"name": t.get("name"), "start": t.get("start")}


# ---------------------------------------------------------------------------
# Integration-level backup status. One sensor per destination — state is
# the timestamp of the most recent successful backup that landed there.
# ---------------------------------------------------------------------------


class BackupLastSuccessSensor(CoordinatorEntity[BabyTrackerCoordinator], SensorEntity):
    """Timestamp of the most recent successful backup to a destination."""

    _attr_has_entity_name = True
    _attr_translation_key = SENSOR_BACKUP_LAST_SUCCESS
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: BabyTrackerCoordinator, destination_id: int) -> None:
        super().__init__(coordinator)
        self._destination_id = destination_id
        self._attr_unique_id = f"{coordinator.entry_id}_dest{destination_id}_last_success"
        self._attr_name = "Last successful backup"

    @property
    def _status(self):
        return self.coordinator.data.backup_status.get(self._destination_id)

    @property
    def native_value(self):
        s = self._status
        if not s or not s.last_success:
            return None
        # Treat naive timestamps as UTC for HA's purposes. The backend serves
        # them in its local timezone but HA expects tz-aware for TIMESTAMP
        # sensors. Good-enough approximation; ISO-8601-aware parsing would
        # be nicer but requires a backend change.
        t = s.last_success
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t

    @property
    def extra_state_attributes(self):
        s = self._status
        if not s:
            return None
        return {
            "destination_name": s.destination.get("name"),
            "destination_type": s.destination.get("type"),
            "total_backups": s.total_backups,
        }

    @property
    def device_info(self) -> DeviceInfo:
        s = self._status
        name = s.destination.get("name") if s else f"Destination {self._destination_id}"
        return DeviceInfo(
            identifiers={(DOMAIN, f"backup-destination-{self._destination_id}")},
            name=f"Backup: {name}",
            manufacturer="BabyTracker",
            model="Backup destination",
        )


class BackupCountSensor(CoordinatorEntity[BabyTrackerCoordinator], SensorEntity):
    """Number of backup archives currently stored at this destination."""

    _attr_has_entity_name = True
    _attr_translation_key = SENSOR_BACKUP_COUNT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "backups"

    def __init__(self, coordinator: BabyTrackerCoordinator, destination_id: int) -> None:
        super().__init__(coordinator)
        self._destination_id = destination_id
        self._attr_unique_id = f"{coordinator.entry_id}_dest{destination_id}_count"
        self._attr_name = "Backup count"

    @property
    def _status(self):
        return self.coordinator.data.backup_status.get(self._destination_id)

    @property
    def native_value(self):
        s = self._status
        return s.total_backups if s else 0

    @property
    def device_info(self) -> DeviceInfo:
        s = self._status
        name = s.destination.get("name") if s else f"Destination {self._destination_id}"
        return DeviceInfo(
            identifiers={(DOMAIN, f"backup-destination-{self._destination_id}")},
            name=f"Backup: {name}",
            manufacturer="BabyTracker",
            model="Backup destination",
        )
