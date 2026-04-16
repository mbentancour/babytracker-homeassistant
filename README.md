# BabyTracker — Home Assistant Custom Integration

A native Home Assistant integration for [BabyTracker](https://github.com/mbentancour/babytracker).
Surfaces every child as a HA device with sensors, fires events on every new
activity, and exposes services to log everything from automations and
dashboards — without ever opening the BabyTracker app.

## What you get

For each child, the integration creates a Home Assistant **device** with the
following entities:

| Entity                                         | Type      | Description                                |
| ---------------------------------------------- | --------- | ------------------------------------------ |
| `sensor.<name>_last_feeding`                   | timestamp | When the last feeding started              |
| `sensor.<name>_last_sleep`                     | timestamp | When the last sleep started                |
| `sensor.<name>_last_diaper`                    | timestamp | When the last diaper change happened       |
| `sensor.<name>_last_temperature`               | numeric   | Most recent temperature reading            |
| `sensor.<name>_last_medication`                | text      | Most recent medication name                |
| `sensor.<name>_hours_since_last_feeding`       | hours     | Numeric alternative to the timestamp — handy in automation conditions |
| `sensor.<name>_hours_since_last_sleep`         | hours     | Hours since the last sleep **ended** (= "awake since") |
| `sensor.<name>_hours_since_last_diaper`        | hours     | Hours since the last diaper change         |
| `sensor.<name>_feedings_today`                 | count     | Number of feedings since midnight          |
| `sensor.<name>_feeding_volume_today`           | mL        | Total fed volume today                     |
| `sensor.<name>_sleep_today`                    | hours     | Total sleep today                          |
| `sensor.<name>_diapers_today`                  | count     | Number of diapers today                    |
| `sensor.<name>_wet_diapers_today`              | count     | Number of wet diapers today                |
| `sensor.<name>_solid_diapers_today`            | count     | Number of solid diapers today              |
| `sensor.<name>_age_days` / `_weeks` / `_months`| numeric   | Age derived from the birth date            |
| `sensor.<name>_latest_weight`                  | kg        | Most recent weight entry                   |
| `sensor.<name>_latest_height`                  | cm        | Most recent height entry                   |
| `sensor.<name>_latest_head_circumference`      | cm        | Most recent head circumference entry       |
| `sensor.<name>_latest_bmi`                     | kg/m²     | Most recent BMI entry                      |
| `sensor.<name>_active_timer_duration`          | seconds   | Elapsed time of a running timer (0 while idle) |
| `binary_sensor.<name>_active_timer`            | on/off    | On while a timer is running for this child |

Per backup destination, a second device is created with:

| Entity                                 | Type      | Description                                     |
| -------------------------------------- | --------- | ----------------------------------------------- |
| `sensor.backup_<name>_last_successful` | timestamp | Most recent successful backup to this destination |
| `sensor.backup_<name>_backup_count`    | count     | Number of archives currently stored there        |

Useful for alerting when backups stop reaching a remote destination (e.g.
"notify me if the Nextcloud backup is more than 48 hours old").

**Push updates via webhook.** On setup the integration registers an HA
webhook with BabyTracker; new activity events (feeding, sleep, diaper, etc.)
arrive within a second, HMAC-SHA256 signed so the payload can be trusted.
The coordinator still polls every 10 minutes as a safety net — for missed
deliveries, today-totals rollover at midnight, and "hours since last…"
sensors that drift by the minute.

Credentials can be updated later via **Settings → Devices & Services →
BabyTracker → Configure** without re-adding the integration.

## Installation

> **HACS does not manage Home Assistant add-ons.** The main BabyTracker
> repository is an add-on repository, so HACS won't see this integration
> there. This integration lives in its own repo:

### HACS (custom repository)

1. In HACS → ⋮ → **Custom repositories**.
2. Add `https://github.com/mbentancour/babytracker-homeassistant` with
   category **Integration**.
3. Search "BabyTracker" → install.
4. Restart Home Assistant.
5. **Settings → Devices & Services → Add Integration → "BabyTracker"**.

### Manual

1. Copy `custom_components/babytracker/` into HA's `config/custom_components/`.
2. Restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → "BabyTracker"**.

## Setup

You'll be asked for:

- **URL** — Where BabyTracker is running. Examples:
  - Pi appliance: `https://babytracker.local:8099`
  - **HA add-on (no port exposed, recommended): `http://<hash>-babytracker:8099`.**
    Supervisor assigns each add-on container a DNS name of the form
    `<hash>-<slug>`, where `<hash>` is the 8-char SHA1 of the exact URL you
    typed when adding the add-on repository. You can find yours two ways:
    - **From the Supervisor logs**, look for a line like
      `Updating image <hash>/amd64-addon-babytracker:…`. The prefix before
      the `/` is the hash. This is the most reliable source of truth.
    - **By hand**: `python3 -c "import hashlib; print(hashlib.sha1(b'<URL>').hexdigest()[:8])"`
      where `<URL>` is the exact string you entered in **Supervisor →
      Add-on Store → ⋮ → Repositories**. A trailing `.git` or `/` changes
      the hash.
  - HA add-on with a host port mapped: `http://homeassistant.local:8099`
  - Remote server: `http://10.0.0.42:8099`
- **API token** — Create one in BabyTracker → **Settings → Integrations
  → API Tokens**. The integration needs **Read & Write** to use the logging
  services; **Read only** is enough for sensors and events.
- **Verify SSL** — Uncheck for the self-signed cert that ships with the Pi
  appliance.

## Events (use as automation triggers)

The integration fires these events on the HA event bus when activity is
detected in BabyTracker:

| Event                            | Fired when                       | Data                                |
| -------------------------------- | -------------------------------- | ----------------------------------- |
| `babytracker_new_feeding`        | A feeding entry is created       | `child_id`, `child_name`, `entry`   |
| `babytracker_new_sleep`          | A sleep entry is created         | `child_id`, `child_name`, `entry`   |
| `babytracker_new_diaper`         | A diaper change is logged        | `child_id`, `child_name`, `entry`   |
| `babytracker_new_temperature`    | A temperature is logged          | `child_id`, `child_name`, `entry`   |
| `babytracker_new_medication`     | A medication dose is logged      | `child_id`, `child_name`, `entry`   |
| `babytracker_timer_started`      | A timer starts                   | `child_id`, `child_name`, `timer`   |
| `babytracker_timer_stopped`      | A timer is stopped/deleted       | `timer_id`                          |

`entry` and `timer` carry the full record from the API (id, type, method,
amount, start/end times, etc.) so you can branch on any of those fields in
your automation.

## Services (use to control BabyTracker)

Every logging service uses the **HA device picker** for the child — you
never have to remember a numeric child ID. Time fields default to "now"
when omitted.

### Activity logging

| Service                                | What it does                          |
| -------------------------------------- | ------------------------------------- |
| `babytracker.log_feeding`              | Record a feeding                      |
| `babytracker.log_sleep`                | Record a sleep period                 |
| `babytracker.log_diaper`               | Record a diaper change                |
| `babytracker.log_tummy_time`           | Record a tummy time session           |
| `babytracker.log_pumping`              | Record a pumping session              |
| `babytracker.log_temperature`          | Record a temperature reading          |
| `babytracker.log_medication`           | Record a medication dose              |
| `babytracker.log_note`                 | Record a free-form note               |
| `babytracker.log_milestone`            | Record a developmental milestone      |

### Measurements

| Service                                | What it does                          |
| -------------------------------------- | ------------------------------------- |
| `babytracker.log_weight`               | Record a weight measurement           |
| `babytracker.log_height`               | Record a height measurement           |
| `babytracker.log_head_circumference`   | Record a head circumference reading   |

### Timers and display

| Service                       | What it does                                  |
| ----------------------------- | --------------------------------------------- |
| `babytracker.start_timer`     | Start a timer for a child                     |
| `babytracker.stop_timer`      | Stop a timer for a child (by name, optional)  |
| `babytracker.set_slideshow`   | Start/stop the picture frame slideshow        |
| `babytracker.refresh`         | Force-refresh the integration's sensor data   |

### Backups

| Service                       | What it does                                  |
| ----------------------------- | --------------------------------------------- |
| `babytracker.create_backup`   | Trigger an on-demand backup to every enabled destination (or a specific subset via the `destinations` field) |

All write services need a **Read & Write** API token and trigger an
immediate sensor refresh after the call succeeds.

### Tags

Every activity-logging service (feeding, sleep, diaper, tummy time, pumping,
temperature, medication, note, milestone, weight, height, head circumference)
accepts an optional **Tags** field — a comma-separated list of tag names
that get attached to the entry. Unknown names are auto-created with a
default color; rename or recolor them in BabyTracker → Settings → Data →
Tags.

Example automation snippet:

```yaml
service: babytracker.log_diaper
data:
  device_id: !secret lily_device_id
  wet: true
  solid: true
  tags: "teething, nappy rash"
```

Creates the diaper entry, auto-creates the two tags if they don't exist,
and attaches both to the entry. The tag list in the BabyTracker UI shows
the same set on that row immediately.

## Example automations

### Notify when it's been more than 4 hours since the last feeding

```yaml
automation:
  - alias: "Feeding overdue"
    trigger:
      - platform: template
        value_template: >-
          {{ (now() - states('sensor.lily_last_feeding') | as_datetime).total_seconds() > 4 * 3600 }}
    action:
      - service: notify.mobile_app_phone
        data:
          message: "It's been over 4 hours since Lily's last feeding"
```

### Turn on a nightlight while a sleep timer is running

```yaml
automation:
  - alias: "Nightlight while sleeping"
    trigger:
      - platform: state
        entity_id: binary_sensor.lily_active_timer
        to: "on"
    condition:
      - condition: template
        value_template: >-
          {{ state_attr('binary_sensor.lily_active_timer', 'name') | lower in ['sleep', 'nap'] }}
    action:
      - service: light.turn_on
        target: { entity_id: light.nursery_nightlight }
```

### Announce every new feeding on a speaker

```yaml
automation:
  - alias: "Feeding announcement"
    trigger:
      - platform: event
        event_type: babytracker_new_feeding
    action:
      - service: tts.google_say
        data:
          entity_id: media_player.kitchen_speaker
          message: >-
            {{ trigger.event.data.child_name }} was fed
            {% if trigger.event.data.entry.amount %}
              {{ trigger.event.data.entry.amount }} milliliters of
            {% endif %}
            {{ trigger.event.data.entry.type }}
```

### Alert if temperature is high

```yaml
automation:
  - alias: "Fever alert"
    trigger:
      - platform: event
        event_type: babytracker_new_temperature
    condition:
      - condition: template
        value_template: "{{ trigger.event.data.entry.temperature >= 38.0 }}"
    action:
      - service: notify.mobile_app_phone
        data:
          title: "Fever alert"
          message: >-
            {{ trigger.event.data.child_name }}: {{ trigger.event.data.entry.temperature }}°
```

### One-tap "log a wet diaper" button on the dashboard

Drop this script into a dashboard button. The Child picker is shown when
you call the service from Developer Tools → Services or in the script's
field selectors:

```yaml
script:
  log_lily_wet_diaper:
    sequence:
      - service: babytracker.log_diaper
        data:
          device_id: !input lily_device   # set when configuring the script
          wet: true
          solid: false
```

### Start a sleep timer when the nursery lights turn off

```yaml
automation:
  - alias: "Auto-start sleep timer"
    trigger:
      - platform: state
        entity_id: light.nursery
        to: "off"
    action:
      - service: babytracker.start_timer
        data:
          device_id: <pick Lily in the UI>
          name: "Sleep"
```

### Stop the sleep timer when motion is detected

```yaml
automation:
  - alias: "Stop sleep timer on motion"
    trigger:
      - platform: state
        entity_id: binary_sensor.nursery_motion
        to: "on"
    condition:
      - condition: state
        entity_id: binary_sensor.lily_active_timer
        state: "on"
    action:
      - service: babytracker.stop_timer
        data:
          device_id: <pick Lily in the UI>
          name: "Sleep"
```

### Show the slideshow when the room is idle for 5 minutes

```yaml
automation:
  - alias: "Bedroom slideshow"
    trigger:
      - platform: state
        entity_id: binary_sensor.bedroom_motion
        to: "off"
        for: "00:05:00"
    action:
      - service: babytracker.set_slideshow
        data:
          enabled: true
          device: bedroom-tablet
```

## Troubleshooting

- **"Cannot connect"** — Check the URL is reachable from the HA host. If
  using `.local` names, mDNS must work; try the IP otherwise.
- **"Authentication failed"** — Token revoked or wrong scope. Regenerate
  in BabyTracker → Settings → Integrations → API Tokens.
- **Self-signed certificates** — Uncheck "Verify SSL" during setup. The
  Pi appliance image ships with a self-signed cert by default.
- **Service calls fail with "authentication failed"** — Your API token is
  Read-only. The logging services need Read & Write.
- **Times look wrong** — BabyTracker treats all timestamps as local time.
  The integration sends `YYYY-MM-DDTHH:MM:SS` (no timezone) to match. As
  long as HA's timezone is correct, "now" will line up.

## Differences from the BabyTracker HA add-on

- The **add-on** runs BabyTracker itself inside Home Assistant.
- This **integration** connects to a running BabyTracker (anywhere) and
  exposes its data as Home Assistant entities, events, and services.

You can use both: install the add-on to run BabyTracker on your HA host,
then install this integration so your dashboards, automations, and voice
assistants can read and write to it.
