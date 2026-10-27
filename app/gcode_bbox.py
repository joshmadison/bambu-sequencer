"""Compute true 2D bbox from real extrusion moves in the sliced gcode.

Ground truth (verified 2026-09-28 against user's Lenker example):
- plate_1.json bbox_objects/bbox_all = WRONG for whole-object extent
  (only covers part of the model: 38.98 x 45.44 vs true 118.59 x 114.50)
- Real extrusion moves in the gcode are the reliable source
- STL truth: 119.085 x 114.92 x 30 (Bambu Lab page) - matches gcode extent
  (with extrusion width + skirt margins) perfectly.

Extrusion move detection:
- G1 lines with X AND Y AND an E value that is a POSITIVE length
  ('E.27613', 'E1.0') - not pure E-moves ('G1 E10 F1200') and not
  retracts ('E-...').
- Coords clamped to a plausible bed range; bed_limit comes from the
  printer_db record so larger printers (H2D 350x320) work too.
"""
import re

MOVE_RE = re.compile(r"^G1\s+.*\bX(-?[\d.]+)\s+.*\bY(-?[\d.]+)", re.IGNORECASE)
E_RE = re.compile(r"\bE(\d*\.?\d+)", re.IGNORECASE)
NEG_E_RE = re.compile(r"\bE-")


def bbox_from_gcode(gcode: str, bed_limit: float = 262.0) -> dict | None:
    """2D bbox (x_min..x_max, y_min..y_max) of real extrusion moves."""
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    count = 0
    limit_plus = bed_limit + 6.0
    for line in gcode.splitlines():
        if not line.startswith("G1"):
            continue
        if NEG_E_RE.search(line):
            continue
        m = E_RE.search(line)
        if not m:
            continue
        mm = MOVE_RE.match(line)
        if not mm:
            continue
        x = float(mm.group(1))
        y = float(mm.group(2))
        if -5.0 <= x <= limit_plus and -5.0 <= y <= limit_plus:
            minx = min(minx, x)
            maxx = max(maxx, x)
            miny = min(miny, y)
            maxy = max(maxy, y)
            count += 1
    if count == 0:
        return None
    return {
        "x_min": round(minx, 2),
        "x_max": round(maxx, 2),
        "y_min": round(miny, 2),
        "y_max": round(maxy, 2),
        "moves": count,
    }


VAL_RE = re.compile(r"\b([XY])(-?[\d.]+)", re.IGNORECASE)

OBJ_ID_RE = re.compile(r";\s*OBJECT_ID:\s*(\d+)")
# Z of any motion command (G0/G1 travel, G2/G3 spiral z-hop): the current
# layer height is the last commanded Z before an extrusion move.
Z_RE = re.compile(r"^G[0-3]\b.*?\bZ(-?[\d.]+)", re.IGNORECASE)


def parse_object_bboxes(gcode: str, bed_limit: float = 262.0, id_names: dict | None = None) -> list:
    """Per-object 2D bboxes from '; OBJECT_ID: n' groups + extrusion moves.

    Bambu emits one marker per object per layer; moves between two markers
    belong to that object. Skirts/brims (outside every object, front-of-plate
    poop at Y<0) would pollute single-object files, so they are excluded by
    the OBJECT_ID grouping itself - only real object moves carry a marker.
    id_names: optional {str(id): name} map from slice_info.config's
    <object identify_id=...> elements - fills the 'name' field when provided.
    Returns one bbox dict per distinct id, sorted by id, with 'moves' counts
    and 'z_max' - the highest Z at which that object was extruded, i.e. its
    printed height. The plate JSON only has 2D boxes, so this is the only
    per-object height source; the min height check depends on it.
    """
    limit_plus = bed_limit + 6.0
    stats: dict = {}
    current_id = None
    cur_z = 0.0
    for line in gcode.splitlines():
        s = line.strip()
        if s[:1] == "G":
            mz = Z_RE.match(s)
            if mz:
                try:
                    cur_z = float(mz.group(1))
                except ValueError:
                    pass
        if s.startswith(";"):
            m_id = OBJ_ID_RE.match(s)
            if m_id:
                current_id = m_id.group(1)
            continue
        if current_id is None:
            continue
        if NEG_E_RE.search(line):
            continue
        m = E_RE.search(line)
        if not m:
            continue
        mm = MOVE_RE.match(line)
        if not mm:
            continue
        x = float(mm.group(1))
        y = float(mm.group(2))
        if y < 0.0 or y > limit_plus or x < -5.0 or x > limit_plus:
            continue
        st = stats.setdefault(current_id, {"x_min": x, "x_max": x, "y_min": y, "y_max": y,
                                           "moves": 0, "z_max": 0.0})
        st["z_max"] = max(st["z_max"], cur_z)
        st["x_min"] = min(st["x_min"], x)
        st["x_max"] = max(st["x_max"], x)
        st["y_min"] = min(st["y_min"], y)
        st["y_max"] = max(st["y_max"], y)
        st["moves"] += 1
    out = []
    for oid in sorted(stats, key=lambda k: int(k)):
        st = stats[oid]
        entry = {
            "id": int(oid),
            "x_min": round(st["x_min"], 2),
            "x_max": round(st["x_max"], 2),
            "y_min": round(st["y_min"], 2),
            "y_max": round(st["y_max"], 2),
            "moves": st["moves"],
        }
        if st["z_max"] > 0:
            entry["z_max"] = round(st["z_max"], 2)
        if id_names and str(oid) in id_names:
            entry["name"] = id_names[str(oid)]
        out.append(entry)
    return out


def find_push_lane_conflicts(objects: list, toolhead_half_width_mm: float = 18.0) -> list:
    """Pairs of objects that share one push lane.

    The ejection pushes the rear-most object first from its y_max down to
    Y=0. The toolhead is centered on that object, so its body sweeps the
    X lane [center - half_width, center + half_width] over the full plate
    depth: every remaining object whose X band touches that lane is hit -
    the parts can cascade off together or jam. Uses the same dynamic order
    as the generator (rear-most first, tie: closest to X=0).
    Returns [(rear_obj, at_risk_obj), ...].
    """
    conflicts = []
    remaining = list(objects)
    while remaining:
        rear = min(remaining, key=lambda o: (-o["y_max"], (o["x_min"] + o["x_max"]) / 2))
        remaining.remove(rear)
        center = (rear["x_min"] + rear["x_max"]) / 2
        lane_min = center - toolhead_half_width_mm
        lane_max = center + toolhead_half_width_mm
        for other in remaining:
            if other["x_max"] > lane_min and other["x_min"] < lane_max:
                conflicts.append((rear, other))
    return conflicts


def print_end_position(gcode: str, bed_limit: float = 262.0) -> dict | None:
    """Last commanded X/Y before the native shutdown begins.

    Ground truth: every copy in the sequence keeps its native shutdown
    stripped, so at ejection time the toolhead is exactly where the print's
    last motion left it - NOT at the plate center. Used by the animation
    so the 'print finished' frame shows the real start.
    """
    limit_plus = bed_limit + 6.0
    last_x = last_y = None
    for line in gcode.splitlines():
        s = line.strip()
        if s.startswith(";"):
            continue
        if s.startswith("M140 S0"):
            break
        if not (s.startswith("G0 ") or s.startswith("G1 ") or s in ("G0", "G1")):
            continue
        for m in VAL_RE.finditer(s):
            v = float(m.group(2))
            # park positions sit outside the bed (A1 wipe X=-13, poop Y=-0.5):
            # only reject clearly insane values
            if v < -60.0 or v > limit_plus + 60.0:
                continue
            if m.group(1).upper() == "X":
                last_x = v
            else:
                last_y = v
    if last_x is None and last_y is None:
        return None
    return {"x": round(last_x, 2) if last_x is not None else None,
            "y": round(last_y, 2) if last_y is not None else None}