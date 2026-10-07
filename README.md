# ESB Smart Meter for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![GitHub release](https://img.shields.io/github/release/jmurphylaois1/esb-smart-meter-ha.svg)](https://github.com/jmurphylaois1/esb-smart-meter-ha/releases)

Home Assistant integration for ESB Networks (Ireland) smart meters. Downloads your half-hourly electricity consumption data and adds it to the Energy Dashboard with full historical data going back up to 2 years.

## Features

- Full historical consumption data (up to 2 years) in the Energy Dashboard
- Solar export / microgeneration as a separate statistic, when your meter reports it
- Daily and monthly bar charts out of the box
- Updates every 6 hours by default (configurable)
- Handles ESB Networks' Azure B2C login flow automatically
- Reuses a saved login for as long as ESB accepts it, and waits 24 hours after a failed login (CAPTCHA rate limit protection)
- Prompts you to re-enter your password if ESB rejects it, and raises a Repairs warning if updates keep failing

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations → ⋮ → Custom repositories**
3. Add `https://github.com/jmurphylaois1/esb-smart-meter-ha` as an **Integration**
4. Search for **ESB Smart Meter** and install
5. Restart Home Assistant

### Manual

Copy `custom_components/esb_smart_meter/` to your HA `config/custom_components/` directory and restart.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **ESB Smart Meter**
3. Enter your credentials:

| Field | Description |
|-------|-------------|
| Email address | Your myaccount.esbnetworks.ie login email |
| Password | Your ESB Networks password |
| MPRN | Your 11-digit Meter Point Reference Number |

Your MPRN is on your electricity bill or at [myaccount.esbnetworks.ie](https://myaccount.esbnetworks.ie) under your meter details.

Home Assistant logs in once to check the credentials. That login is saved and reused for the first download.

To change how often data is downloaded, go to **Settings → Devices & Services → ESB Smart Meter → Configure**.

## Sensors

| Sensor | Description |
|--------|-------------|
| Last complete day consumption | kWh used on the most recent full day ESB has published (usually 2–3 days ago). The `date` attribute says which day. |
| Last complete day export | kWh exported on that day. Only created if your meter reports export. |
| Latest reading | End of the newest half-hour ESB has published. |
| Last successful update | When data was last downloaded. The `last_error` attribute shows why the most recent update failed, if it did. |

These sensors are for display. **Don't add them to the Energy Dashboard.** Use the statistics described below instead, which carry the correct hourly timestamps.

## Energy Dashboard

After the first successful update, your consumption data will appear at **Settings → Dashboards → Energy → Add consumption → Pick a statistical sensor** — select **ESB Smart Meter Consumption** (statistic ID `esb_smart_meter:consumption_<MPRN>`). If you export solar, add **ESB Smart Meter Export** (`esb_smart_meter:export_<MPRN>`) under **Return to grid**.

Historical data (up to 2 years) is imported on first run.

## Upgrading from 1.0

- Your existing Energy Dashboard history carries over unchanged. Timestamps are handled exactly as in 1.0: each ESB timestamp is the UTC start of its half-hour.
- The old **ESB Smart Meter Consumption** entity is now **Last complete day consumption**. It keeps its entity ID (`sensor.esb_smart_meter_consumption`), but it is no longer a running meter. Home Assistant may show an issue under **Developer tools → Statistics** saying the entity no longer has a state class. Choose **Delete** to remove the old, unreliable statistics for that entity. This doesn't affect the Energy Dashboard statistic.

## Dashboard Cards

Find all card examples in [`examples/dashboard_cards.yaml`](examples/dashboard_cards.yaml).

Replace `YOUR_MPRN` with your 11-digit MPRN (digits only, no spaces).

### Daily Consumption Bar Chart

Shows kWh consumed each day for the last 30 days.

```yaml
type: statistics-graph
title: Daily Consumption — Last 30 Days
entities:
  - entity: esb_smart_meter:consumption_YOUR_MPRN
    name: Grid Import
stat_types:
  - change
period: day
days_to_show: 30
chart_type: bar
```

### Monthly Consumption Bar Chart

```yaml
type: statistics-graph
title: Monthly Consumption
entities:
  - entity: esb_smart_meter:consumption_YOUR_MPRN
    name: Grid Import
stat_types:
  - change
period: month
days_to_show: 365
chart_type: bar
```

### Last complete day (entity card)

If you upgraded from 1.0, the entity is `sensor.esb_smart_meter_consumption`.

```yaml
type: entity
entity: sensor.esb_smart_meter_last_complete_day_consumption
name: Last Complete Day
icon: mdi:transmission-tower
```

## Notes

- **Data is typically 2–3 days behind.** ESB Networks publish smart meter readings with a delay — the most recent data available is usually 2 to 3 days ago, regardless of how frequently the integration updates. This is a limitation of the ESB Networks portal, not the integration.
- ESB Networks rate-limits logins (~2 per IP per 24 hours). The integration reuses its saved login for as long as ESB accepts it, and only logs in again once it has expired.
- If a login fails (for example `CAPTCHA detected`), the integration won't try to log in again for 24 hours, because retrying only extends the block. The sensors keep their last values in the meantime. If updates have been failing for more than a day, a warning appears under **Settings → Repairs**.
- Data appears in the Energy Dashboard after the first successful update (may take a few minutes on first run due to the volume of historical data).

## Troubleshooting

**No data after installation**

Check **Settings → System → Logs** for `ESB:` log entries. Common causes:
- Incorrect credentials — Home Assistant will show a **Reconfigure** prompt under Settings → Devices & Services. Enter your current password there.
- CAPTCHA rate limit — the integration retries by itself after 24 hours. Restarting doesn't help.

**Data stops updating**

Check the **Last successful update** sensor and its `last_error` attribute. Remember that ESB's newest data is usually 2–3 days old, so **Latest reading** lagging behind is normal.

## Development

```bash
pip install -r requirements-test.txt
pytest
```

## License

MIT
