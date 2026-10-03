# bambu-sequencer

Self-hosted web app that turns sliced Bambu Lab print files into an
**automatic multi-copy sequence**: print → cool → the toolhead pushes the
part off the plate's front edge into a container → re-heat → next copy.
[![CI](https://github.com/joshmadison/bambu-sequencer/actions/workflows/ci.yml/badge.svg)](../../actions)

Multi-plate files are supported too: configure the prints per plate and the
app merges everything into ONE file that runs plate after plate.

**No extra hardware needed** - the push is done by the printer's own
toolhead (A1 bedslinger kinematics: the bed moves, the toolhead stands
still). Everything runs locally; no data ever leaves your machine.

> ⚠️ **Never leave the printer unattended on the first full cycle.**
> This tool manipulates your printer's motion commands. See
> [Safety](#safety-first).

## How it works

```
print ends
   │
   ▼
retract filament ── lift toolhead to safe height ── bed fully forward
   │                                                    (toolhead parks
   ▼                                                     BEHIND the plate)
pick the rear-most object ── center X on it ── cool down + safety wait
   │
   ▼
push: bed retracts fully ── the object slides off the plate's front edge
   │                        into your container
   ▼
lift + re-park ── next object ... (until the plate is empty)
   │
   ▼
re-heat ── next copy starts
```

The exact sequence is documented in
[docs/ejection_logic.md](docs/ejection_logic.md). The web UI includes a
to-scale animation of the ejection for **your** uploaded file before you
build anything.

## Safety first

- **Always watch the first full cycle** before leaving the printer
  unattended.
- Objects must be **tall enough** (default ≥ 7 mm) so the toolhead cannot
  jam on short parts.
- Point a container at the front of the printer; the part lands there.
- Objects **sharing one push lane** (overlapping X range) can cascade off
  together or jam; the app warns about such layouts - watch their first
  cycle closely.
- Verified on **Bambu Lab A1**. A1 mini / A2L share the same mechanics
  (expected to work). CoreXY models (X1/P1/H2 series) move the toolhead
  instead of the bed - plausible but needs a test print (see the Printers
  page in the app).

## Run

### Docker (recommended, also for a homeserver)

```bash
git clone https://github.com/joshmadison/bambu-sequencer.git
cd bambu-sequencer
docker compose up -d
# open http://localhost:8080
```

The compose file ships sane defaults (non-root container, upload cap
300 MB, healthcheck, `restart: unless-stopped`). To change the upload cap:

```yaml
environment:
  - MAX_UPLOAD_MB=600
```

### Local (no Docker)

```bash
cd bambu-sequencer
python -m venv venv
venv/Scripts/pip install -r requirements.txt   # Linux/macOS: venv/bin/pip
venv/Scripts/python -m app.main                # Linux/macOS: venv/bin/python -m app.main
# open http://localhost:8000
```

### Homeserver notes

- The app binds to `0.0.0.0` inside the container; use your compose port
  mapping to expose it (default `8080`).
- If you expose it beyond your LAN, put a reverse proxy with auth in front
  (the app itself has no login) - e.g. traefik, caddy or nginx.
- No persistence is needed: uploads are processed in memory, nothing is
  written to disk.

## Usage

1. Slice your object once in **Bambu Studio** (or OrcaSlicer).
2. Export: *File → Export → Plate as .gcode.3mf*.
3. Upload in this app, set the prints per plate, push height, lift height,
   ejection bed temperature and safety wait, **Build sequence**.
4. Download the `*_x<N>.gcode.3mf` and print it on your Bambu printer like
   any other file.

### Multi-plate files

Export with *File → Export → All plates as .gcode.3mf* and upload it. The
app shows every plate with its own preview, objects and a **prints** field
(0 = skip the plate). The generated file is a normal single-plate
`.gcode.3mf` whose gcode runs the plates back to back: each plate is
printed as often as configured and every print is followed by that plate's
ejection push. The first-print purge/calibration strip runs only once for
the whole sequence; homing/leveling still runs before every print, and the
native shutdown only at the very end.

## Supported printers

The printer database (`app/printers.json`, provenance in
[docs/PRINTER_DB_SOURCES.md](docs/PRINTER_DB_SOURCES.md)) covers 14 Bambu
Lab models (A1, A1 mini, A2L, P1P, P1S, P2S, X1, X1C, X1E, X2D, H2S, H2D,
H2D Pro, H2C) with slicer-derived geometry. Filenames/printer-model
identifiers from Bambu Studio and OrcaSlicer are resolved automatically -
you never need to pick your printer manually.

Push behavior is **verified** on the A1 from a real printed file; for
CoreXY models the park positions come from each machine's end gcode, and
the app marks them `verified` / `from end gcode` / `estimated` so you know
what is proven.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q          # 57 tests
python -m ruff check app tests     # lint
```

See [docs/ejection_logic.md](docs/ejection_logic.md) for the ejection
design and [docs/PRINTER_DB_SOURCES.md](docs/PRINTER_DB_SOURCES.md) for
where every printer-database value comes from.

## License

MIT - see [LICENSE](LICENSE). Printed parts eject at your own setup's
risk: verify container placement, plate adhesion and the first cycle per
material.