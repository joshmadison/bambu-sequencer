# PlateLoop

**Print. Eject. Repeat.**

PlateLoop is a self-hosted web app that turns a sliced Bambu Lab
`.gcode.3mf` into an **automatic multi-copy print sequence**: print → cool →
the printer's own toolhead pushes the part off the plate into a container →
re-heat → next copy.

[![CI](https://github.com/joshmadison/plateloop/actions/workflows/ci.yml/badge.svg)](https://github.com/joshmadison/plateloop/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/license-MIT-30d158.svg)](LICENSE) [![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/) [![Docker ready](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)

<p align="center">
  <img src="docs/assets/hero.png" alt="PlateLoop interface with a loaded two-plate file and the ejection preview" width="100%">
</p>
<p align="center">
  <sub>Upload once, preview the real ejection, and download one file for the whole run.</sub>
</p>

## Demo

<p align="center">
  <img src="docs/assets/ejection.gif" alt="Animated preview of the ejection sequence" width="620">
</p>
<p align="center">
  <sub>Lift → park → cool → push → re-park. The preview uses the real object positions from your file.</sub>
</p>

## Features

- **One file, many copies.** Set the number of prints per plate and get a
  single `.gcode.3mf` that runs the whole sequence.
- **Multi-plate orchestration.** Configure every plate independently;
  `0` skips a plate. Plates run back to back without touching the printer.
- **No extra hardware.** The part is pushed by the printer's own toolhead —
  no ejector, conveyor or custom end effector.
- **Per-object ejection.** Multiple objects on one plate are pushed off one
  by one, rear-most first, with a lift and re-park between each push.
- **Printer-aware geometry.** 14 Bambu Lab models with slicer-derived travel
  limits, park positions and toolhead clearance.
- **Safety guards.** Minimum part height, cooldown + safety wait, cooling
  checks and push-lane collision warnings before anything is built.
- **Local and private.** No account, no cloud, no telemetry. Uploads are
  processed in memory and nothing is written to disk.

## How it works

```mermaid
flowchart TD
    A([Print finished]) --> B[Retract filament<br/>lift to safe Z]
    B --> C[Bed fully forward<br/>toolhead parks behind plate]
    C --> D[Pick rear-most object]
    D --> E[Move X to object center]
    E --> F[Cool bed + safety wait]
    F --> G[Push: bed retracts<br/>part slides off the plate]
    G --> H{Objects left?}
    H -- yes --> D
    H -- no --> I[Re-heat]
    I --> J[Next copy]
    J --> A
```

The generated file is a normal single-plate `.gcode.3mf`: the printer's
native start/end blocks are preserved, and the ejection sequence is inserted
between copies. The full motion design is documented in
[docs/ejection_logic.md](docs/ejection_logic.md); the animation frames are
validated against the generated gcode in the test suite.

## Quick start

### Docker (recommended)

```bash
git clone https://github.com/joshmadison/plateloop.git
cd plateloop
docker compose up -d
# open http://localhost:8080
```

The compose file ships sane defaults: non-root container, a 300 MB upload
cap, healthcheck and `restart: unless-stopped`. To raise the upload cap:

```yaml
environment:
  - MAX_UPLOAD_MB=600
```

### Local Python

```bash
git clone https://github.com/joshmadison/plateloop.git
cd plateloop
python -m venv venv

venv/Scripts/pip install -r requirements.txt   # Windows
venv/bin/pip install -r requirements.txt       # Linux / macOS

venv/Scripts/python -m app.main                # Windows
venv/bin/python -m app.main                    # Linux / macOS
# open http://localhost:8000
```

### Homeserver notes

> [!NOTE]
> The app has no login. It binds to `0.0.0.0` inside the container, so publish
> it only on your LAN. If you expose it beyond your LAN, put a reverse proxy
> with authentication in front (Traefik, Caddy, nginx, ...).

Uploads are processed in memory and nothing is persisted, so no volumes are
required. The container healthcheck requests `/` every 30 seconds.

## Usage

1. Slice your object in **Bambu Studio** (or OrcaSlicer).
2. Export **File → Export → Plate as .gcode.3mf** — or **All plates as
   .gcode.3mf** for a multi-plate run.
3. Upload the file, set the prints per plate, adjust the ejection settings
   and check the preview.
4. **Build sequence file**, download the `*_x<N>.gcode.3mf` and print it on
   your Bambu printer like any other file.

<p align="center">
  <img src="docs/assets/confirm.png" alt="Sequence confirmation dialog with plates, prints, filament and ejection settings" width="800">
</p>
<p align="center">
  <sub>Every build is summarized once more before the file is generated.</sub>
</p>

## Multi-plate files

Export with **All plates as .gcode.3mf** and upload it. Each plate gets its own
preview, object list and **prints** field (`0` = skip). The output is still a
single-plate `.gcode.3mf` whose gcode runs the plates back to back:

- every plate is printed as often as configured,
- every print is followed by that plate's ejection push,
- the first-print purge/calibration strip runs **once** for the whole sequence,
- homing and leveling still run before every print,
- the native shutdown runs only after the very last job.

<p align="center">
  <img src="docs/assets/plate-contents.png" alt="Two plates with previews, object dimensions, filament estimates and prints fields" width="720">
</p>

## Configuration

| Setting | Default | What it changes |
| --- | --- | --- |
| Prints per plate | `1` | Total copies for that plate. `0` skips it. |
| Push height | `0.4 mm` | Height of the toolhead edge above the plate during the push. |
| Lift height | `5 mm` | Extra clearance above the tallest object before moving. |
| Ejection bed temp | `45 °C` | Bed target for the cooldown before a part is pushed. |
| Safety wait | `60 s` | Dwell after the bed reaches the ejection temperature. |
| Min height check | `7 mm` | Rejects parts that are too short for a clean push. `0` disables the check. |

The download is named after your file, e.g. `model_x12.gcode.3mf` for 12
total prints.

## Safety first

> [!WARNING]
> **Never leave the printer unattended on the first full cycle.**
> PlateLoop changes the motion commands in the generated file. Watch the
> first complete print → eject → re-heat cycle for every new model or material.

- Point a container at the front of the printer; the part lands there.
- Parts must be tall enough for the toolhead to catch them. The default
  minimum is 7 mm; short parts can jam the toolhead.
- Objects sharing one push lane (overlapping X range) can cascade off together
  or jam. The app warns about these layouts before building.
- Push behavior is **verified on the Bambu Lab A1**. A1 mini and A2L share the
  same mechanics and are marked *expected*. CoreXY models (P1, P2, X1, X2 and
  H2 series) move the toolhead instead of the bed — plausible, but needs a
  test print.
- Verify plate adhesion, container placement and the first cycle for every
  material you use.

## Supported printers

The printer database ([`app/printers.json`](app/printers.json), provenance in
[docs/PRINTER_DB_SOURCES.md](docs/PRINTER_DB_SOURCES.md)) covers 14 Bambu Lab
models:

| Status | Models |
| --- | --- |
| Verified | A1 |
| Expected | A1 mini, A2L |
| Needs a test print | P1P, P1S, P2S, X1, X1C, X1E, X2D, H2S, H2D, H2D Pro, H2C |

Filenames and printer-model identifiers from Bambu Studio and OrcaSlicer are
resolved automatically — you never pick a printer manually. Unknown models
fall back to A1 geometry and show a warning instead of silently assuming the
wrong values. Each model's rear Y limit is tagged `verified`, `from end gcode`
or `estimated`, so you can see what is proven.

<p align="center">
  <img src="docs/assets/printers.png" alt="PlateLoop printer compatibility page" width="100%">
</p>

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests -q          # 57 tests
python -m ruff check app tests     # lint
```

### Project layout

| Path | Purpose |
| --- | --- |
| `app/main.py` | FastAPI routes, upload limits, request validation |
| `app/gcode.py` | Ejection sequence, animation keyframes, multi-plate merge |
| `app/gcode_bbox.py` | Object bounding boxes, push-lane conflicts, print-end position |
| `app/threemf.py` | 3MF parsing, statistics, thumbnails, single-plate repack |
| `app/printer_db.py` | Printer database loader and model resolution |
| `app/printers.json` | Bed, kinematics and toolhead geometry per printer |
| `app/static/index.html` | Self-contained web UI and canvas preview |
| `tests/` | API and pipeline tests, including animation/gcode parity |

Ejection changes must keep `test_keyframes_mirror_gcode` green: the preview and
the generated gcode are validated against each other. To add a printer, extend
`app/printers.json` and document where every value comes from in
[docs/PRINTER_DB_SOURCES.md](docs/PRINTER_DB_SOURCES.md).

## Troubleshooting

**“This looks like a project 3MF without sliced gcode.”**
The upload contains a project, not a sliced plate. Slice it first, then use
*File → Export → Plate as .gcode.3mf*.

**“Object height … is below required 7 mm.”**
The jam guard rejected the build. Slice a taller object or lower the minimum
height — but check the first cycle closely if you do.

**“Printer … is not in the printer database.”**
The app falls back to A1 geometry so it can still build a file. Confirm the
park positions for your model before printing, or add the model to
`app/printers.json`.

**“Objects share the same push lane.”**
Their X ranges overlap, so the rear object may shove the front one along or
jam. Rearrange the plate, or watch the first cycle closely.

**Upload fails with HTTP 413.**
Increase `MAX_UPLOAD_MB` (Docker: see [Quick start](#quick-start)).

**Port 8000 is already in use.**
Start the local server with `PORT=8001 python -m app.main` (Linux/macOS) or
`$env:PORT=8001; python -m app.main` (PowerShell).

## Contributing

Issues and pull requests are welcome. Before opening a PR:

- run `python -m pytest tests -q` and `python -m ruff check app tests`,
- keep ejection changes covered by tests — especially the gcode/animation
  parity test,
- document new printer data with its source.

## License

MIT — see [LICENSE](LICENSE).

PlateLoop moves your printer's toolhead and bed. Verify container placement,
plate adhesion and the first full cycle for every material and model. Use at
your own risk.
