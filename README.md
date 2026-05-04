# Power Monitor

CLI tool that queries Norwegian power utility outage APIs and correlates them
with PRTG network sensor downtime. When a PRTG sensor goes down, run a check
to find out if a reported power outage in the area is the likely cause.

## Providers

| Provider | Coverage | API type |
|---|---|---|
| **Elvia** | Innlandet, Oslo, Akershus, Østfold | ArcGIS Online FeatureServer (public) |
| **Vevig** | Nord-Fron, Sør-Fron, Ringebu, Skjåk, Øyer | Custom geoserver-api (public) |
| **Etna Nett** | Etnedal, Nord-Aurdal, Nordre Land, Søndre Land | Custom geoserver-api (public) |
| **Griug** | Numedal / Hallingdal (Nore og Uvdal, Rollag, Nes) | Quant Insight / Embriq REST (public) |
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

The default is `innlandet` — Elvia, Vevig, Etna Nett and Griug combined.

```
python -m power_monitor list
python -m power_monitor list --provider innlandet
python -m power_monitor list --provider elvia
python -m power_monitor list --provider vevig
python -m power_monitor list --provider etna
python -m power_monitor list --provider griug
python -m power_monitor list --provider glitre
python -m power_monitor list --provider arva
python -m power_monitor list --provider all
```

### List upcoming scheduled outages (not yet started)

Some providers (Vevig, Etna Nett, Griug) publish future planned outages before
they begin. Use `planned` to see these — they will not appear in `list` or
trigger the PRTG integration since the power is not actually out yet.

The default is `innlandet`, same as for `list`.

```
python -m power_monitor planned
python -m power_monitor planned --provider innlandet
python -m power_monitor planned --provider etna
python -m power_monitor planned --provider griug
python -m power_monitor planned --provider all
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
        etna.py         Etna Nett (custom geoserver-api)
        griug.py        Griug (Quant Insight / Embriq REST API)
        glitre.py       Glitre Nett (ArcGIS on-prem)
        arva.py         Arva / Tromskraft (ArcGIS on-prem)
    geocoding.py        Kartverket address / postnr / GPS lookup
    models.py           PowerOutage dataclass
    cli.py              Click CLI commands
prtg_outage_check.py    PRTG EXE notification script (see below)
requirements.txt
```

---

## Input handling and security

All external data — from utility APIs, Kartverket, and PRTG — is treated as
untrusted. The measures below are defence in depth against compromised or
misbehaving upstream sources.

### Data from provider APIs

Every outage record passes through `models.py:PowerOutage.__post_init__`
regardless of which provider produced it:

| Field | Measure |
|---|---|
| `num_affected` | Clamped to `0–100 000`. Handles `None`, negative values, and unrealistic counts from a misbehaving API. |
| `customer_message` | Truncated to 500 characters. |
| `municipality` | Truncated to 100 characters. |

Additional per-collector protections:

- **ArcGIS collectors (Elvia, Glitre, Arva):** `if "error" in data` check on
  API responses; deduplication across endpoints; `None` returned for records
  missing a required ID field; `.strip()` on all string fields.
- **Vevig / Etna Nett:** `_safe_int()` helper used in `fetch_summary()` —
  returns `0` on `TypeError`/`ValueError` instead of raising.
- **Griug:** `try/except` around polygon centroid calculation; graceful `None`
  return when geocoding fails.
- **All collectors:** 15-second request timeout; `raise_for_status()` on HTTP
  responses; `try/except requests.RequestException` around every network call.

### PRTG notification script (`prtg_outage_check.py`)

| Input | Measure |
|---|---|
| GPS coordinates (from PRTG Location field) | Validated against Norwegian coordinate bounds: 57–72 °N, 4–32 °E. Script exits with code 1 if coordinates are absent or out of range. |
| `device`, `group`, `sensor` (from PRTG) | Truncated to 200 characters before being written to output or log. |
| `status`, `down` (from PRTG) | Truncated to 50 characters. |
| `customer_message`, `municipality` (from provider APIs) | `html.escape()` applied before embedding in notification text. PRTG inserts `%scriptresult` into HTML email and Teams templates — without escaping, a compromised API could inject links or markup into alerts. |

### API server (`server.py`)

| Measure | Detail |
|---|---|
| `lat` / `lon` validation | Parsed as `float` with `try/except`; returns HTTP 400 on missing or non-numeric values. |
| PRTG context fields | Truncated to 200 / 50 characters via `_trunc()`. |
| `customer_message`, `municipality` in text output | `html.escape()` applied (same reason as above). |
| Rate limiting | `/check` limited to **30 requests per minute per IP** (flask-limiter, in-memory). Each check fans out to 6 provider calls + a Kartverket geocoding request. |
| Optional API key | Set `POWER_MONITOR_API_KEY` on the server; all `/check` requests must then include `X-API-Key: <key>`. Recommended when the server is reachable outside a trusted LAN. |

### What is not in scope

- **SQL injection** — there is no database. No SQL is used anywhere.
- **Code execution from input** — `eval`, `exec`, `subprocess`, and
  `shell=True` are not used. JSON is parsed with `json.loads` / `resp.json()`,
  not `eval`. Arguments are parsed with `argparse`.

---

## PRTG integration

When a PRTG sensor goes Down, `prtg_outage_check.py` is triggered as an
EXE/Script notification. It reverse-geocodes the PRTG group's GPS location
to a municipality, queries the outage APIs, and outputs a plain-text result
that PRTG exposes as `%scriptresult` in notification templates.

### Deployment modes

**Remote mode (recommended)**
Run `server.py` on any central server. The PRTG script calls it over HTTP —
the PRTG server only needs Python and `requests`, nothing else.

```
[PRTG server]  prtg_outage_check.py  -->  HTTP GET  -->  [API server]  server.py
                  (tiny, no deps)                           (full power_monitor)
```

**Local mode**
Run the check directly on the PRTG server. Requires Python 3.11+ and the
full `power_monitor` package installed on the PRTG server.

---

### Running the API server

On the server that will run the outage checks:

```
pip install -r requirements.txt
python server.py
```

The server binds to `0.0.0.0:5000` by default. Test it:

```
curl http://<server-ip>:5000/health
curl "http://<server-ip>:5000/check?lat=61.5120&lon=9.1234"
```

**Environment variables:**

| Variable | Default | Description |
|---|---|---|
| `POWER_MONITOR_HOST` | `0.0.0.0` | Bind address |
| `POWER_MONITOR_PORT` | `5000` | Port |
| `POWER_MONITOR_API_KEY` | _(none)_ | Optional API key (recommended) |

**Optional API key** — set on the server:
```
set POWER_MONITOR_API_KEY=your-secret-key
python server.py
```

Then set the matching key in `prtg_outage_check.py`:
```python
OUTAGE_API_KEY = "your-secret-key"
```

**Rate limiter — multi-worker note:**
The default rate limiter uses in-process memory, which means each gunicorn
worker has its own counter. For multi-worker deployments set a shared Redis
store so the limit is enforced across all workers:

```
pip install flask-limiter[redis]
set RATELIMIT_STORAGE_URL=redis://localhost:6379   # Windows
export RATELIMIT_STORAGE_URL=redis://localhost:6379  # Linux
gunicorn -w 4 -b 0.0.0.0:5000 server:app
```

**Running as a service (Windows):**
```
pip install pywin32
python -m pywin32_postinstall -install
# then use NSSM or Task Scheduler to run server.py on startup
```

**Running as a service (Linux):**
See the systemd example at the bottom of this section.

---

### Prerequisites (PRTG server — remote mode)

- Python 3.x (any version)
- `pip install requests`

### Prerequisites (PRTG server — local mode)

- Python 3.11+
- `pip install -r requirements.txt`
- The full `power_monitor/` folder copied to the EXE directory

---

### Step 1 — Copy the script to PRTG

**Remote mode:** copy only `prtg_outage_check.py` to the EXE directory.

**Local mode:** copy `prtg_outage_check.py` **and** the entire `power_monitor/` folder.

Target directory:

```
C:\Program Files (x86)\PRTG Network Monitor\Notifications\EXE\
```

**Remote mode** — the directory should look like:

```
Notifications\EXE\
    prtg_outage_check.py
```

**Local mode** — the directory should look like:

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

### Step 5 — Add repeat triggers to handle delayed outage reporting

Utilities typically publish outages a few minutes after they occur. The
script only runs once per trigger, so add escalation triggers that re-run
the check at 10 and 30 minutes — that way a delayed report is still caught.

In the same **Notifications** tab on the group, add two more triggers:

| Trigger type | Condition | Action |
|---|---|---|
| State trigger | Still Down after **10 minutes** | Execute `Power Outage Check` |
| State trigger | Still Down after **30 minutes** | Execute `Power Outage Check` |

To set this up:

1. Click **Add State Trigger** again
2. Set:
   - **When sensor is:** Down for at least **10** minutes
   - **Perform:** Execute notification -> `Power Outage Check`
3. Repeat for 30 minutes
4. Click **Save**

All three runs write to the same log file with timestamps, giving you a
full timeline of what was checked and when.

### Step 6 — Show the result in alert notifications

PRTG captures the script's stdout and makes it available as `%scriptresult`
in any notification template. Add it to your existing email or Teams
notification message body — no API integration required.

Example message template:

```
Sensor %name on %device is %status.
Down for: %down
Message: %message

--- Power Outage Check ---
%scriptresult
```

To edit a notification template:

1. Go to **Setup** -> **Account Settings** -> **Notifications**
2. Open your existing email / Teams notification
3. Find the **Message** or **Subject** body field
4. Add `%scriptresult` where you want the outage result to appear
5. Click **Save**

The script only runs when triggered by a Down sensor — there is no
periodic polling.

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
