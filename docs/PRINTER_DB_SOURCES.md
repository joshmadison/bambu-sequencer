# Printer database sources (`app/printers.json`)

Provenance and verification notes for every value in the printer database.
Bambu Lab values re-fetched **2026-10-01** (database version 3); the other
vendor families added **2026-10-21** (database version 5).

## 1. Primary sources

| Source | Commit | Used for |
|---|---|---|
| BambuStudio (first-party) | [`da8b44e`](https://github.com/bambulab/BambuStudio/commit/da8b44ee34dd349f2ae0df3f1cbae366df482354) (2026-09-28) | all Bambu machine values, park positions, toolhead rows |
| OrcaSlicer | [`03377a0`](https://github.com/OrcaSlicer/OrcaSlicer/commit/03377a0242d95b1bbad7bce004254a1f08b09661) (2026-10-01) | Bambu cross-check; same values except P2S clearance radius |
| OrcaSlicer | [`2457173`](https://github.com/OrcaSlicer/OrcaSlicer/commit/2457173764b4f3a02f488585642219d8a6098012) (2026-10-20) | machine profiles + `model_id`s for Prusa, Creality, Elegoo, Anycubic, QIDI, Sovol, Voron |
| Vendor specifications | linked per family below | kinematics (`bedslinger` / `corexy`) where the profile has no `printer_structure` |
| Native A1 gcode | real exported `lenker.gcode.3mf` (workspace) | rear park/wipe Y position (Q10 kinematics) |

Bambu profile paths: `resources/profiles/BBL/machine/`

- base machine config: `Bambu Lab <MODEL> 0.4 nozzle.json`
- machine model (id + name): `Bambu Lab <MODEL>.json` (`model_id`)
- inherited common: `fdm_bbl_3dp_001_common.json` (A1/X1/P1/P2/X2 series),
  `fdm_bbl_3dp_002_common.json` (H2 series), `fdm_machine_common.json`
- end gcode: `Bambu Lab <MODEL> 0.4 nozzle template machine_end_gcode.json`

Non-Bambu profile paths: `resources/profiles/<Vendor>/machine/…` (Elegoo uses
sub-folders, e.g. `Elegoo/machine/ECC/…`). Common parents used by the added
models:

- Prusa: `fdm_machine_common_mk4s`, `fdm_machine_common_nextruder`,
  `fdm_machine_common_xl`, `fdm_machine_common`
- Creality: `fdm_creality_common`, `fdm_machine_common`
- Elegoo: `fdm_elegoo_3dp_001_common`, `fdm_elegoo_common`
- Anycubic: `fdm_machine_common`
- QIDI: `fdm_qidi_x3_common`, `fdm_qidi_common`, `fdm_machine_common`
- Sovol: `fdm_machine_common`
- Voron: `fdm_klipper_common`, `fdm_machine_common`

Example URLs (commit-pinned):

- https://github.com/bambulab/BambuStudio/blob/da8b44ee34dd349f2ae0df3f1cbae366df482354/resources/profiles/BBL/machine/Bambu%20Lab%20A1%200.4%20nozzle.json
- https://github.com/OrcaSlicer/OrcaSlicer/blob/2457173764b4f3a02f488585642219d8a6098012/resources/profiles/Prusa/machine/Prusa%20MK4S%200.4%20nozzle.json
- https://github.com/OrcaSlicer/OrcaSlicer/blob/2457173764b4f3a02f488585642219d8a6098012/resources/profiles/Creality/machine/Creality%20K1%20(0.4%20nozzle).json
- https://github.com/OrcaSlicer/OrcaSlicer/blob/2457173764b4f3a02f488585642219d8a6098012/resources/profiles/Elegoo/machine/ECC/Elegoo%20Centauri%20Carbon%200.4%20nozzle.json

All values are *resolved*: child overrides parent (`inherits` chain), e.g.
`Bambu Lab X1 Carbon 0.4 nozzle` → `fdm_bbl_3dp_001_common` →
`fdm_machine_common`, and `Prusa MK4S 0.4 nozzle` →
`fdm_machine_common_mk4s` → `fdm_machine_common_nextruder` →
`fdm_machine_common`.

Vendor spec pages used only for kinematics of models whose profile omits
`printer_structure`:

| Family | Source |
|---|---|
| Prusa | CORE One and XL are CoreXY; MK4S/MK3S/MK3.5/MINI are i3 bedslingers (Prusa product pages) |
| Creality | K1/K1C/K1 Max/K2 Plus are CoreXY; Ender-3 V3/V3 KE are i3 (Creality product pages) |
| Anycubic | Kobra S1/S1 Max are CoreXY, Kobra 3/3 Max are i3 (profiles carry `printer_structure`, cross-checked) |
| QIDI | Q1 Pro, X-Plus 4, X-Max 4, Q2 are CoreXY (QIDI product pages / profile) |
| Sovol | SV08, SV08 MAX, Zero are CoreXY (Sovol specs, SV08 Klipper `kinematics: corexy`); SV06 ACE is i3 |
| Voron | 2.4, Trident and 0.1 are CoreXY |

## 2. Schema (version 5) and field mapping

`printers.json` groups each printer into `machine` / `kinematics` /
`toolhead` (plus `vendor`, `suitability*` and `source`). Top-level maps are the
single source of truth for display text: `vendors` (label + name prefixes +
order), `structures` (label + mechanism_short), `suitability_levels` (label +
description) and `provenance_levels` (`verified_gcode` / `end_gcode_park` /
`no_park` / `estimate`). `/api/printers` flattens the structure for the UI and
adds the vendor label/counts.

| `printers.json` field | Slicer key | Notes |
|---|---|---|
| `machine.printable.x/.y` | `printable_area` corners | e.g. `["0x0","256x0","256x256","0x256"]` → 256×256 |
| `machine.printable.z` | `printable_height` | max object height in the slicer |
| `machine.structure` | `printer_structure` | `i3` → `bedslinger`, `corexy`/`corexz` → `corexy`; missing in most non-BBL profiles → vendor specs |
| `machine.bed_exclude.x/.y` | `bed_exclude_area` | front-left corner of the excluded polygon (Bambu X1/P1: 18×28); `0/0` = none |
| `toolhead.clearance_radius` | `extruder_clearance_max_radius` **or** `extruder_clearance_radius` | non-BBL profiles use the shorter key |
| `toolhead.dist_to_rod` | `extruder_clearance_dist_to_rod` **or** `extruder_clearance_height_to_rod` | non-BBL profiles use height-to-rod |
| `toolhead.body_height` | `nozzle_height` | missing except Anycubic (4.0) → 5.0 app estimate |
| `toolhead.footprint.width/.depth` | `machine_head_with_fans_polygon` | **absent from every checked profile** (BBL and non-BBL) → 40×35 mm app estimate |
| `toolhead.estimated` | — | `true` for the non-Bambu rows: body height/footprint are assumptions |
| `kinematics.rear_command_limit` | — | parked toolhead Y behind the plate; bed depth when the vendor has no park (`no_park`) |
| `kinematics.rear_command_limit_source` | — | key into `provenance_levels` |
| `kinematics.front_lip_y` | — | commanded Y at the plate front edge |
| `aliases` | `model_id` / `printer_model` | all Bambu and non-Bambu ids verified against the profile/model files |

`bed_exclude` for non-Bambu models is stored as `0/0`: the values in those
profiles are frame/wipe zones (e.g. Anycubic's full-bed frame, QIDI's rear
wiper strip, Elegoo's rear-right corner), not the front-left keep-free area
this schema describes.

## 3. Printer-model ids (`printer_model_id`)

OrcaSlicer writes `printer_model_id` from the selected machine model's
`model_id`:

- `src/slic3r/GUI/Plater.cpp:20512` — `printer_model_id = …get_printer_type(…)`
- `src/libslic3r/Preset.cpp:1003` — `get_printer_type()` returns
  `vendor_model.model_id` for the selected `printer_model`

Examples of the ids stored in `aliases`:

| Family | `model_id` examples |
|---|---|
| Bambu Lab | `N2S` (A1), `C12` (P1S), `BL-P001` (X1C) |
| Prusa | `MK4S`, `MK3S`, `MK3.5`, `MINI`, `Prusa_CORE_One`, `Prusa XL` |
| Creality | `Creality-K1`, `Creality-K1C`, `Creality-K1-Max`, `Creality-K2-Plus`, `Creality-Ender3-V3` |
| Elegoo | `Elegoo-CC`, `Elegoo-C`, `Elegoo-N4Pro`, `Elegoo-N4Max` |
| Anycubic | `Anycubic Kobra 3`, `Anycubic Kobra S1` (the full model name is the id) |
| QIDI | `Qidi-Q1Pro`, `Qidi-XPlus-4`, `Qidi-XMax-4`, `Qidi-Q2` |
| Sovol | `Sovol-SV06-ACE`, `Sovol-SV08`, `Sovol-SV08 MAX`, `Sovol-Zero` |
| Voron | `Voron2_350`, `Voron2_Trident_300`, `Voron0` |

`printer_model` (full name, e.g. `Prusa MK4S`) is also stored as an alias, so
the UI resolves both. `Prusa MINI IS` shares the MINI profile geometry and is
mapped to `MINI`.

## 4. Rear park position (`kinematics.rear_command_limit`)

Evidence: the highest Y commanded in the native `machine_end_gcode`
(and the real A1 file). The printer itself moves there, so the position is
firmware-accepted. The provenance is machine-readable per printer via
`rear_command_limit_source` and shown in the app ("verified" / "from end
gcode" / "no park zone" / "estimated").

Bambu Lab:

| Model(s) | Park Y | Source |
|---|---|---|
| A1 | 262.5 | `verified_gcode` — real sliced A1 gcode: `G1 Y262.5 F6000` (wipe) |
| A1 mini | 185 | `end_gcode_park` — template `G1 Y185` / `G1 X85 Y185` |
| X1, X1C, X1E, P1P, P1S | 265 | `end_gcode_park` — template `G1 Y265 F3000` (behind the 256 plate) |
| A2L, P2S, X2D, H2S, H2D, H2D Pro, H2C | bed depth + 6 mm | `estimate` — no behind-plate park in the end gcode |

Other vendors (added 2026-10-21):

| Model(s) | Park Y | Bed Y | Source |
|---|---|---|---|
| Elegoo Centauri, Centauri Carbon | 264.5 | 256 | `end_gcode_park` — `G1 Y264.5 F1200` (8.5 mm behind the plate) |
| Anycubic Kobra S1 | 270 | 250 | `end_gcode_park` — park 20 mm behind the plate |
| Prusa MK4S / MK3S / MK3.5 / MINI | 170 / 200 / 201 / 178 | 210 / 210 / 210 / 180 | `no_park` — park is *inside* the bed |
| Prusa CORE One, XL | −9 / 350 | 220 / 360 | `no_park` |
| Creality K1 family, K2 Plus | none | 220–350 | `no_park` — no Y park in the end gcode |
| Creality Ender-3 V3 KE | 218 | 220 | `no_park` |
| Elegoo Neptune 4 Pro / Max | none | 230 / 426 | `no_park` |
| Anycubic Kobra 3 / 3 Max / S1 Max | 220 / 420 / none | 255 / 420 / 350 | `no_park` |
| QIDI Q1 Pro / X-Plus 4 / X-Max 4 / Q2 | 0 / 290 / 380 / 270 | 245 / 305 / 390 / 270 | `no_park` |
| Sovol SV06 ACE / SV08 / SV08 MAX / Zero | none | 220–500 | `no_park` |
| Voron 2.4 / Trident / 0.1 | none | 350 / 300 / 120 | `no_park` |

`no_park` means there is **no position behind the printable area**: the pusher
starts at the rear bed edge (`rear_command_limit = printable.y`). The generated
gcode therefore needs the rear-most part to keep at least the toolhead
footprint (35 mm in the app estimate) clear of the rear edge. Every non-Bambu
model is `needs-test` until this is confirmed on hardware.

`front_lip_y = 0` = commanded Y at the plate's front edge (push end);
verified on A1, assumption for all others.

## 5. Toolhead footprint (`toolhead.footprint.width` / `.depth`)

**There is no public specification for the BBL toolhead width/depth.**

- `machine_head_with_fans_polygon` (the key other vendors use) is **absent
  from all 162 BBL machine profiles** and also from the OrcaSlicer Prusa,
  Creality, Elegoo, Anycubic, QIDI, Sovol and Voron machine profiles that were
  checked (2026-10-21).
- `resources/profiles/BBL/bbl-3dp-hotend.stl` is an illustration, not to
  scale (measured bbox 4.1 × 3.5 × 12 mm) — rejected as a source.
- Therefore `toolhead.footprint` holds conservative **app estimates**. Bambu
  rows use per-family 36/28, 36/25 or 40/28 mm; all non-Bambu rows use
  40 × 35 mm and set `toolhead.estimated: true`. Replace them with a caliper
  measurement if precision is needed.
- `body_height` comes from `nozzle_height` where available (Anycubic 4.0); all
  other non-Bambu rows use a 5.0 mm estimate for the animation.

## 6. Pipeline caveats for non-Bambu files

OrcaSlicer exports the same `.gcode.3mf` container for every vendor
(`Metadata/plate_N.gcode`, `slice_info.config`, `project_settings.config`), so
the existing reader applies. However:

- Bambu G-code contains `; EXECUTABLE_BLOCK_START/END` and
  `; MACHINE_START_GCODE_END`; Prusa/Creality/… Orca output does not.
  `app/gcode.py` then uses its **fallback append mode**: the native shutdown is
  split at `M140 S0`, and the full start gcode is replayed for every print
  (no purge-free variant). Expect longer per-copy times.
- `max_z_height` can be missing from non-Bambu headers; the builder then falls
  back to 20 mm and surfaces a warning. Check the generated file.
- Native PrusaSlicer `.gcode` / `.bgcode` (and unsliced Prusa 3MF projects)
  are **not supported** yet: they carry neither plate G-code nor the
  `slice_info.config` object boxes. Slice non-Bambu models with OrcaSlicer and
  export **Plate as .gcode.3mf**.

Online sample search (2026-10-21): no public Orca-sliced `.gcode.3mf` for a
Prusa/Creality/Elegoo/Anycubic/QIDI/Sovol/Voron machine was found (the
OrcaSlicer repo ships only `OrcaSliced.3mf`, a model project without G-code).
The `printer_model_id` mapping is therefore sourced from the OrcaSlicer code
paths above, not from a sample file. Attach a real sliced file when available.

## 7. Verification

Automated regression in `tests/test_pipeline.py`:

- `test_printer_db_valid` — schema vocabulary, vendor ids, bed/park invariants
  (`no_park` ⇔ rear limit equals bed depth), toolhead values.
- `test_printer_vendors_and_aliases` — all vendors populated, model ids and
  full names resolve (`Creality-K1`, `Elegoo-CC`, `Prusa MK4S`, …).

`tests/test_api.py::test_printers_api_flat_contract` checks the flat API view,
including vendor grouping and the `no_park` label.

Re-fetch check:

```bash
# Bambu
curl -sL "https://raw.githubusercontent.com/bambulab/BambuStudio/master/resources/profiles/BBL/machine/Bambu%20Lab%20A1%200.4%20nozzle.json"
# non-Bambu (OrcaSlicer, pin the commit used above)
curl -sL "https://raw.githubusercontent.com/OrcaSlicer/OrcaSlicer/2457173764b4f3a02f488585642219d8a6098012/resources/profiles/Prusa/machine/Prusa%20MK4S%200.4%20nozzle.json"
```

To produce a real test file for a non-Bambu model:

```bash
orca-slicer --slice --export-3mf /tmp/out.gcode.3mf --load-settings machine.json,process.json model.stl
```

## 8. Changelog

| Date | Version | Change |
|---|---|---|
| 2026-09-29 | 2 | initial OrcaSlicer fetch + alias table |
| 2026-10-01 | 3 | full re-fetch & verification against BambuStudio master; added `printable.z`; corrected values (park Y, exclusions, P2S radius, X2D/H2 body height, rod distances) |
| 2026-10-01 | 4 | schema restructure: grouped `machine` / `kinematics` / `toolhead`; top-level label maps; machine-readable park provenance; footprint `estimated` |
| 2026-10-21 | 5 | multi-vendor: 31 models for Prusa, Creality, Elegoo, Anycubic, QIDI, Sovol and Voron from OrcaSlicer `2457173`; top-level `vendors` (label/prefix/order) and `printer.vendor`; new `no_park` provenance; `toolhead.estimated`; model-id + full-name aliases; Printers page grouped by vendor with filter and remembered state |
