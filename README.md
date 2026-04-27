# Power Monitor

CLI tool that queries Norwegian power utility outage APIs and correlates them
with PRTG network sensor downtime. When a PRTG sensor goes down, run a check
to find out if a reported power outage in the area is the likely cause.

## Providers

| Provider | Coverage | API type |
|---|---|---|
| **Elvia** | Innlandet, Oslo, Akershus, Østfold | ArcGIS Online FeatureServer (public) |
| **Vevig** | Nord-Fron, Sør-Fron, Ringebu, Skjåk, Øyer | Custom geoserver-api (public) |
| Glitre Nett | Numedal, Drammen, Kongsberg | ArcGIS on-prem MapServer (public) |
| Arva (Tromskraft) | Tromsø / Troms | ArcGIS on-prem FeatureServer (public) |

All APIs are public and require no authentication. Queries are only made on
demand — there is no background polling.

---

## Installation

```
pip install -r requirements.txt
```

Requires Python 3.11+.

---

## CLI usage

All commands are run from the project directory:

```
cd c:\Github_projects\power_monitor
```

### Check a postal code or address

```
python -m power_monitor check 2640
python -m power_monitor check "Storgata 1, Lillehammer"
```

By default only Innlandet providers (Elvia + Vevig) are queried.
Add `--all-providers` to include Glitre and Arva:

```
python -m power_monitor check 2640 --all-providers
```

### List all active outages from a provider

```
python -m power_monitor list
python -m power_monitor list --provider elvia
python -m power_monitor list --provider vevig
python -m power_monitor list --provider glitre
python -m power_monitor list --provider arva
python -m power_monitor list --provider all
```

### Show provider status

```
python -m power_monitor providers
```

### Verbose / debug output

```
python -m power_monitor -v check 2640
```

---

## Project structure

```
power_monitor/
    collectors/
        base.py         Abstract base class for all collectors
        arcgis.py       Shared ArcGIS REST query logic
        elvia.py        Elvia (ArcGIS Online)
        vevig.py        Vevig (custom geoserver-api)
        glitre.py       Glitre Nett (ArcGIS on-prem)
        arva.py         Arva / Tromskraft (ArcGIS on-prem)
    geocoding.py        Kartverket address / postnr / GPS lookup
    models.py           PowerOutage dataclass
    cli.py              Click CLI commands
prtg_outage_check.py    PRTG EXE notification script (see below)
requirements.txt
```

---

## PRTG integration

When a PRTG sensor goes Down, `prtg_outage_check.py` is triggered as an
EXE/Script notification. It reverse-geocodes the PRTG group's GPS location
to a municipality, queries the outage APIs, and outputs a plain-text result
that PRTG includes in the alert notification.

### Prerequisites

- Python 3.11+ installed on the PRTG server and available on `PATH`
- The `power_monitor` project accessible from the PRTG server
  (either copied locally or on a shared drive)
- `pip install -r requirements.txt` run on the PRTG server

### Step 1 — Copy the script to PRTG

Copy `prtg_outage_check.py` **and** the entire `power_monitor/` folder to
PRTG's EXE notification directory:

```
C:\Program Files (x86)\PRTG Network Monitor\Notifications\EXE\
```

The directory should look like:

```
Notifications\EXE\
    prtg_outage_check.py
    power_monitor\
        __init__.py
        collectors\
        geocoding.py
        models.py
        ...
```

### Step 2 — Set GPS coordinates on PRTG groups

For each site group in PRTG, add the GPS coordinates to its Location field:

1. Open the group in PRTG
2. Click **Edit** -> **Settings** tab
3. Find the **Location** field
4. Enter the site GPS coordinates in either format:
   - `61.5120, 9.1234`
   - `61.5120,9.1234`
   - Pasting directly from Google Maps works fine

> **Tip:** Right-click any location in Google Maps and click the coordinates
> at the top of the context menu to copy them.

### Step 3 — Create the notification in PRTG

1. Go to **Setup** -> **Account Settings** -> **Notifications**
2. Click **Add Notification**
3. Fill in:
   - **Name:** `Power Outage Check`
   - **Type:** Execute Program
4. Under **Execute Program**:
   - **Program File:** `prtg_outage_check.py`
   - **Parameters** (copy exactly, including the quotes):
     ```
     --device "%device" --group "%group" --sensor "%name" --status "%status" --location "%location" --down "%down"
     ```
5. Click **Save**

### Step 4 — Assign the notification as a trigger

You can assign it at group level so it fires for any sensor underneath:

1. Open the site group
2. Go to the **Notifications** tab
3. Click **Add State Trigger**
4. Set:
   - **When sensor is:** Down
   - **Perform:** Execute notification -> `Power Outage Check`
5. Click **Save**

### What the output looks like

When an outage is found:

```
2026-04-27 08:14:22  INFO     === PRTG outage check | device='SW-Vinstra-01' group='Vinstra Site' status=Down ===
2026-04-27 08:14:23  INFO     GPS 61.51200, 9.52100 -> NORD-FRON (Innlandet)
Device : SW-Vinstra-01
Group  : Vinstra Site
Sensor : Ping
Status : Down  (down 3 minutes)
Area   : NORD-FRON
------------------------------------------------------------
RESULT : 1 active power outage(s) found -- likely cause!
  [Vevig] Driftsforstyrrelse | 12 customers affected
    1 fault(s) in Nord-Fron vest
```

When no outage is found:

```
RESULT : No active power outages detected in this area.
         Investigate other causes (hardware, connectivity, config).
```

### Log file

The script writes a rolling log to:

```
Notifications\EXE\prtg_outage_check.log
```

Rotates at 5 MB. To change location or disable, edit `LOG_FILE` at the top
of `prtg_outage_check.py`.

### Troubleshooting

**"Could not import power_monitor"**
Ensure the `power_monitor/` folder is present next to `prtg_outage_check.py`
in the EXE directory.

**"No usable GPS location"**
The PRTG group's Location field is missing or not in a recognised coordinate
format. Ensure it contains decimal lat/lon e.g. `61.5120, 9.1234`.

**"Reverse geocoding failed"**
The GPS coordinates are valid but Kartverket's punktsok API returned no
nearby address (this can happen in very remote areas). Try increasing the
search radius by editing `lookup_gps(lat, lon, radius=2000)` in geocoding.py.

**No outage shown but power is actually out**
- The affected provider may not have published the outage yet (typically a
  few minutes delay)
- The municipality derived from GPS may not match the provider's area label
  exactly — check `prtg_outage_check.log` to see which municipality was
  resolved and compare against `python -m power_monitor list --provider all`

---

## Adding a new provider

1. Create `power_monitor/collectors/myprovider.py`
2. For ArcGIS-based providers, subclass `ArcGISCollector` and set `query_urls`
3. For custom APIs, subclass `BaseCollector` and implement `fetch_outages()`
4. Register it in `collectors/__init__.py` and `cli.py`
5. Add to `PROVIDERS` in `prtg_outage_check.py`

See `vevig.py` for an example of a custom API and `glitre.py` for a minimal
ArcGIS example.
