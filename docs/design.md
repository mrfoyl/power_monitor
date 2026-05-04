# Power Monitor — Systemdesign

## 1. Flyt: fra PRTG-alarm til svar

```mermaid
flowchart TD
    SENSOR[PRTG-sensor\ngår NED]
    TRIGGER["State Trigger\n(konfigurert på gruppe)"]
    SCRIPT["prtg_outage_check.py\n--device %device\n--group %group\n--location %location\n--status %status\n--down %down"]

    SENSOR -->|Status: Down| TRIGGER
    TRIGGER -->|Kjør EXE-notifikasjon| SCRIPT

    SCRIPT --> PARSE["Parse GPS fra Location-felt\n'61.5120, 9.1234'"]
    PARSE --> MODE{OUTAGE_API_URL\nsatt?}

    MODE -->|Ja — Remote| HTTP["HTTP GET /check\n?lat=61.512&lon=9.521\n&device=…&group=…"]
    MODE -->|Nei — Local| LOCAL["Direkte funksjonskall\ni samme prosess"]

    HTTP -->|valgfri X-API-Key header| SERVER

    subgraph SERVER["server.py  (Flask API)"]
        ROUTE["/check endpoint"]
        AUTH["Autentisering\n(API-nøkkel valgfritt)"]
        ROUTE --> AUTH
    end

    AUTH --> GEOCODE
    LOCAL --> GEOCODE

    GEOCODE["lookup_gps(lat, lon)\nKartverket punktsøk-API"]
    GEOCODE --> MUNIC["Kommune-navn\neks. 'NORD-FRON'"]

    MUNIC --> PROV["Spør alle providere\n(parallelt)"]

    PROV --> E["Elvia"]
    PROV --> V["Vevig"]
    PROV --> ET["Etna Nett"]
    PROV --> G["Griug"]
    PROV --> GL["Glitre"]
    PROV --> A["Arva"]

    E & V & ET & G & GL & A --> FILTER["Filtrer på kommune\n(case-insensitiv)"]

    FILTER --> RESULT["Tekstresultat\n± liste over strømbrudd"]
    RESULT --> PRTG_OUT["PRTG viser %scriptresult\ni e-post / Teams-varsling"]
```

---

## 2. Deployment

```mermaid
flowchart LR
    subgraph PRTG_SERVER["PRTG-server  (Windows)"]
        SCRIPT2["prtg_outage_check.py\n(i Notifications\\EXE\\)"]
        NOTE["Trenger kun:\n• Python 3.x\n• pip install requests"]
    end

    subgraph MONITOR_SERVER["Sentral server  (Linux/Windows)"]
        FLASK["server.py\n(Flask / Gunicorn)"]
        PKG["power_monitor-pakken\nalle providere og geocoding"]
        FLASK --> PKG
    end

    subgraph EXTERNAL["Eksterne API-er  (internett)"]
        KART["Kartverket GeoNorge\nnorgeskart.no"]
        ARCGIS_ONLINE["ArcGIS Online\nservices-eu1.arcgis.com\n(Elvia)"]
        ARCGIS_ONPREM["ArcGIS on-prem\nGlitre / Arva"]
        QUANT["Quant / Embriq\npowerapi + geoserver-api\n(Vevig, Etna, Griug)"]
    end

    SCRIPT2 -->|"HTTP GET /check\n(LAN)"| FLASK
    PKG -->|"HTTPS"| KART
    PKG -->|"HTTPS"| ARCGIS_ONLINE
    PKG -->|"HTTPS"| ARCGIS_ONPREM
    PKG -->|"HTTPS"| QUANT
```

> **Alternativ — Local mode:** Installer `power_monitor` direkte på PRTG-serveren og la `OUTAGE_API_URL` stå tom. Da trenger du ikke `server.py`.

---

## 3. Provider-oversikt

```mermaid
flowchart LR
    subgraph INNLANDET["Innlandet — primær dekningsområde"]
        ELVIA_N["Elvia\nInnlandet / Oslo / Akershus / Østfold"]
        VEVIG_N["Vevig\nGudbrandsdalen"]
        ETNA_N["Etna Nett\nNumedal"]
        GRIUG_N["Griug\nNumedal / Hallingdal"]
    end

    subgraph ANDRE["Andre providere"]
        GLITRE_N["Glitre Nett\nNumedal / Hallingdal"]
        ARVA_N["Arva / Tromskraft\nTroms"]
    end

    subgraph API_TYPE["API-plattform"]
        AO["ArcGIS Online\n(FeatureServer)"]
        ONPREM["ArcGIS on-prem\n(MapServer / FeatureServer)"]
        QE["Quant / Embriq\ngeoserver-api / powerapi"]
    end

    ELVIA_N --> AO
    VEVIG_N --> QE
    ETNA_N  --> QE
    GRIUG_N --> QE
    GLITRE_N --> ONPREM
    ARVA_N  --> ONPREM
```

---

## 4. CLI-kommandoer

```
python -m power_monitor check 2615
    └─ Slår opp postnummer → kommune → spør Innlandet-providere

python -m power_monitor check "Storgata 1, Lillehammer"
    └─ Slår opp adresse → kommune → spør Innlandet-providere

python -m power_monitor check 2615 --all-providers
    └─ Samme, men spør alle 6 providere

python -m power_monitor list [--provider elvia|vevig|etna|griug|glitre|arva|innlandet|all]
    └─ Lister alle aktive strømbrudd fra valgt provider

python -m power_monitor planned [--provider ...]
    └─ Lister planlagte koblinger som ikke har startet ennå

python -m power_monitor providers
    └─ Viser status og antall endepunkter per provider
```

---

## 5. PRTG-konfigurasjon (oppsummering)

```
1. Kopier prtg_outage_check.py til:
   C:\Program Files (x86)\PRTG Network Monitor\Notifications\EXE\

2. Sett OUTAGE_API_URL i toppen av filen (remote) eller la den stå tom (local).

3. PRTG → Setup → Notifications → Add Notification
   Type: Execute Program
   Program: prtg_outage_check.py
   Parameters:
     --device "%device" --group "%group" --sensor "%name"
     --status "%status" --location "%location" --down "%down"

4. PRTG → Gruppe → Notifications → Add State Trigger
   When: Down  →  Execute: Power Outage Check

5. Legg til %scriptresult i varslings-malen (e-post / Teams).

6. Sett Location-feltet på hver gruppe til GPS-koordinater:
   "61.5120, 9.1234"
```
