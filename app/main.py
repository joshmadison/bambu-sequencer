"""FastAPI app: upload sliced 3MF, configure sequence, download looped .gcode.3mf."""
import base64
import io
import json
import logging
import os
import urllib.parse
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response

from .gcode import build_multi_plate_sequence, ejection_keyframes, parse_max_z_height
from .gcode_bbox import (
    bbox_from_gcode,
    find_push_lane_conflicts,
    parse_object_bboxes,
    print_end_position,
)
from .printer_db import (
    PRINTERS,
    PROVENANCE_LEVELS,
    STRUCTURES,
    SUITABILITY_LEVELS,
    get_printer,
    is_known_printer,
    resolve_display_name,
)
from .threemf import (
    extract_plate_gcodes,
    extract_thumbnail,
    parse_3mf,
    repack_single_plate_3mf,
)

app = FastAPI(title="bambu-sequencer", version="0.4.0")
log = logging.getLogger("bambu-sequencer")

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "300"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

_STATIC = Path(__file__).parent / "static"


# ============ upload handling ============

async def read_upload(file: UploadFile, require_3mf: bool = True) -> bytes:
    """Validate size + extension BEFORE reading the full body; capped chunked
    read so a missing/lying Content-Length cannot buffer-overflow RAM."""
    if file.size and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File larger than {MAX_UPLOAD_MB} MB")
    if require_3mf and file.filename and not file.filename.lower().endswith(".3mf"):
        raise HTTPException(400, "Please upload a .3mf file")
    chunks = []
    read_total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        read_total += len(chunk)
        if read_total > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"File larger than {MAX_UPLOAD_MB} MB")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(400, "Empty file")
    return data


async def parse_upload(file: UploadFile) -> tuple:
    """Read + parse a 3MF upload. Returns (info, data)."""
    data = await read_upload(file)
    try:
        info = await run_in_threadpool(parse_3mf, io.BytesIO(data), file.filename or "upload.3mf")
    except Exception as exc:
        raise HTTPException(400, f"Could not parse 3MF: {exc}") from exc
    return info, data


async def load_gcode_3mf(file: UploadFile) -> tuple:
    """Read + parse a sliced gcode 3MF. Returns (info, data, {plate: gcode}).

    400 unless it is a sliced gcode 3MF; 400 when no plate gcode can be read.
    """
    info, data = await parse_upload(file)
    if not info.is_gcode_3mf:
        raise HTTPException(
            400,
            "This looks like a project 3MF without sliced gcode. Please slice in Bambu Studio first "
            "(File > Export > Plate as .gcode.3mf).",
        )
    try:
        gcodes = await run_in_threadpool(extract_plate_gcodes, io.BytesIO(data))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return info, data, gcodes


# ============ routes ============

@app.get("/", response_class=HTMLResponse)
async def index():
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    return html.replace("</head>",
        f"<script>window.APP_MAX_UPLOAD_MB = {MAX_UPLOAD_MB};</script></head>")


@app.get("/api/printers")
async def api_printers():
    """Printer compatibility table for the Printers page (flat API view)."""
    order = {"verified": 0, "expected": 1, "needs-test": 2}
    rows = []
    for name, p in PRINTERS.items():
        machine = p.get("machine", {})
        kin = p.get("kinematics", {})
        th = p.get("toolhead", {})
        printable = machine.get("printable", {})
        struct = STRUCTURES.get(machine.get("structure", ""), {})
        suit = SUITABILITY_LEVELS.get(p.get("suitability", ""), {})
        prov = PROVENANCE_LEVELS.get(kin.get("rear_command_limit_source", ""), {})
        rows.append({
            "name": p.get("name", name),
            "printable_x": printable.get("x"),
            "printable_y": printable.get("y"),
            "printable_z": printable.get("z"),
            "rear_command_limit": kin.get("rear_command_limit"),
            "rear_command_limit_source": kin.get("rear_command_limit_source"),
            "rear_command_limit_label": prov.get("label", ""),
            "toolhead_clearance_radius": th.get("clearance_radius"),
            "toolhead_body_height": th.get("body_height"),
            "structure": machine.get("structure", ""),
            "structure_label": struct.get("label", machine.get("structure", "")),
            "mechanism_short": struct.get("mechanism_short", ""),
            "suitability": p.get("suitability", "needs-test"),
            "suitability_label": suit.get("label", p.get("suitability", "")),
            "note": p.get("suitability_note", ""),
        })
    rows.sort(key=lambda r: (order.get(r["suitability"], 9), r["name"]))
    return {"printers": rows}


@app.post("/api/inspect")
async def api_inspect(file: UploadFile = File(...)):
    info, _ = await parse_upload(file)
    return {
        "filename": info.filename,
        "is_gcode_3mf": info.is_gcode_3mf,
        "max_z_height": info.max_z_height,
        "plates": [
            {
                "index": p.index,
                "min_object_height": p.min_object_height,
                "max_z_height": p.max_z_height,
                "objects": [{"name": o.name, "height_mm": o.height_mm} for o in p.objects],
            }
            for p in info.plates
        ],
        "warnings": info.warnings,
    }


def _fmt_duration(sec: float) -> str:
    if sec <= 0:
        return "—"
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    return f"{h}h {m}m" if h else f"{m}m {s}s" if m else f"{s}s"


@app.post("/api/stats")
async def api_stats(file: UploadFile = File(...)):
    """Per-plate stats + thumbnails: dimensions, times, filament usage."""
    info, data = await parse_upload(file)
    warnings = list(info.warnings)
    if info.printer_model and not is_known_printer(info.printer_model):
        warnings.append(
            f"Printer '{info.printer_model}' is not in the printer database - "
            "A1 geometry assumed, push depths may differ"
        )

    true_printer = get_printer(info.printer_model or "A1")
    bed_limit = true_printer["machine"]["printable"]["y"] + 8.0
    try:
        gcodes = await run_in_threadpool(extract_plate_gcodes, io.BytesIO(data))
    except ValueError as exc:
        gcodes = {}
        warnings.append(str(exc))

    def _thumb_b64(plate_idx: int) -> str:
        try:
            thumb = extract_thumbnail(io.BytesIO(data), "large", plate_idx)
            if thumb:
                return "data:image/png;base64," + base64.b64encode(thumb).decode()
        except Exception:
            pass  # thumbnail is optional
        return ""

    plates_out = []
    all_objects = []
    for p in info.plates:
        gcode = gcodes.get(p.index, "")
        true_bbox = None
        true_objects = []
        if gcode:
            try:
                true_bbox = await run_in_threadpool(bbox_from_gcode, gcode, bed_limit)
                true_objects = await run_in_threadpool(
                    parse_object_bboxes, gcode, bed_limit, info.object_names
                )
            except Exception:
                true_bbox, true_objects = None, []

        plate_objects = [
            {
                "plate": p.index,
                "name": o.name,
                "height_mm": o.height_mm,
                "width_mm": o.width_mm,
                "depth_mm": o.depth_mm,
            }
            for o in p.objects
        ]

        if true_objects:
            # One row per OBJECT_ID group - the reliable per-object source.
            # Names come from slice_info <object identify_id=...> when present,
            # else from the plate-JSON name (single-object files), else "Object <id>".
            fallback_name = plate_objects[0]["name"] if plate_objects else None
            total_moves = sum(t["moves"] for t in true_objects)
            n_multi = len(true_objects) > 1
            all_layers = max(1, p.layer_count or 1)
            new_objects = []
            for gi, tobj in enumerate(true_objects):
                share = (tobj["moves"] / total_moves) if total_moves else 0.0
                name = tobj.get("name") or (fallback_name or f"Object {tobj['id']}")
                entry = {
                    "multi": n_multi,
                    "plate": p.index,
                    "obj_id": tobj["id"],
                    "name": name,
                    "height_mm": p.min_object_height or p.max_z_height,
                    "width_mm": round(tobj["x_max"] - tobj["x_min"], 2),
                    "depth_mm": round(tobj["y_max"] - tobj["y_min"], 2),
                    "x_min": tobj["x_min"],
                    "x_max": tobj["x_max"],
                    "y_min": tobj["y_min"],
                    "y_max": tobj["y_max"],
                    "index": gi + 1,
                    "share_pct": round(share * 100, 1),
                    "layers_est": max(1, round(all_layers * share)) if n_multi else all_layers,
                    "filament_g_est": round((p.filament_weight_g or 0) * share, 2) if n_multi else round(p.filament_weight_g or 0, 2),
                    "print_time_sec_est": round((p.print_time_sec or 0) * share) if n_multi else round(p.print_time_sec or 0),
                }
                new_objects.append(entry)
            plate_objects = new_objects
            half_w = true_printer.get("toolhead", {}).get("footprint", {}).get("width", 36.0) / 2.0
            for rear, at_risk in find_push_lane_conflicts(true_objects, half_w):
                rn = rear.get("name") or f"Object {rear['id']}"
                an = at_risk.get("name") or f"Object {at_risk['id']}"
                warnings.append(
                    f"Plate {p.index}: '{rn}' and '{an}' share the same push lane "
                    "(too close in X for the toolhead) - the rear object can push the "
                    "front object off the plate too (cascade) or jam; watch the first cycle"
                )
        elif true_bbox and plate_objects:
            plate_objects[0]["width_mm"] = round(true_bbox["x_max"] - true_bbox["x_min"], 2)
            plate_objects[0]["depth_mm"] = round(true_bbox["y_max"] - true_bbox["y_min"], 2)
            plate_objects[0]["x_min"] = true_bbox["x_min"]
            plate_objects[0]["x_max"] = true_bbox["x_max"]
            plate_objects[0]["y_min"] = true_bbox["y_min"]
            plate_objects[0]["y_max"] = true_bbox["y_max"]
        elif p.objects:
            warnings.append(f"Plate {p.index}: could not derive true size from gcode - using slicer metadata")

        all_objects.extend(plate_objects)
        plates_out.append({
            "index": p.index,
            "print_time_sec": p.print_time_sec,
            "print_time_human": _fmt_duration(p.print_time_sec),
            "total_time_sec": p.total_time_sec,
            "layer_count": p.layer_count,
            "filament_weight_g": p.filament_weight_g,
            "filament_length_m": round(p.filament_length_mm / 1000, 2),
            "first_layer_bed_temp": p.first_layer_bed_temp,
            "max_z_height": p.max_z_height,
            "filaments": p.filaments,
            "thumbnail": _thumb_b64(p.index),
            "objects": plate_objects,
        })

    return {
        "filename": info.filename,
        "is_gcode_3mf": info.is_gcode_3mf,
        "slicer_version": info.slicer_version,
        "printer_model": resolve_display_name(info.printer_model, info.printer_model_full),
        "plate_count": len(plates_out),
        "print_time_sec": info.print_time_sec,
        "print_time_human": _fmt_duration(info.print_time_sec),
        "total_time_sec": info.total_time_sec,
        "total_time_human": _fmt_duration(info.total_time_sec),
        "layer_count": info.layer_count,
        "filament_weight_g": info.filament_weight_g,
        "filament_length_m": round(info.filament_length_mm / 1000, 2),
        "first_layer_bed_temp": info.first_layer_bed_temp,
        "max_z_height": info.max_z_height,
        "filaments": info.filaments,
        "objects": all_objects,
        "thumbnail": plates_out[0]["thumbnail"] if plates_out else "",
        "plates": plates_out,
        "warnings": warnings,
    }


@app.post("/api/thumbnail")
async def api_thumbnail(file: UploadFile = File(...)):
    """Raw plate render PNG from the 3MF."""
    data = await read_upload(file)
    try:
        thumb = await run_in_threadpool(extract_thumbnail, io.BytesIO(data), "large")
    except Exception:
        raise HTTPException(400, "Could not read this 3MF (corrupt or not a ZIP?)") from None
    if not thumb:
        raise HTTPException(404, "No plate thumbnail in this 3MF")
    return Response(content=thumb, media_type="image/png")


@app.post("/api/animation")
async def api_animation(
    file: UploadFile = File(...),
    plate: int = Form(1, ge=1, le=20),
    t_lift: float = Form(5.0, ge=1.0, le=50.0),
    push_z: float = Form(0.4, ge=0.1, le=2.0),
    cool_temp_bed: int = Form(45, ge=20, le=100),
    safety_wait_s: int = Form(60, ge=0, le=600),
):
    """Animation keyframes for ONE plate, mirroring the generated ejection gcode."""
    info, _data, gcodes = await load_gcode_3mf(file)
    if plate not in gcodes:
        raise HTTPException(404, f"Plate {plate} not found in this 3MF (plates: {sorted(gcodes)})")
    gcode = gcodes[plate]

    true_printer = get_printer(info.printer_model or "A1")
    bed_limit = true_printer["machine"]["printable"]["y"] + 8.0
    bbox = await run_in_threadpool(bbox_from_gcode, gcode, bed_limit)
    if not bbox:
        raise HTTPException(400, "No extrusion moves found - cannot derive object position")
    print_end_pos = await run_in_threadpool(print_end_position, gcode, bed_limit)
    objects = await run_in_threadpool(parse_object_bboxes, gcode, bed_limit, info.object_names)

    plate_info = next((p for p in info.plates if p.index == plate), None)
    max_z_height = (plate_info.max_z_height if plate_info else 0.0) or parse_max_z_height(gcode) or 20.0

    kf = ejection_keyframes(
        max_z_height=max_z_height,
        t_lift=t_lift,
        push_z=push_z,
        cool_temp_bed=cool_temp_bed,
        safety_wait_s=safety_wait_s,
        printer=true_printer,
        objects=objects,
        print_end_pos=print_end_pos,
    )
    kf["object_bbox"] = bbox
    kf["objects_bboxes"] = objects
    kf["max_z_height"] = max_z_height
    kf["eject_enabled"] = True
    kf["plate"] = plate
    kf["plate_count"] = len(gcodes)
    kf["printer_detected"] = resolve_display_name(info.printer_model, info.printer_model_full)
    return kf


@app.post("/api/sequence")
async def api_sequence(
    file: UploadFile = File(...),
    plate_prints: str = Form(...),
    t_lift: float = Form(5.0, ge=1.0, le=50.0),
    push_z: float = Form(0.4, ge=0.1, le=2.0),
    cool_temp_bed: int = Form(45, ge=20, le=100),
    safety_wait_s: int = Form(60, ge=0, le=600),
    min_height_mm: float = Form(7.0, ge=0, le=100),
):
    """Build ONE gcode that runs all selected plates sequentially.

    plate_prints: JSON {"1": 10, "2": 5} = TOTAL prints per plate (0 = skip).
    """
    info, data, gcodes = await load_gcode_3mf(file)

    # ----- selection: total prints per plate -----
    if not plate_prints.strip():
        raise HTTPException(422, "plate_prints is required: JSON like {\"1\": 10}")
    try:
        raw = json.loads(plate_prints)
    except json.JSONDecodeError:
        raise HTTPException(422, "plate_prints must be a JSON object like {\"1\": 10}") from None
    if not isinstance(raw, dict):
        raise HTTPException(422, "plate_prints must be a JSON object like {\"1\": 10}")
    selected: dict = {}
    for key, val in raw.items():
        try:
            idx = int(key)
        except (TypeError, ValueError):
            raise HTTPException(422, f"plate_prints: invalid plate index {key!r}") from None
        if isinstance(val, bool) or not isinstance(val, (int, float)) or float(val) != int(val):
            raise HTTPException(
                422, f"plate_prints: prints for plate {idx} must be an integer (got {val!r})"
            )
        n = int(val)
        if idx not in gcodes:
            raise HTTPException(422, f"Plate {idx} is not present in this 3MF (plates: {sorted(gcodes)})")
        if n < 0 or n > 99:
            raise HTTPException(422, f"plate_prints: {n} prints for plate {idx} out of range 0..99")
        selected[idx] = n

    active = {idx: n for idx, n in selected.items() if n > 0}
    if not active:
        raise HTTPException(422, "No plates selected - set at least one plate to 1 or more prints")

    # ----- height check per selected plate -----
    if min_height_mm > 0:
        for idx in sorted(active):
            p = next((p for p in info.plates if p.index == idx), None)
            if p and p.objects and p.min_object_height < min_height_mm:
                raise HTTPException(
                    422,
                    f"Plate {idx}: object height {p.min_object_height}mm is below required "
                    f"{min_height_mm}mm (risk of toolhead jam). Slice a taller object or lower the check.",
                )

    # ----- per-plate objects / max_z -----
    true_printer = get_printer(info.printer_model or "A1")
    bed_limit = true_printer["machine"]["printable"]["y"] + 8.0
    plate_objects: dict = {}
    plate_max_z: dict = {}
    for idx in sorted(active):
        gcode = gcodes[idx]
        p = next((p for p in info.plates if p.index == idx), None)
        plate_objects[idx] = await run_in_threadpool(parse_object_bboxes, gcode, bed_limit, info.object_names)
        mz = (p.max_z_height if p else 0.0) or parse_max_z_height(gcode)
        plate_max_z[idx] = mz if mz > 0 else 20.0

    new_gcode, _notes = await run_in_threadpool(
        build_multi_plate_sequence,
        gcodes,
        active,
        t_lift=t_lift,
        push_z=push_z,
        cool_temp_bed=cool_temp_bed,
        safety_wait_s=safety_wait_s,
        eject_enabled=True,
        min_height_mm=min_height_mm,
        printer=true_printer,
        plate_objects=plate_objects,
        plate_max_z=plate_max_z,
    )

    # slice_info totals of the remaining plate: printed weight/time of ALL jobs
    total_prints = sum(active.values())
    total_weight = 0.0
    total_prediction = 0.0
    per_filament: dict = {}
    for idx, n in active.items():
        p = next((p for p in info.plates if p.index == idx), None)
        if not p:
            continue
        total_prediction += (p.print_time_sec or 0) * n
        total_weight += (p.filament_weight_g or sum(f.get("weight_g", 0) for f in p.filaments)) * n
        for f in p.filaments:
            key = (f.get("type", ""), f.get("color", ""))
            entry = per_filament.setdefault(key, {"used_g": 0.0, "used_m": 0.0})
            entry["used_g"] += (f.get("weight_g", 0) or 0) * n
            entry["used_m"] += (f.get("used_m", 0) or 0) * n

    keep_plate = min(active)
    out_buf = await run_in_threadpool(
        repack_single_plate_3mf,
        io.BytesIO(data),
        new_gcode,
        keep_plate,
        {"prediction_sec": total_prediction, "weight_g": total_weight, "per_filament": per_filament},
    )
    payload = out_buf.getvalue()

    stem = Path(file.filename or "model.gcode.3mf").stem
    out_name = f"{stem}_x{total_prints}.gcode.3mf"
    quoted = urllib.parse.quote(out_name)
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename=\"sequence.gcode.3mf\"; filename*=UTF-8''{quoted}",
            "X-Sequence-Info": f"prints={total_prints}; plates={len(active)}",
        },
    )


# ============ error handling ============

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=500, content={"detail": "Internal server error - check the app logs"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
