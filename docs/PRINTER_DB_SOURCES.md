# Printer database sources (`app/printers.json`)

Provenance and verification notes for every value in the printer database.
Re-fetched **2026-10-01** (database version 3).

## 1. Primary sources

| Source | Commit | Used for |
|---|---|---|
| BambuStudio (first-party) | [`da8b44e`](https://github.com/bambulab/BambuStudio/commit/da8b44ee34dd349f2ae0df3f1cbae366df482354) (2026-09-28) | all machine values, park positions, toolhead rows |
| OrcaSlicer | [`03377a0`](https://github.com/OrcaSlicer/OrcaSlicer/commit/03377a0242d95b1bbad7bce004254a1f08b09661) (2026-10-01) | cross-check; same values except P2S clearance radius |
| Native A1 gcode | real exported `lenker.gcode.3mf` (workspace) | rear park/wipe Y position (Q10 kinematics) |

Profile files per printer: `resources/profiles/BBL/machine/`

- base machine config: `Bambu Lab <MODEL> 0.4 nozzle.json`
- machine model (id + name): `Bambu Lab <MODEL>.json` (`model_id`)
- inherited common: `fdm_bbl_3dp_001_common.json` (A1/X1/P1/P2/X2 series),
  `fdm_bbl_3dp_002_common.json` (H2 series), `fdm_machine_common.json`
- end gcode: `Bambu Lab <MODEL> 0.4 nozzle template machine_end_gcode.json`

Example URLs (commit-pinned):

- https://github.com/bambulab/BambuStudio/blob/da8b44ee34dd349f2ae0df3f1cbae366df482354/resources/profiles/BBL/machine/Bambu%20Lab%20A1%200.4%20nozzle.json
- https://github.com/OrcaSlicer/OrcaSlicer/blob/03377a0242d95b1bbad7bce004254a1f08b09661/resources/profiles/BBL/machine/Bambu%20Lab%20A1%200.4%20nozzle.json

All values are *resolved*: child overrides parent (`inherits` chain), e.g.
`Bambu Lab X1 Carbon 0.4 nozzle` → `fdm_bbl_3dp_001_common` → `fdm_machine_common`.

## 2. Schema (version 4) and field mapping

`printers.json` groups each printer into `machine` / `kinematics` /
`toolhead` (plus `suitability*` and `source`). Top-level maps are the single
source of truth for display text: `structures` (label + mechanism_short),
`suitability_levels` (label + description) and `provenance_levels`
(verified_gcode / end_gcode_park / estimate). `/api/printers` flattens the
structure for the UI.

| `printers.json` field | Slicer key | Notes |
|---|---|---|
| `machine.printable.x/.y` | `printable_area` corners | e.g. `["0x0","256x0","256x256","0x256"]` → 256×256 |
| `machine.printable.z` | `printable_height` | max object height in the slicer |
| `machine.structure` | `printer_structure` | `i3` → `bedslinger`, `corexy` → `corexy` |
| `machine.bed_exclude.x/.y` | `bed_exclude_area` | front-left corner of the excluded polygon (X1/P1: 18×28); `0/0` = no exclusion |
| `toolhead.clearance_radius` | `extruder_clearance_max_radius` | swing radius around the nozzle |
| `toolhead.dist_to_rod` | `extruder_clearance_dist_to_rod` | nozzle-to-rod distance |
| `toolhead.body_height` | `nozzle_height` | nozzle tip → toolhead body |
| `toolhead.footprint.width/.depth` | — | **app estimate**, `estimated: true` (see section 4) |
| `kinematics.rear_command_limit` | — | parked toolhead Y behind the plate (see section 3) |
| `kinematics.rear_command_limit_source` | — | `verified_gcode` / `end_gcode_park` / `estimate` |
| `kinematics.front_lip_y` | — | commanded Y at the plate front edge (push end) |
| `aliases` (`N2S`, …) | `model_id` in the machine-model file | all 14 ids verified against both repos |

## 3. Rear park position (`kinematics.rear_command_limit`)

Evidence: the highest Y commanded in the native `machine_end_gcode`
(and the real A1 file). The printer itself moves there, so the position is
firmware-accepted. The provenance is machine-readable per printer via
`rear_command_limit_source` and shown in the app ("verified" / "from end
gcode" / "estimated").

| Model(s) | Park Y | Source |
|---|---|---|
| A1 | 262.5 | `verified_gcode` - real sliced A1 gcode: `G1 Y262.5 F6000` (wipe) |
| A1 mini | 185 | `end_gcode_park` - template `G1 Y185` / `G1 X85 Y185` |
| X1, X1C, X1E, P1P, P1S | 265 | `end_gcode_park` - template `G1 Y265 F3000` (behind the 256 plate) |
| A2L, P2S, X2D, H2S, H2D, H2D Pro, H2C | bed depth + 6 mm | `estimate` - no behind-plate park in the end gcode (max Y is inside the plate area or absent); must be confirmed on hardware |

`front_lip_y = 0` = commanded Y at the plate's front edge (push end);
verified on A1, assumption for all others.

## 4. Toolhead footprint (`toolhead.footprint.width` / `.depth`)

**There is no public specification for the BBL toolhead width/depth.**

- `machine_head_with_fans_polygon` (the key other vendors use) is **absent
  from all 162 BBL machine profiles** in both repositories (checked).
- `resources/profiles/BBL/bbl-3dp-hotend.stl` is an illustration, not to
  scale (measured bbox 4.1 × 3.5 × 12 mm) - rejected as a source.
- Therefore `toolhead.footprint` (36/28, 36/25, 40/28 mm) holds conservative
  **app estimates** (`estimated: true`), used for the animation and the
  push-lane warning. Replace them with a caliper measurement if precision
  is needed.

## 5. Known discrepancies / decisions

| Topic | Detail | Decision |
|---|---|---|
| P2S clearance radius | BambuStudio `72`, OrcaSlicer `68` | used `72` (first-party) - re-check on the next sync |
| X1 family max height | profile `250` vs marketing `256` | used the slicer's `250` (source of truth for slicing) |
| Park estimate models | no behind-plate park in native end gcode | `rear_command_limit` stays an estimate, `suitability_note` says so |
| A1 bed exclusion | current profiles: `bed_exclude_area: []` | stored `0/0`; older profile versions had 28×28 (superseded) |
| A2L end-gcode moves | `G1 Y295` / `G0 Y319` are bed-away/maintenance moves, not a rear park | treat as estimate |

## 6. Verification

Automated regression: `tests/test_pipeline.py::test_printer_db_corrected_values`
checks the values corrected in version 3 (park Y, exclusions, P2S radius,
X2D/H2 body height, rod distances). Re-fetch check:

```bash
# profiles (requires network access to raw.githubusercontent.com)
curl -sL "https://raw.githubusercontent.com/bambulab/BambuStudio/master/resources/profiles/BBL/machine/Bambu%20Lab%20A1%200.4%20nozzle.json"
# then compare printer_structure / printable_area / extruder_* / nozzle_height
```

## 7. Changelog

| Date | Version | Change |
|---|---|---|
| 2026-09-29 | 2 | initial OrcaSlicer fetch + alias table |
| 2026-10-01 | 3 | full re-fetch & verification against BambuStudio master; added `printable.z` (max height); filled `clearance_dist_to_rod` (X1/P1/P2/X2D); corrected `nozzle_height` (X2D/H2S/H2D/H2D Pro/H2C 4.2 → 4.0); corrected P2S clearance radius (68 → 72); corrected bed exclusions (A1, A1 mini, P2S, X2D → none); rear park from native end gcode (A1 262.5, A1 mini 185, X1/P1 family 265); suitability notes now state which park values are estimates |
| 2026-10-01 | 4 | schema restructure: grouped `machine` / `kinematics` / `toolhead`; top-level `structures` / `suitability_levels` / `provenance_levels` as single source for labels; machine-readable park provenance; footprint flags `estimated: true`; `/api/printers` flattens the schema and exposes labels |
