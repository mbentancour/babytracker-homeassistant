# BabyTracker — Home Assistant Custom Integration

A native Home Assistant integration that exposes data from your BabyTracker
instance as sensors. Use these in dashboards, automations, and notifications.

## What you get

For each child, the integration creates a device with these entities:

| Entity                        | Type            | Description                                  |
| ----------------------------- | --------------- | -------------------------------------------- |
| `sensor.<name>_last_feeding`  | timestamp       | When the last feeding started                |
| `sensor.<name>_last_sleep`    | timestamp       | When the last sleep started                  |
| `sensor.<name>_last_diaper`   | timestamp       | When the last diaper change happened         |
| `sensor.<name>_feedings_today`| count           | Number of feedings since midnight            |
| `sensor.<name>_feeding_volume_today` | mL       | Total fed volume today                       |
| `sensor.<name>_sleep_today`   | hours           | Total sleep today                            |
| `sensor.<name>_diapers_today` | count           | Number of diapers today                      |
| `binary_sensor.<name>_active_timer` | on/off    | On while a timer is running for this child   |

Polls BabyTracker every 60 seconds.

## Installation

> **HACS does not manage Home Assistant add-ons.** The main BabyTracker
> repository is an add-on repository, so HACS won't see this integration if
> you install it from there. For HACS, the integration must live in its own
> repository (see below).

### HACS (custom repository)

This integration is published at: **https://github.com/mbentancour/babytracker-homeassistant**

1. In HACS, open the three-dot menu → **Custom repositories**.
2. Add `https://github.com/mbentancour/babytracker-homeassistant` with
   category **Integration**.
3. Search for "BabyTracker" in HACS and install.
4. Restart Home Assistant.
5. Go to **Settings → Devices & Services → Add Integration** and search for
   "BabyTracker".

### Manual

1. Download or clone the
   [babytracker-homeassistant repository](https://github.com/mbentancour/babytracker-homeassistant)
   (or this `homeassistant/` subfolder of the main repo).
2. Copy `custom_components/babytracker/` into your Home Assistant
   `config/custom_components/` directory.
3. Restart Home Assistant.
4. Go to **Settings → Devices & Services → Add Integration** and search for
   "BabyTracker".

## Setup

You'll be asked for:

- **URL** — Your BabyTracker URL, e.g. `https://babytracker.local:8099` or
  the IP/port if on a server. Include scheme (`http://` or `https://`).
- **API token** — In BabyTracker, go to **Settings → Integrations → API
  Tokens** and create one. Read-only is enough for sensors.
- **Verify SSL** — Uncheck this if your BabyTracker uses a self-signed
  certificate (the default for the Pi appliance image).

## Events

The integration fires events on the Home Assistant event bus whenever new
activity is detected in BabyTracker. Use these as automation triggers:

| Event                          | Fired when                          | Data                              |
| ------------------------------ | ----------------------------------- | --------------------------------- |
| `babytracker_new_feeding`      | A feeding entry is created          | `child_id`, `child_name`, `entry` |
| `babytracker_new_sleep`        | A sleep entry is created            | `child_id`, `child_name`, `entry` |
| `babytracker_new_diaper`       | A diaper change is logged           | `child_id`, `child_name`, `entry` |
| `babytracker_timer_started`    | A timer starts                      | `child_id`, `child_name`, `timer` |
| `babytracker_timer_stopped`    | A timer is stopped/deleted          | `timer_id`                        |

The `entry` and `timer` payloads contain the full record from the API (id,
type, method, amount, start/end times, etc.).

## Services

The integration registers services to control BabyTracker from Home Assistant:

| Service                       | Action                                        |
| ----------------------------- | --------------------------------------------- |
| `babytracker.log_feeding`     | Record a feeding                              |
| `babytracker.log_sleep`       | Record a sleep period                         |
| `babytracker.log_diaper`      | Record a diaper change                        |
| `babytracker.start_timer`     | Start a timer for a child                     |
| `babytracker.stop_timer`      | Stop a running timer by ID                    |
| `babytracker.set_slideshow`   | Start/stop the picture frame slideshow        |

These require a `read_write` API token (configure in BabyTracker → Settings
→ Integrations → API Tokens). All write services trigger a coordinator
refresh so sensors update immediately.

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

### One-tap "log a diaper change" button on the dashboard

```yaml
script:
  log_lily_diaper_wet:
    sequence:
      - service: babytracker.log_diaper
        data:
          child_id: 1
          wet: true
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
          child_id: 1
          name: "Sleep"
```

### Show the slideshow when the room is dark and idle

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

- **"Cannot connect"** — Check that the URL is reachable from your HA host.
  If using `babytracker.local`, mDNS resolution must work between the two.
  Try the IP address instead.
- **"Authentication failed"** — Token may have been revoked. Regenerate in
  BabyTracker → Settings → Integrations → API Tokens.
- **Self-signed certificates** — Uncheck "Verify SSL" during setup. The
  Pi appliance image ships with a self-signed cert by default.

## How it differs from the BabyTracker HA add-on

- The **add-on** runs BabyTracker itself inside Home Assistant.
- This **integration** connects to a BabyTracker instance (running anywhere)
  and surfaces its data as Home Assistant entities.

You can use both: install the add-on to run BabyTracker locally on your HA
box, then install this integration on top so your dashboards and automations
have access to the data.
