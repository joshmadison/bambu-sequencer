"""3MF parsing: read metadata, compute object heights, extract gcode.

XML from uploaded archives is parsed with defusedxml (billion-laughs /
entity-expansion hardening); serialization uses plain ElementTree.
"""
import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field

from defusedxml.ElementTree import fromstring as _safe_fromstring

BAMBU_NAMESPACE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"


@dataclass
class ObjectInfo:
    name: str
    height_mm: float
    width_mm: float
    depth_mm: float
    # placement on the bed (mm, from plate JSON bbox)
    x_min: float = 0.0
    x_max: float = 0.0
    y_min: float = 0.0
    y_max: float = 0.0

    @property
    def center_x(self) -> float:
        return round((self.x_min + self.x_max) / 2, 2)

    @property
    def front_y(self) -> float:
        """Y of the front edge (smallest Y = front on Bambu beds)."""
        return round(self.y_min, 2)


@dataclass
class PlateInfo:
    index: int
    objects: list = field(default_factory=list)
    max_z_height: float = 0.0
    gcode_name: str = ""
    print_time_sec: float = 0.0
    total_time_sec: float = 0.0
    layer_count: int = 0
    filament_weight_g: float = 0.0
    filament_length_mm: float = 0.0
    filaments: list = field(default_factory=list)  # [{type,color,weight_g,used_m}]
    first_layer_bed_temp: int = 0

    @property
    def min_object_height(self) -> float:
        if not self.objects:
            return 0.0
        return min(o.height_mm for o in self.objects)


@dataclass
class ThreeMFInfo:
    filename: str
    is_gcode_3mf: bool
    plates: list = field(default_factory=list)
    gcode_size: int = 0
    warnings: list = field(default_factory=list)
    # stats (populated for gcode.3mf)
    print_time_sec: float = 0.0
    total_time_sec: float = 0.0
    layer_count: int = 0
    filament_weight_g: float = 0.0
    filament_length_mm: float = 0.0
    filaments: list = field(default_factory=list)  # [{type,color,weight_g}]
    slicer_version: str = ""
    printer_model: str = ""
    printer_model_full: str = ""
    first_layer_bed_temp: int = 0
    # slice_info <object identify_id="207" name="Lenker.stl"> - the reliable
    # per-id name map for OBJECT_ID groups in the gcode
    object_names: dict = field(default_factory=dict)  # {"207": "Lenker.stl"}

    @property
    def min_object_height(self) -> float:
        heights = [p.min_object_height for p in self.plates if p.objects]
        return min(heights) if heights else 0.0

    @property
    def max_z_height(self) -> float:
        heights = [p.max_z_height for p in self.plates if p.max_z_height > 0]
        return max(heights) if heights else 0.0

    @property
    def object_count(self) -> int:
        return sum(len(p.objects) for p in self.plates)


def _local(tag: str) -> str:
    """Local XML tag/attribute name without its namespace."""
    return tag.rsplit("}", 1)[-1]


def _iter_local(root: ET.Element, name: str):
    """Iterate all descendants whose local tag name matches (namespace-agnostic)."""
    return (el for el in root.iter() if _local(el.tag) == name)


def _attr_local(el: ET.Element, name: str) -> str | None:
    """First attribute whose local name matches (handles p:path etc.)."""
    for key, value in el.attrib.items():
        if _local(key) == name:
            return value
    return None


def _bbox_of_vertices(root: ET.Element) -> dict | None:
    """Axis-aligned bbox of all mesh vertices inside an element, or None."""
    min_x = min_y = min_z = float("inf")
    max_x = max_y = max_z = float("-inf")
    found = False
    for vertex in _iter_local(root, "vertex"):
        found = True
        x = float(vertex.get("x", "0"))
        y = float(vertex.get("y", "0"))
        z = float(vertex.get("z", "0"))
        min_x, min_y, min_z = min(min_x, x), min(min_y, y), min(min_z, z)
        max_x, max_y, max_z = max(max_x, x), max(max_y, y), max(max_z, z)
    if not found:
        return None
    return {
        "height": round(max_z - min_z, 2),
        "width": round(max_x - min_x, 2),
        "depth": round(max_y - min_y, 2),
    }


def _objects_of_3dmodel(model_bytes: bytes) -> tuple:
    """Map 3dmodel.model object ids to their geometry source.

    Returns (component_paths, inline_bboxes):
    - component_paths: {object_id: "/3D/Objects/x.model"} for objects that
      reference an external part file via <component p:path=...>.
    - inline_bboxes: {object_id: bbox} for objects carrying their own mesh.
    """
    root = _safe_fromstring(model_bytes)
    component_paths: dict = {}
    inline_bboxes: dict = {}
    for obj in _iter_local(root, "object"):
        oid = obj.get("id")
        if not oid:
            continue
        path = None
        for comp in _iter_local(obj, "component"):
            path = _attr_local(comp, "path")
            if path:
                break
        if path:
            component_paths[oid] = path
        else:
            bb = _bbox_of_vertices(obj)
            if bb:
                inline_bboxes[oid] = bb
    return component_paths, inline_bboxes


def _parse_bambu_3mf(zf: zipfile.ZipFile, info: ThreeMFInfo) -> None:
    """Parse a NON-sliced slicer project 3MF (no gcode inside).

    Mapping chain (real Bambu/Orca project layout):
      Metadata/model_settings.config: <plate> -> <model_instance> metadata
      'object_id' -> <object id> metadata 'name'.
      3D/3dmodel.model: <object id> -> <component p:path="/3D/Objects/x.model">.
      3D/Objects/x.model: mesh vertices -> bbox (height/width/depth).
    Instance transforms (rotation/scale) are not applied; local part
    dimensions are shown.
    """
    names = zf.namelist()

    # 1) geometry bbox per external part file
    geoms: dict = {}  # "3D/Objects/x.model" -> bbox dict
    for name in names:
        if not name.startswith("3D/Objects/") or not name.endswith(".model"):
            continue
        try:
            bb = _bbox_of_vertices(_safe_fromstring(zf.read(name)))
        except Exception:
            continue
        if bb:
            geoms[name] = bb

    # 2) object_id -> geometry (via 3dmodel components or inline mesh)
    obj_geom: dict = {}
    model3d = next((n for n in names if n.lower().endswith("3dmodel.model")), None)
    if model3d:
        try:
            component_paths, inline_bboxes = _objects_of_3dmodel(zf.read(model3d))
        except Exception:
            component_paths, inline_bboxes = {}, {}
        for oid, path in component_paths.items():
            key = path.lstrip("/")
            bb = geoms.get(key) or geoms.get("3D/Objects/" + key.split("/")[-1])
            if bb:
                obj_geom[oid] = bb
        obj_geom.update(inline_bboxes)

    # 3) project metadata: object names + object ids per plate
    metadata_model = next(
        (n for n in names if n.lower().endswith("metadata/model_settings.config")), None
    )
    obj_names: dict = {}
    plate_objects: dict = {}  # plate index (1-based) -> [object_id, ...]
    if metadata_model:
        try:
            root = _safe_fromstring(zf.read(metadata_model))
        except Exception:
            root = None
            info.warnings.append("Could not parse model_settings.config")
        if root is not None:
            for obj in _iter_local(root, "object"):
                oid = obj.get("id")
                if not oid:
                    continue
                oname = next(
                    (m.get("value") for m in _iter_local(obj, "metadata") if m.get("key") == "name"),
                    None,
                )
                if oname:
                    obj_names[oid] = oname
            for plate in _iter_local(root, "plate"):
                # real Bambu: metadata key "plater_id" (1-based);
                # synthetic/older files: attribute index (0-based)
                pidx = next(
                    (m.get("value") for m in _iter_local(plate, "metadata")
                     if m.get("key") in ("plater_id", "index") and (m.get("value") or "").isdigit()),
                    None,
                )
                if pidx is None:
                    attr_idx = plate.get("index")
                    pidx = str(int(attr_idx) + 1) if (attr_idx or "").isdigit() else None
                if pidx is None:
                    continue
                oids = []
                for mi in _iter_local(plate, "model_instance"):
                    oid = next(
                        (m.get("value") for m in _iter_local(mi, "metadata")
                         if m.get("key") == "object_id"),
                        None,
                    )
                    if oid is None:
                        ident = mi.get("id", "")
                        m = re.search(r"Object (\d+)", ident)
                        oid = m.group(1) if m else None
                    if oid:
                        oids.append(oid)
                if oids:
                    plate_objects[int(pidx)] = oids

    # 4) build ObjectInfo lists
    unmapped = 0
    if plate_objects:
        for idx, oids in plate_objects.items():
            objs = []
            for oid in oids:
                bb = obj_geom.get(oid)
                if bb is None and len(obj_geom) == 1:
                    bb = next(iter(obj_geom.values()))
                name = obj_names.get(oid)
                if bb is None:
                    unmapped += 1
                    name = name or f"Object {oid}"
                objs.append(ObjectInfo(
                    name=name or f"Object {oid}",
                    height_mm=bb["height"] if bb else 0.0,
                    width_mm=bb["width"] if bb else 0.0,
                    depth_mm=bb["depth"] if bb else 0.0,
                ))
            info.plates.append(PlateInfo(index=idx, objects=objs))
    elif geoms:
        # no plate mapping available: list the part files as one plate
        objs = [
            ObjectInfo(name=n.split("/")[-1], height_mm=g["height"],
                       width_mm=g["width"], depth_mm=g["depth"])
            for n, g in sorted(geoms.items())
        ]
        info.plates.append(PlateInfo(index=1, objects=objs))
    if unmapped:
        info.warnings.append(
            f"{unmapped} object(s) could not be matched to a 3D part file - size unknown"
        )


def _plate_for(info: ThreeMFInfo, idx: int) -> PlateInfo:
    """Get-or-create the PlateInfo for a plate index."""
    for p in info.plates:
        if p.index == idx:
            return p
    p = PlateInfo(index=idx)
    info.plates.append(p)
    return p


def _parse_plate_gcode_header(zf: zipfile.ZipFile, gcode_name: str, plate: PlateInfo, info: ThreeMFInfo) -> None:
    """Per-plate stats from a plate gcode header (time, layers, weight, max_z)."""
    try:
        header = zf.read(gcode_name).decode("utf-8", "replace")[:6000]
    except Exception:
        return
    m = re.search(r"; model printing time: ([^;]+)", header)
    if m:
        plate.print_time_sec = _parse_duration(m.group(1))
    m = re.search(r"; total estimated time: ([^;\n]+)", header)
    if m:
        plate.total_time_sec = _parse_duration(m.group(1))
    m = re.search(r"; total layer number: (\d+)", header)
    if m:
        plate.layer_count = int(m.group(1))
    m = re.search(r"; total filament weight \[g\] : ([\d.]+)", header)
    if m:
        plate.filament_weight_g = float(m.group(1))
    m = re.search(r"; total filament length \[mm\] : ([\d.]+)", header)
    if m:
        plate.filament_length_mm = float(m.group(1))
    m = re.search(r";\s*max_z_height:\s*([\d.]+)", header)
    if m:
        plate.max_z_height = float(m.group(1))
    if not info.slicer_version:
        m = re.search(r"; BambuStudio ([\d.]+)", header)
        if m:
            info.slicer_version = m.group(1)
    # first-layer bed temp from the first real M140 command in the file
    try:
        with zf.open(gcode_name) as fh:
            window = fh.read(120 * 1024).decode("utf-8", "replace")
        for line in window.splitlines():
            s = line.strip()
            if s.startswith("M140 S") and not s.startswith(";"):
                mm2 = re.match(r"M140 S(\d+)", s)
                if mm2:
                    plate.first_layer_bed_temp = int(mm2.group(1))
                    break
    except Exception:
        pass


def _parse_gcode_3mf(zf: zipfile.ZipFile, info: ThreeMFInfo) -> None:
    """Parse a sliced multi-plate .gcode.3mf.

    Real Bambu structure (verified against Bambu Studio 02.08 output):
    - Metadata/plate_N.json has 2D bbox_objects (xmin,xmax,ymin,ymax) - NO height.
    - Metadata/plate_N.gcode: one complete print per plate; per-plate stats
      (time, layers, filament, max_z_height) come from its own header.
    """
    names = zf.namelist()
    gcode_files = [n for n in names if n.lower().endswith(".gcode")]
    info.gcode_size = sum(zf.getinfo(n).file_size for n in gcode_files)

    # 1) object names + 2D bboxes from plate JSON (bbox is a LIST, not dict!)
    for name in names:
        if not name.lower().endswith(".json") or "plate_" not in name:
            continue
        try:
            data = json.loads(zf.read(name).decode("utf-8", "ignore"))
        except Exception:
            continue
        objects = []
        for obj in data.get("bbox_objects", []) or data.get("objects", []):
            ident = obj.get("name", obj.get("id", "object"))
            h = 0.0
            w = 0.0
            d = 0.0
            x_min = x_max = y_min = y_max = 0.0
            bbox = obj.get("bbox")
            if isinstance(bbox, dict):
                try:
                    x_min, x_max = float(bbox["xmin"]), float(bbox["xmax"])
                    y_min, y_max = float(bbox["ymin"]), float(bbox["ymax"])
                    w = round(x_max - x_min, 2)
                    d = round(y_max - y_min, 2)
                    h = round(float(bbox.get("zmax", 0)) - float(bbox.get("zmin", 0)), 2)
                except (KeyError, TypeError, ValueError):
                    pass
            elif isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                # Bambu 2D bbox: [xmin, xmax, ymin, ymax]
                try:
                    x_min, x_max = float(bbox[0]), float(bbox[1])
                    y_min, y_max = float(bbox[2]), float(bbox[3])
                    w = round(x_max - x_min, 2)
                    d = round(y_max - y_min, 2)
                except (TypeError, ValueError):
                    pass
            objects.append(ObjectInfo(
                name=str(ident), height_mm=h, width_mm=w, depth_mm=d,
                x_min=round(x_min, 2), x_max=round(x_max, 2),
                y_min=round(y_min, 2), y_max=round(y_max, 2),
            ))
        if objects:
            m = re.search(r"plate_(\d+)", name)
            idx = int(m.group(1)) if m else 1
            info.plates.append(PlateInfo(index=idx, objects=objects))

    # 2) per-plate gcode headers: time, layers, filament, bed temp, max_z
    for gf in gcode_files:
        plate_match = re.search(r"plate_(\d+)", gf)
        pidx = int(plate_match.group(1)) if plate_match else 1
        plate = _plate_for(info, pidx)
        if not plate.gcode_name:
            plate.gcode_name = gf
        _parse_plate_gcode_header(zf, gf, plate, info)

    # 3) fill object heights from the plate max only when the object has no
    # height of its own: max_z_height is the TALLEST object on the plate and
    # would defeat the toolhead-jam guard for short objects.
    for plate in info.plates:
        if plate.max_z_height > 0:
            for o in plate.objects:
                if o.height_mm == 0.0:
                    o.height_mm = plate.max_z_height

    # 4) slice_info.config: object names, per-plate filaments, printer model
    si = next((n for n in names if n.endswith("slice_info.config")), None)
    if si:
        try:
            root = _safe_fromstring(zf.read(si))
            for objel in root.iter("object"):
                oid = objel.get("identify_id")
                oname = objel.get("name")
                if oid and oname:
                    info.object_names[str(oid)] = oname
            for plate_el in root.iter("plate"):
                idx_el = plate_el.find("metadata[@key='index']")
                pidx = int(idx_el.get("value")) if idx_el is not None and (idx_el.get("value") or "").isdigit() else 1
                fils = []
                for fil in plate_el.findall("filament"):
                    fils.append({
                        "type": fil.get("type", ""),
                        "color": fil.get("color", ""),
                        "weight_g": round(float(fil.get("used_g", "0") or 0), 2),
                        "used_m": round(float(fil.get("used_m", "0") or 0), 2),
                    })
                if fils:
                    _plate_for(info, pidx).filaments = fils
            for plate_el in root.iter("plate"):
                mm = plate_el.find("metadata[@key='printer_model_id']")
                if mm is not None and mm.get("value"):
                    info.printer_model = mm.get("value")
                    break
        except (ET.ParseError, ValueError):
            pass

    # human-readable printer name (project_settings.config: "printer_model": "Bambu Lab A1")
    ps = next((n for n in names if n.endswith("project_settings.config")), None)
    if ps:
        try:
            txt = zf.read(ps).decode("utf-8", "replace")
            m = re.search(r'"printer_model"\s*:\s*"([^"]+)"', txt)
            if m and m.group(1).strip():
                info.printer_model_full = m.group(1).strip()
        except Exception:
            pass

    # 5) top-level aggregates across all plates (single-plate files unchanged)
    info.plates.sort(key=lambda p: p.index)
    info.print_time_sec = round(sum(p.print_time_sec for p in info.plates), 2)
    info.total_time_sec = round(sum(p.total_time_sec for p in info.plates), 2)
    info.layer_count = sum(p.layer_count for p in info.plates)
    info.filament_weight_g = round(sum(p.filament_weight_g for p in info.plates), 2)
    info.filament_length_mm = round(sum(p.filament_length_mm for p in info.plates), 2)
    bed_temps = [p.first_layer_bed_temp for p in info.plates if p.first_layer_bed_temp]
    if bed_temps:
        info.first_layer_bed_temp = max(bed_temps)
    agg: dict = {}
    for p in info.plates:
        for f in p.filaments:
            key = (f["type"], f["color"])
            e = agg.setdefault(key, {"type": f["type"], "color": f["color"], "weight_g": 0.0, "used_m": 0.0})
            e["weight_g"] += f["weight_g"]
            e["used_m"] += f["used_m"]
    if agg:
        info.filaments = [
            {**e, "weight_g": round(e["weight_g"], 2), "used_m": round(e["used_m"], 2)}
            for e in agg.values()
        ]


def _parse_duration(text: str) -> float:
    """Parse Bambu duration strings like '1h 6m 48s' or '2d 3h 4m' into seconds."""
    total = 0.0
    for value, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([dhms])", text.lower()):
        v = float(value)
        if unit == "d":
            total += v * 86400
        elif unit == "h":
            total += v * 3600
        elif unit == "m":
            total += v * 60
        else:
            total += v
    return total


def extract_thumbnail(fileobj: io.BufferedIOBase, size: str = "large", plate: int = 1) -> bytes | None:
    """Extract the plate render PNG from a .gcode.3mf.

    size: 'large' (plate_N.png), 'small' (plate_N_small.png).
    'middle' is accepted and treated as 'large' (top_N.png is not a
    reliable plate render). plate: 1-based plate index.
    """
    preferred = {
        "large": [f"Metadata/plate_{plate}.png", f"Metadata/plate_no_light_{plate}.png"],
        "small": [f"Metadata/plate_{plate}_small.png"],
    }
    try:
        with zipfile.ZipFile(fileobj) as zf:
            all_names = zf.namelist()
            candidates = preferred.get(size, preferred["large"])
            for c in candidates:
                if c in all_names:
                    return zf.read(c)
            # fallback: the SAME plate's small render (never another plate's)
            for n in all_names:
                if re.match(rf"^Metadata/plate_{plate}(?:_small)?\.png$", n):
                    return zf.read(n)
    except zipfile.BadZipFile:
        pass
    return None


def parse_3mf(fileobj: io.BufferedIOBase, filename: str) -> ThreeMFInfo:
    """Parse a 3MF from a file-like object."""
    info = ThreeMFInfo(filename=filename, is_gcode_3mf=False)
    with zipfile.ZipFile(fileobj) as zf:
        names = zf.namelist()
        has_gcode = any(n.lower().endswith(".gcode") for n in names)
        if has_gcode:
            info.is_gcode_3mf = True
            _parse_gcode_3mf(zf, info)
        else:
            _parse_bambu_3mf(zf, info)
        if not info.plates:
            info.warnings.append("No objects found in 3MF metadata")
    return info


def extract_plate_gcodes(fileobj: io.BufferedIOBase, max_uncompressed_mb: int = 400) -> dict:
    """Extract ALL plate gcodes from a .gcode.3mf, keyed by 1-based plate index.

    Total uncompressed size is capped (zip-bomb guard); members without a
    'plate_N' in the name are numbered after the existing indices.
    """
    cap = max_uncompressed_mb * 1024 * 1024
    out: dict = {}
    with zipfile.ZipFile(fileobj) as zf:
        gcode_files = [n for n in zf.namelist() if n.lower().endswith(".gcode")]
        if not gcode_files:
            raise ValueError("No .gcode file inside 3MF - did you slice it?")
        total = 0
        next_idx = 1
        for n in gcode_files:
            zinfo = zf.getinfo(n)
            total += zinfo.file_size
            if zinfo.file_size > cap or total > cap:
                raise ValueError(
                    f"Uncompressed gcode exceeds the {max_uncompressed_mb} MB limit (possible zip bomb)"
                )
            m = re.search(r"plate_(\d+)", n)
            if m:
                idx = int(m.group(1))
            else:
                while next_idx in out:
                    next_idx += 1
                idx = next_idx
            out[idx] = zf.read(n).decode("utf-8", "replace")
            next_idx = max(next_idx, idx + 1)
    return out


# per-plate asset members that must be dropped when collapsing to one plate
_PLATE_ASSET_RES = (
    re.compile(r"^Metadata/plate_(?:no_light_)?(\d+)(?:_small)?\.(?:png|json|gcode|gcode\.md5)$"),
    re.compile(r"^Metadata/(?:top|pick)_(\d+)\.png$"),
)


def _plate_asset_index(name: str) -> int | None:
    """Plate index of a per-plate asset member, or None if the member is shared."""
    for rx in _PLATE_ASSET_RES:
        m = rx.match(name)
        if m:
            return int(m.group(1))
    return None


def _xml_bytes(root: ET.Element) -> bytes:
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")


def _trim_model_settings(xml_bytes: bytes, keep: int) -> bytes:
    root = _safe_fromstring(xml_bytes)
    for plate in list(root.findall("plate")):
        pid = plate.find("metadata[@key='plater_id']")
        if pid is not None and (pid.get("value") or "").isdigit() and int(pid.get("value")) != keep:
            root.remove(plate)
    for el in root:
        el.tail = "\n  "
    root.text = "\n  "
    return _xml_bytes(root)


def _trim_rels(xml_bytes: bytes, keep_gcode: str) -> bytes:
    root = _safe_fromstring(xml_bytes)
    keep_base = keep_gcode.split("/")[-1]
    for rel in list(root):
        if not rel.tag.endswith("Relationship"):
            continue
        target = rel.get("Target", "")
        if target.lower().endswith(".gcode") and target.split("/")[-1] != keep_base:
            root.remove(rel)
    return _xml_bytes(root)


def _trim_slice_info(xml_bytes: bytes, keep: int, totals: dict | None) -> bytes:
    """Keep only the target plate and rewrite its sequence totals.

    totals: {"prediction_sec": float, "weight_g": float,
             "per_filament": {(type, color): {"used_g": float, "used_m": float}}}
    Per-filament values are matched by (type, color); the legacy scalar keys
    used_g/used_m still apply to every remaining filament as a fallback.
    """
    root = _safe_fromstring(xml_bytes)
    for plate in list(root.findall("plate")):
        idx = plate.find("metadata[@key='index']")
        if idx is None or (idx.get("value") or "").strip() != str(keep):
            root.remove(plate)
            continue
        if not totals:
            continue
        pred = plate.find("metadata[@key='prediction']")
        if pred is not None and totals.get("prediction_sec"):
            pred.set("value", str(int(round(totals["prediction_sec"]))))
        weight = plate.find("metadata[@key='weight']")
        if weight is not None and totals.get("weight_g"):
            weight.set("value", f"{totals['weight_g']:.2f}")
        per = totals.get("per_filament") or {}
        for fil in plate.findall("filament"):
            entry = per.get((fil.get("type", ""), fil.get("color", "")))
            if entry:
                fil.set("used_g", f"{entry['used_g']:.2f}")
                fil.set("used_m", f"{entry['used_m']:.2f}")
            else:
                if totals.get("used_g") is not None:
                    fil.set("used_g", f"{totals['used_g']:.2f}")
                if totals.get("used_m") is not None:
                    fil.set("used_m", f"{totals['used_m']:.2f}")
    for el in root:
        el.tail = "\n  "
    root.text = "\n  "
    return _xml_bytes(root)


def _trim_filament_sequence(raw: bytes, keep: int) -> bytes:
    try:
        data = json.loads(raw.decode("utf-8", "ignore"))
    except Exception:
        return raw
    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if k == f"plate_{keep}"}
    return json.dumps(data).encode("utf-8")


def repack_single_plate_3mf(
    original_fileobj: io.BufferedIOBase,
    new_gcode: str,
    plate_index: int = 1,
    slice_totals: dict | None = None,
) -> io.BytesIO:
    """Build a single-plate .gcode.3mf carrying the merged sequence.

    plate_N.gcode is replaced with new_gcode (md5 recomputed, uppercase);
    assets and config entries of all other plates are removed so the printer
    sees exactly one plate. slice_totals ({prediction_sec, weight_g,
    per_filament}) optionally rewrites the remaining plate's slice_info
    metadata (weight, prediction and per-(type,color) filament usage).
    """
    out = io.BytesIO()
    new_bytes = new_gcode.encode("utf-8")
    new_md5 = hashlib.md5(new_bytes).hexdigest().upper().encode("ascii")
    with zipfile.ZipFile(original_fileobj) as src:
        names = src.namelist()
        gcode_files = [n for n in names if n.lower().endswith(".gcode")]
        if not gcode_files:
            raise ValueError("No .gcode file inside 3MF - did you slice it?")
        target = f"Metadata/plate_{plate_index}.gcode"
        if target not in gcode_files:
            target = max(gcode_files, key=lambda n: src.getinfo(n).file_size)
            m = re.search(r"plate_(\d+)", target)
            plate_index = int(m.group(1)) if m else 1
        target_md5 = target + ".md5"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                name = item.filename
                if name == target:
                    dst.writestr(item, new_bytes)
                    continue
                if name == target_md5:
                    dst.writestr(item, new_md5)
                    continue
                asset_idx = _plate_asset_index(name)
                if asset_idx is not None and asset_idx != plate_index:
                    continue
                if name.endswith("model_settings.config"):
                    dst.writestr(item, _trim_model_settings(src.read(name), plate_index))
                    continue
                if name.endswith("model_settings.config.rels"):
                    dst.writestr(item, _trim_rels(src.read(name), target))
                    continue
                if name.endswith("slice_info.config"):
                    dst.writestr(item, _trim_slice_info(src.read(name), plate_index, slice_totals))
                    continue
                if name.endswith("filament_sequence.json"):
                    dst.writestr(item, _trim_filament_sequence(src.read(name), plate_index))
                    continue
                dst.writestr(item, src.read(name))
            if target_md5 not in names:
                dst.writestr(target_md5, new_md5)
    out.seek(0)
    return out