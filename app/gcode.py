"""Gcode manipulation for real Bambu Studio sliced files.

Ejection logic V2 (see docs/ejection_logic.md):
- Phase 1 (once): retract filament -> lift to safe_z -> bed fully forward ->
  center X -> descend to push_z.
- Phase 2 (repeat until the plate is empty): pick the next object -> X to
  its bbox center -> cool down -> safety wait -> push until
  front_lip_y -> bed fully forward (re-park).
- Phase 3: re-heat + next print (in build_multi_plate_sequence).

Kinematics (A1 bedslinger):
- Y = rear_limit (~262): bed fully FORWARD, toolhead behind the plate (park)
- Y = front_lip_y (~0): bed fully BACK, toolhead in front of the plate (push end)
- Y decreasing: toolhead pushes the object forward over the plate's front edge
"""
import re

EJECT_BEGIN = "; ===== plateloop auto-ejection (generated) ====="
EJECT_END = "; ===== end auto-ejection ====="

EXEC_START = "; EXECUTABLE_BLOCK_START"
EXEC_END = "; EXECUTABLE_BLOCK_END"


def find_block_indices(lines: list) -> tuple:
    """Return (exec_start_idx, exec_end_idx) or -1s."""
    exec_start = exec_end = -1
    for i, line in enumerate(lines):
        s = line.strip()
        if s == EXEC_START and exec_start == -1:
            exec_start = i
        elif s == EXEC_END:
            exec_end = i
    return exec_start, exec_end


def _shutdown_split_index(lines: list) -> int:
    """First line index of the native shutdown inside the exec block."""
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("M140 S0"):
            return i
    return -1


START_GCODE_END = "; MACHINE_START_GCODE_END"

_PURGE_SECTION_BANNERS = [
    (";===== auto extrude cali start", ";===== auto extrude cali end"),
    (";===== wipe nozzle ", ";===== wipe nozzle end"),
    ("; wipe nozzle ", "; wipe nozzle end"),
    (";===== nozzle load line ", ";===== nozzle load line end"),
    (";===== extrude cali test ", None),
]


def split_start_block(exec_block_lines: list) -> tuple:
    """Split the exec block at '; MACHINE_START_GCODE_END' (A1 native marker)."""
    for i, line in enumerate(exec_block_lines):
        if line.strip() == START_GCODE_END:
            return exec_block_lines[:i], exec_block_lines[i + 1:]
    return list(exec_block_lines), []


def find_purge_zones(exec_block_lines: list) -> list:
    """(start_idx, end_idx) spans of the front-of-plate purge/cali zone."""
    spans = []
    for start_banner, end_banner in _PURGE_SECTION_BANNERS:
        si = -1
        for i, line in enumerate(exec_block_lines):
            s = line.strip()
            if si == -1:
                if s.startswith(start_banner):
                    si = i
            elif end_banner is not None and s.startswith(end_banner):
                spans.append((si, i))
                si = -1
        if si != -1 and end_banner is None:
            ei = next((j for j, ln in enumerate(exec_block_lines) if ln.strip() == START_GCODE_END), len(exec_block_lines))
            if ei > si:
                spans.append((si, ei - 1))
    return spans


def build_purge_free_start(exec_block_lines: list) -> list | None:
    """Machine-start section WITHOUT the first-print purge/cali strip."""
    pre, _post = split_start_block(exec_block_lines)
    if _post and not pre:
        return None
    spans = find_purge_zones(pre)
    if not spans:
        return None
    drop = set()
    for a, b in spans:
        drop.update(range(a, b + 1))
    kept = [ln for i, ln in enumerate(pre) if i not in drop]
    return kept


def parse_print_temps(gcode: str) -> tuple:
    """(bed_temp, nozzle_temp) from real commands of the exec block.

    The M104 patterns are line-anchored: the CONFIG_BLOCK metadata comment
    'machine_end_gcode = ... M104 S0' must never match (it would re-heat to
    a cold nozzle).
    """
    bed = 65
    nozzle = 220
    m = re.search(r"^M140 S(\d+)", gcode, re.MULTILINE)
    if m:
        bed = int(m.group(1))
    m = re.search(r"; first_layer_temperature = (\d+)", gcode)
    if not m:
        m = re.search(r"^M104 S(\d+)\s*;?\s*prepare to print", gcode, re.MULTILINE)
    if not m:
        m = re.search(r"^M104 S(\d+)", gcode, re.MULTILINE)
    if m:
        nozzle = int(m.group(1))
    return bed, nozzle


def parse_max_z_height(gcode: str) -> float:
    """max_z_height from the gcode header ('; max_z_height: 30.00')."""
    m = re.search(r";\s*max_z_height:\s*([\d.]+)", gcode)
    return float(m.group(1)) if m else 0.0


def _strip_native_shutdown(exec_block_lines: list) -> list:
    idx = _shutdown_split_index(exec_block_lines)
    if idx == -1:
        return list(exec_block_lines)
    return list(exec_block_lines[:idx])


def _native_shutdown(exec_block_lines: list) -> list:
    idx = _shutdown_split_index(exec_block_lines)
    if idx == -1:
        return []
    return list(exec_block_lines[idx:])


def _fmt(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _select_next_object(remaining: list, rear_limit: float) -> dict:
    """Dynamic object selection, ejection logic step 6 + 6.1.

    Criterion: smallest Y distance between the bbox edge (y_max) and the
    toolhead at Y = rear_limit.  distance = rear_limit - object.y_max
    The smallest distance = the rear-most object = pushed first.

    Tie-breaker (6.1): on equal distance pick the object whose bbox center
    (x_min + x_max) / 2 is closest to X = 0.
    """
    return min(remaining, key=lambda o: (
        rear_limit - o["y_max"],
        (o["x_min"] + o["x_max"]) / 2,
    ))


def build_ejection_snippet(
    max_z_height: float,
    t_lift: float = 5.0,
    push_z: float = 0.4,
    cool_temp_bed: int = 45,
    safety_wait_s: int = 60,
    push_speed: int = 3000,
    printer: dict | None = None,
    objects: list | None = None,
) -> str:
    """Ejection sequence per ejection_logic.md (V2).

    Phase 1 (once, after the print ends):
      1. Print done - retract filament (E-6, fixed)
      2. Lift the toolhead to safe_z = max_z_height + t_lift
      3. Bed fully forward - Y to rear_limit (toolhead behind the plate)
      4. Toolhead X to plate center - plate_center_x = printable.x / 2
      5. Descend the toolhead to push_z

    Phase 2 (repeat until the plate is empty):
      6. Find the next object (dynamic, see _select_next_object)
      7. Toolhead X to the bbox center
      8. Cool down to cool_temp_bed (M140 + M190 wait)
      9. Safety wait safety_wait_s (G4)
     10. Push - Y to front_lip_y (object slides over the plate's front edge)
     11. Bed fully forward - Y to rear_limit (re-park)
     -> continue at 6

    objects: per-object bboxes from parse_object_bboxes() (list of dicts
    with x_min/x_max/y_min/y_max). None/empty = single-object fallback.
    """
    if printer is None:
        from .printer_db import get_printer
        printer = get_printer("A1")

    machine = printer.get("machine", {})
    kin = printer.get("kinematics", {})
    rear_limit = kin.get("rear_command_limit", 262.0)
    front_lip = kin.get("front_lip_y", 0.0)
    printable_x = machine.get("printable", {}).get("x", 256.0)

    safe_z = round(max_z_height + t_lift, 2)
    push_end_y = round(front_lip, 2)
    plate_center_x = round(printable_x / 2, 2)

    objs = []
    if objects:
        for o in objects:
            xmin = o.get("x_min", 0.0)
            xmax = o.get("x_max", 0.0)
            ymin = o.get("y_min", 0.0)
            ymax = o.get("y_max", 0.0)
            if xmax > xmin and ymax > ymin:
                objs.append({
                    "cx": round((xmin + xmax) / 2, 2),
                    "x_min": xmin, "x_max": xmax,
                    "y_min": ymin, "y_max": ymax,
                })
    if not objs:
        objs = [{"cx": plate_center_x, "x_min": 0.0, "x_max": printable_x,
                 "y_min": 0.0, "y_max": rear_limit * 0.8}]

    remaining = list(objs)

    lines = [
        EJECT_BEGIN,
        (
            f"; ejection V2: {len(objs)} object(s), safe_z={_fmt(safe_z)} "
            f"(max_z={_fmt(max_z_height)} + t_lift={_fmt(t_lift)})"
        ),
        f"; push_z={_fmt(push_z)}, cool_temp={cool_temp_bed}C, safety_wait={safety_wait_s}s",
        "; ===== Phase 1: preparation =====",
        "M400 ; wait for motion buffer clear",
        "G91",
        "G1 E-6.0 F1200 ; step 1: retract filament",
        "G90",
        f"G1 Z{_fmt(safe_z)} F3000 ; step 2: lift toolhead to safe height",
        f"G1 Y{_fmt(rear_limit)} F12000 ; step 3: bed fully forward (park)",
        f"G1 X{_fmt(plate_center_x)} F12000 ; step 4: toolhead to plate center",
        f"G1 Z{_fmt(push_z)} F600 ; step 5: descend to push height",
    ]

    cycle = 0
    while remaining:
        cycle += 1
        obj = _select_next_object(remaining, rear_limit)
        remaining.remove(obj)
        tag = f" (object {cycle}/{len(objs)})" if len(objs) > 1 else ""

        lines.append(f"; ===== Phase 2: ejection cycle {cycle}/{len(objs)} =====")
        lines.append(
            f"G1 X{_fmt(obj['cx'])} F12000 ; step 7{tag}: toolhead to object center "
            f"(cx={_fmt(obj['cx'])})"
        )
        lines.append(f"M140 S{cool_temp_bed} ; step 8: set bed target to cooldown temp")
        lines.append(f"M190 S{cool_temp_bed} ; step 8: wait until bed reached cooldown temp")
        lines.append(
            f"G4 S{int(safety_wait_s)} ; step 9: safety wait {safety_wait_s}s"
        )
        lines.append(
            f"G1 Y{_fmt(push_end_y)} F{push_speed} ; step 10{tag}: push - "
            f"bed fully retracts, toolhead pushes object off plate"
        )
        if remaining:
            lines.append(
                f"G1 Z{_fmt(safe_z)} F3000 ; step 11a: lift to safe height "
                f"(clear remaining objects)"
            )
            lines.append(
                f"G1 Y{_fmt(rear_limit)} F12000 ; step 11b: bed fully forward (re-park)"
            )
            lines.append(
                f"G1 Z{_fmt(push_z)} F600 ; step 11c: descend to push height"
            )
        else:
            lines.append(
                f"G1 Z{_fmt(safe_z)} F3000 ; step 11a: lift to safe height (final)"
            )
            lines.append(
                f"G1 Y{_fmt(rear_limit)} F12000 ; step 11: bed fully forward (final park)"
            )

    lines.append(EJECT_END)
    return "\n".join(lines) + "\n"


def _lane_displacements(objs: list, lane: list, toolhead_y: float) -> dict:
    """Displacement of each lane object while the toolhead's edge is at Y.

    lane: object indices sorted rear-most first (the pushed object first).
    The pushed object moves once the edge passes its rear (y_max); every
    object in front follows after contact, keeping its original gap.
    """
    dys: dict = {}
    prev = None
    for i in lane:
        if prev is None:
            d = min(0.0, toolhead_y - objs[i]["y_max"])
        else:
            gap = objs[prev]["y_min"] - objs[i]["y_max"]
            d = min(0.0, dys[prev] + gap)
        dys[i] = d
        prev = i
    return dys


def _frame_objects(objs: list, dys: dict, gone: set) -> list:
    """Per-frame state of EVERY object: index, current Y range, gone flag.

    dys: {object_index: displacement} for the objects currently being pushed
    (0 for everything else). gone objects are off the plate and not drawn.
    """
    out = []
    for i, ob in enumerate(objs):
        if i in gone:
            out.append({"index": i, "y_front": None, "y_rear": None, "gone": True})
        else:
            d = dys.get(i, 0.0)
            out.append({
                "index": i,
                "y_front": round(ob["y_min"] + d, 2),
                "y_rear": round(ob["y_max"] + d, 2),
                "gone": False,
            })
    return out


def ejection_keyframes(
    max_z_height: float,
    t_lift: float = 5.0,
    push_z: float = 0.4,
    cool_temp_bed: int = 45,
    safety_wait_s: int = 60,
    printer: dict | None = None,
    objects: list | None = None,
    print_end_pos: dict | None = None,
) -> dict:
    """Animation waypoints for the UI - MUST mirror build_ejection_snippet.

    Frames for phase 1 + phase 2 (per object) + closing.
    """
    if printer is None:
        from .printer_db import get_printer
        printer = get_printer("A1")

    machine = printer.get("machine", {})
    kin = printer.get("kinematics", {})
    th = printer.get("toolhead", {})
    rear_limit = kin.get("rear_command_limit", 262.0)
    front_lip = kin.get("front_lip_y", 0.0)
    printable_x = machine.get("printable", {}).get("x", 256.0)

    safe_z = round(max_z_height + t_lift, 2)
    push_end_y = round(front_lip, 2)
    plate_center_x = round(printable_x / 2, 2)

    clearance_radius = th.get("clearance_radius", 73.0)
    body_height = th.get("body_height", 4.76)
    footprint = th.get("footprint", {})
    toolhead_w = footprint.get("width", min(36.0, clearance_radius * 0.5))
    toolhead_d = footprint.get("depth", min(28.0, body_height * 3.0))

    objs = []
    if objects:
        for o in objects:
            xmin = o.get("x_min", 0.0)
            xmax = o.get("x_max", 0.0)
            ymin = o.get("y_min", 0.0)
            ymax = o.get("y_max", 0.0)
            if xmax > xmin and ymax > ymin:
                objs.append({
                    "cx": round((xmin + xmax) / 2, 2),
                    "x_min": xmin, "x_max": xmax,
                    "y_min": ymin, "y_max": ymax,
                })
    if not objs:
        objs = [{"cx": plate_center_x, "x_min": 0.0, "x_max": printable_x,
                 "y_min": 0.0, "y_max": rear_limit * 0.8}]

    remaining = list(objs)
    multi = len(objs) > 1

    end_x = plate_center_x
    end_y_pos = rear_limit * 0.8
    if print_end_pos and (print_end_pos.get("x") is not None or print_end_pos.get("y") is not None):
        end_x = print_end_pos.get("x") if print_end_pos.get("x") is not None else plate_center_x
        end_y_pos = print_end_pos.get("y") if print_end_pos.get("y") is not None else rear_limit * 0.8

    frames = []
    gone_set: set = set()

    def add_frame(fid, label, tx, ty, tz, dys=None, obj_index=None,
                  pushed_list=None, gone_now=None, wait=False):
        frame = {
            "id": fid,
            "label": label,
            "toolhead": {"x": round(tx, 2), "y": round(ty, 2), "z": round(tz, 2)},
            "objects": _frame_objects(objs, dys or {}, gone_set if gone_now is None else gone_now),
        }
        if obj_index is not None:
            frame["obj_index"] = obj_index
        if pushed_list is not None:
            frame["pushed"] = list(pushed_list)
        if wait:
            frame["wait"] = True
        frames.append(frame)

    # Phase 1: every object is on the plate and static (all drawn in 3D).
    # At print end the nozzle sits at the top layer (max_z_height); the real
    # lift to safe_z happens in the "lift" frame / native step 2.
    add_frame("done", "Print finished - retracting filament", end_x, end_y_pos, max_z_height)
    add_frame("lift", f"Lift toolhead to safe height ({_fmt(safe_z)} mm)", end_x, end_y_pos, safe_z)
    add_frame("park", "Bed slides fully forward - toolhead parks behind the plate",
              end_x, rear_limit, safe_z)
    add_frame("center", "Toolhead moves to plate center", plate_center_x, rear_limit, safe_z)
    add_frame("descend", f"Toolhead descends to push height ({_fmt(push_z)} mm)",
              plate_center_x, rear_limit, push_z)

    # Phase 2 (per object). obj_index refers to the input bbox list (same
    # order as objects_bboxes from /api/animation); 'pushed' = cumulative
    # ejected indices. Every frame carries ALL objects with their current
    # positions - objects sharing the push lane are shoved along (cascade).
    half_lane = toolhead_w / 2
    cycle = 0
    pushed = []
    while remaining:
        cycle += 1
        obj = _select_next_object(remaining, rear_limit)
        oidx = objs.index(obj)
        remaining.remove(obj)
        tag = f" - object {cycle}/{len(objs)}" if multi else ""

        # lane: remaining objects whose X band overlaps the toolhead lane
        # (same rule as the push-lane warning in gcode_bbox)
        lane = [
            i for i, ob in enumerate(objs)
            if i != oidx and i not in gone_set
            and ob["x_max"] > obj["cx"] - half_lane
            and ob["x_min"] < obj["cx"] + half_lane
        ]
        lane.sort(key=lambda i: -objs[i]["y_max"])
        if oidx not in gone_set:
            lane.insert(0, oidx)

        add_frame(f"xcycle{cycle}", f"Toolhead moves to object center{tag}",
                  obj["cx"], rear_limit, push_z, obj_index=oidx, pushed_list=pushed)
        add_frame(f"cool{cycle}", f"Cooling to {cool_temp_bed}°C{tag}",
                  obj["cx"], rear_limit, push_z, obj_index=oidx, pushed_list=pushed, wait=True)
        add_frame(f"wait{cycle}", f"Safety wait {safety_wait_s}s{tag}",
                  obj["cx"], rear_limit, push_z, obj_index=oidx, pushed_list=pushed, wait=True)

        pushed.append(oidx)
        contact_y = obj["y_max"]
        span = contact_y - push_end_y
        if span > 0:
            for step, frac in enumerate((1 / 3, 2 / 3), start=1):
                ty = contact_y - span * frac
                add_frame(f"push{cycle}_{step}", f"Push - object sliding off the plate{tag}",
                          obj["cx"], ty, push_z, dys=_lane_displacements(objs, lane, ty),
                          obj_index=oidx, pushed_list=pushed)
        final_dys = _lane_displacements(objs, lane, push_end_y)
        # objects shoved fully past the front edge fall off with this push
        fell = {i for i in lane
                if i != oidx and objs[i]["y_max"] + final_dys.get(i, 0.0) <= 0}
        add_frame(f"push{cycle}", f"Push - toolhead pushes the object off the plate{tag}",
                  obj["cx"], push_end_y, push_z, dys=final_dys,
                  obj_index=oidx, pushed_list=pushed, gone_now=gone_set | fell)
        gone_set.update(lane)

        if remaining:
            add_frame(f"lift{cycle}", f"Lift to safe height{tag}",
                      obj["cx"], push_end_y, safe_z, obj_index=oidx, pushed_list=pushed)
            add_frame(f"repark{cycle}", f"Bed fully forward - re-park{tag}",
                      obj["cx"], rear_limit, safe_z, obj_index=oidx, pushed_list=pushed)
            add_frame(f"descend{cycle}", f"Descend to push height{tag}",
                      obj["cx"], rear_limit, push_z, obj_index=oidx, pushed_list=pushed)

    add_frame("done_all", "Plate empty - ready for next print",
              objs[pushed[-1]]["cx"], rear_limit, safe_z, pushed_list=pushed)

    return {
        "printable_area": {"width": printable_x, "depth": machine.get("printable", {}).get("y", 256.0)},
        "safe_z": safe_z,
        "push_end_y": push_end_y,
        "plate_center_x": plate_center_x,
        "objects": [{"id": i, "cx": ob["cx"], "rear_y": ob["y_max"],
                      "y_min": ob["y_min"], "y_max": ob["y_max"]}
                     for i, ob in enumerate(objs)],
        "toolhead_dims": {"width": toolhead_w, "depth": toolhead_d,
                          "body_height": body_height, "clearance_radius": clearance_radius,
                          "structure": machine.get("structure", "bedslinger")},
        "printer": {"model_name": printer.get("name", ""),
                     "rear_limit": rear_limit,
                     "rear_limit_source": kin.get("rear_command_limit_source"),
                     "suitability": printer.get("suitability", "needs-test"),
                     "structure": machine.get("structure", "bedslinger")},
        "frames": frames,
    }


def build_multi_plate_sequence(
    plate_gcodes: dict,
    plate_prints: dict,
    t_lift: float = 5.0,
    push_z: float = 0.4,
    cool_temp_bed: int = 45,
    safety_wait_s: int = 60,
    eject_enabled: bool = True,
    min_height_mm: float = 7.0,
    printer: dict | None = None,
    plate_objects: dict | None = None,
    plate_max_z: dict | None = None,
) -> tuple:
    """Merge several plate gcodes into ONE sequential print job.

    Job order: plates ascending, each plate runs its own number of prints
    (plate_prints = TOTAL prints per plate, 0/None = skip the plate). Every
    job is followed by that plate's ejection push (including the very last
    job). The first-print purge/calibration strip runs exactly ONCE for the
    whole sequence; every later job (including the first print of the next
    plate) uses its plate's purge-free start. Re-heat targets the NEXT job's
    plate temps. The native shutdown runs only after the very last job.

    plate_gcodes:  {plate_index: gcode_text}
    plate_prints:  {plate_index: total_prints}
    plate_objects: {plate_index: parse_object_bboxes() result}
    plate_max_z:   {plate_index: max_z_height} (parsed from gcode when absent)
    Returns (new_gcode, notes).
    """
    notes = []
    if printer is None:
        from .printer_db import get_printer
        printer = get_printer("A1")

    jobs = []
    for idx in sorted(plate_gcodes):
        n = int((plate_prints or {}).get(idx, 0) or 0)
        if n > 0:
            jobs.append((idx, n))
    if not jobs:
        return "", ["No plates selected - nothing to build"]

    pinfo: dict = {}
    purge_free_used = False
    for idx, gcode in plate_gcodes.items():
        lines = gcode.splitlines(keepends=True)
        exec_start, exec_end = find_block_indices(lines)
        has_markers = exec_start != -1 and exec_end != -1
        if has_markers:
            header = lines[:exec_start]
            exec_block = lines[exec_start : exec_end + 1]
            body = _strip_native_shutdown(exec_block)
            shutdown = _native_shutdown(exec_block)
            body_first = body
            body_rest = body
            _pre, post = split_start_block(exec_block)
            if post and eject_enabled:
                purge_free = build_purge_free_start(exec_block)
                if purge_free is not None:
                    body_rest = _strip_native_shutdown(purge_free + post)
                    purge_free_used = True
                else:
                    notes.append(
                        f"Plate {idx}: no purge-section banners found - full "
                        "start gcode replayed for its prints"
                    )
        else:
            notes.append(f"Plate {idx}: no EXECUTABLE_BLOCK markers found - using fallback append mode")
            header = []
            body = _strip_native_shutdown(lines)
            shutdown = _native_shutdown(lines)
            body_first = body_rest = body

        objs = (plate_objects or {}).get(idx)
        mz = (plate_max_z or {}).get(idx, 0.0) or 0.0
        if not mz:
            mz = parse_max_z_height(gcode)
        if not mz:
            mz = 20.0
            notes.append(f"Plate {idx}: no max_z_height found - using 20mm fallback")
        if eject_enabled and not objs:
            notes.append(f"Plate {idx}: no object bbox available - blind push at plate center used")
        ejection = (
            build_ejection_snippet(
                mz,
                t_lift=t_lift,
                push_z=push_z,
                cool_temp_bed=cool_temp_bed,
                safety_wait_s=safety_wait_s,
                printer=printer,
                objects=objs,
            )
            if eject_enabled
            else ""
        )
        bed_temp, nozzle_temp = parse_print_temps(gcode)
        reheat = (
            f"; re-heat for next print\n"
            f"M140 S{bed_temp} ; heat bed\n"
            f"M190 S{bed_temp} ; wait bed\n"
            f"M104 S{nozzle_temp} ; heat nozzle\n"
            f"M109 S{nozzle_temp} ; wait nozzle\n"
        )
        pinfo[idx] = {
            "header": header,
            "body_first": body_first,
            "body_rest": body_rest,
            "shutdown": shutdown,
            "has_markers": has_markers,
            "ejection": ejection,
            "reheat": reheat,
        }

    if purge_free_used:
        notes.append(
            "Every print after the very first skips the first-print "
            "purge/calibration strip (would print onto the ejected part)"
        )

    total_jobs = sum(n for _, n in jobs)
    parts = ["".join(pinfo[jobs[0][0]]["header"])]
    job_no = 0
    for idx, n in jobs:
        for c in range(n):
            job_no += 1
            first_job = job_no == 1
            last_job = job_no == total_jobs
            info = pinfo[idx]
            if eject_enabled and not first_job:
                parts.append(
                    f"\n; ===== plateloop print {job_no} of {total_jobs} "
                    f"(plate {idx}, print {c + 1} of {n}) =====\n"
                )
                parts.append(info["reheat"])
            parts.append("".join(info["body_first"] if first_job else info["body_rest"]))
            if eject_enabled:
                parts.append(info["ejection"])
                if not last_job:
                    if info["has_markers"]:
                        parts.append(EXEC_END + "\n")  # balance START/END per job
                else:
                    parts.append("".join(info["shutdown"]))  # includes END
    parts.append("\n")

    if eject_enabled and min_height_mm and min_height_mm > 0:
        notes.append(
            f"Ejection push assumes objects >= {min_height_mm}mm tall - "
            "shorter parts may jam the toolhead"
        )
    return "".join(parts), notes
