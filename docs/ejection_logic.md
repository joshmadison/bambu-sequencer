# Ejection logic (V2)

> PlateLoop - how the toolhead pushes printed parts off the plate,
> one by one, without any extra hardware.
>
> This document is the design reference for `app/gcode.py`
> (`build_ejection_snippet` / `ejection_keyframes`). The animation frames
> are validated against the generated gcode in the test suite.

---

## 1. Purpose

The ejection logic describes what happens between two print copies:

**print ends -> parts pushed off the plate individually -> plate empty ->
next print starts.**

It is designed for Bambu Lab **bedslinger** mechanics (A1): the **bed**
moves in Y, the **toolhead** (with the pusher edge mounted on it) parks in
place. CoreXY models (X1/P1/H2) move the toolhead instead - the same
commands produce the same physical result, which is why they are supported
with a "needs test print" caveat in the app.

---

## 2. Kinematics (A1)

Derived from a real, natively sliced A1 `.gcode.3mf` file.

| Command | Meaning |
|---|---|
| `Y = rear_limit` (~262) | Bed fully FORWARD - toolhead is BEHIND the plate (park position) |
| `Y = front_lip` (~0) | Bed fully BACK - toolhead is in FRONT of the plate (push end) |
| Y decreasing | Toolhead pushes the object FORWARD over the plate's front edge |

**Rule:** the pusher always starts behind the plate (`Y = rear_limit`),
where nothing stands. The push = Y monotonically decreasing to
`front_lip`.

**Other vendors (schema v5).** Only some profiles carry a park position
beyond the printable area (Elegoo Centauri/Carbon and Anycubic Kobra S1 do).
For every other non-Bambu model the database stores
`rear_command_limit_source = no_park` and `rear_limit = printable.y`: the
pusher starts at the rear bed edge, so the rear-most object must keep the
toolhead footprint (~35 mm) clear of that edge. All non-Bambu models are
marked *needs a test print* until this is confirmed on hardware.

---

## 3. Parameters

### 3.1 UI parameters (set by the user)

| Parameter | Code name | Description | Default |
|---|---|---|---|
| Lift height | `t_lift` | Extra height above the tallest bounding box on the plate | 5 mm |
| Push height | `push_z` | Gap between pusher and print plate while descending | 0.4 mm |
| Target temperature | `cool_temp_bed` | Bed temperature to cool down to (temperature mode); bed target in fixed-time mode | 30 °C |
| Cooldown time | `cool_time_s` | `0` = wait for `cool_temp_bed` (`M190`); `> 0` = fixed cooldown in seconds (`G4`), never stalls | 300 s (API/UI); 0 in the library functions |
| Safety wait | `safety_wait_s` | Wait time after reaching the target temperature | 60 s |

### 3.2 Printer parameters (from `printers.json`, see
[PRINTER_DB_SOURCES.md](PRINTER_DB_SOURCES.md))

| Parameter | Description | A1 value |
|---|---|---|
| `rear_command_limit` | Y position: bed fully extended (pusher behind the plate) | 262.5 mm |
| `front_lip_y` | Y position: plate front edge (push end) | 0 mm |
| `printable.x` | Printable width | 256 mm |
| `printable.y` | Printable depth | 256 mm |

If no park zone exists (`rear_command_limit_source = no_park`), the pusher
starts at `rear_command_limit = printable.y` instead.

### 3.3 Parsed from the 3MF file

| Source | Description |
|---|---|
| `parse_object_bboxes()` | Bounding boxes of all objects: `x_min`, `x_max`, `y_min`, `y_max` |
| `max_z_height` | Tallest object height on the plate (gcode header) |

### 3.4 Derived values

| Value | Formula | Description |
|---|---|---|
| `safe_z` | `max_z_height + t_lift` | Height where the pusher clears every object |
| `push_end_y` | `front_lip_y` | Y end position of the push (plate fully retracted) |
| `plate_center_x` | `printable.x / 2` | X center of the print plate |

---

## 4. Flow

### Phase 1 - preparation (once, after the print ends)

| Step | Action | Parameter |
|---|---|---|
| 1 | **Print done** - last gcode move finished, retract filament | `E-6` (fixed) |
| 2 | **Lift the toolhead** to `safe_z` | `safe_z = max_z_height + t_lift` |
| 3 | **Bed fully forward** - Y to `rear_limit` (pusher behind the plate) | `rear_limit` |
| 4 | **Toolhead X to plate center** | `plate_center_x` |
| 5 | **Descend the toolhead** to `push_z` (pusher at min gap above the plate) | `push_z` |

### Phase 2 - object selection + ejection (repeat until the plate is empty)

| Step | Action | Detail |
|---|---|---|
| 6 | **Find the next object** | The part whose bounding box edge is closest to the toolhead in Y (at `Y = rear_limit` = the rear-most object). |
| 6.1 | *Tie-breaker* | On equal distance - the part whose bbox center is **closest to X = 0**. |
| 7 | **Toolhead X position** | X to the bbox center of the chosen part. |
| 8 | **Cool down** | Fixed time: `M140 S<cool_temp_bed>` + `G4 S<cool_time_s>`. Temperature mode: `M140` + `M190` wait for `cool_temp_bed`. G-code has no "wait for temperature with a timeout". |
| 9 | **Safety wait** | Wait `safety_wait_s` (`G4`). |
| 10 | **Push** | Y fully retract to `push_end_y` - the part is pushed forward over the plate's front edge. |
| 11 | **Lift + bed fully forward** | First lift to `safe_z`, then Y back to `rear_limit` - this also applies after the last object: the pusher NEVER sweeps the plate at `push_z`. |
| - | **Continue at step 6** until the plate is empty. | |

### Phase 3 - closing

| Step | Action |
|---|---|
| 12 | **Plate empty** -> re-heat (bed + nozzle to print temperature) + next print starts. |

---

## 5. Object selection (detail)

Objects are selected **dynamically in every cycle**, not pre-sorted.

**Criterion:** smallest Y distance between the bbox edge (`y_max`) and the
toolhead at `Y = rear_limit`:

```
distance = rear_limit - object.y_max
```

The smallest distance = the rear-most object = pushed first.

**Tie-breaker (6.1):** on equal distance, pick the object whose bbox
center `(x_min + x_max) / 2` is closest to `X = 0`.

**Push-lane limitation:** if the X bands of two parts overlap, the rear
part shoves the front part along (cascade) or both jam - the pusher cannot
travel over standing parts. The app **warns** about such layouts before
printing (no hard block; a cascade may still work). There is no
collision-free push path for these arrangements.

---

## 6. Multi-plate sequencing

`build_multi_plate_sequence` merges several plate gcodes into ONE job:

- plates run in ascending order, each with its own number of prints
  (`plate_prints`, 0 = skip),
- every print (including the very last) is followed by that plate's
  ejection push,
- the first-print purge/calibration strip runs **exactly once** for the
  whole sequence; every later print uses its plate's purge-free start
  (so the purge never extrudes onto an ejected cold part),
- re-heat targets the NEXT job's plate temperatures,
- the native shutdown runs only after the very last job.