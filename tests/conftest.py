"""Shared fixtures: realistic Bambu 3MF + corrupt/edge-case uploads."""
import io
import json
import sys
import zipfile

sys.path.insert(0, ".")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

GCODE = """; HEADER_BLOCK_START
; BambuStudio 02.08.02.61
; model printing time: 1h 6m 48s; total estimated time: 1h 13m 5s
; total layer number: 150
; total filament length [mm] : 11247.87
; total filament weight [g] : 33.55
; max_z_height: 30.00
; HEADER_BLOCK_END

; CONFIG_BLOCK_START
; machine_end_gcode = ;===== date: 20260513 =====================\\nM140 S0 ; turn off bed\\nM104 S0\\nM18 X Y Z\\n
; CONFIG_BLOCK_END

; EXECUTABLE_BLOCK_START
M73 P0 R73
M106 S0
G392 S0
M104 S140
M140 S65
M109 S25 H140
M104 S220
M109 S220
G28 Z
M190 S65; ensure bed temp
M104 S220 ; prepare to print
M109 S220
G1 X100 Y100 E1.0 F6000
G1 X150 Y100 E2.0 F6000
G1 X150 Y150 E3.0 F6000
M400
M140 S0 ; turn off bed
M106 S0 ; turn off fan
M620 S255
G1 X267 F15000
T255
M621 S255
M104 S0 ; turn off hotend
M17 S
G1 Z256 F600
M400 P100
M17 R
G90
G1 X-48 Y180 F3600
M220 S100  ; Reset feedrate magnitude
M201.2 K1.0 ; Reset acc magnitude
M73.2   R1.0 ;Reset left time magnitude
M1002 set_gcode_claim_speed_level : 0
M17
M400 S1
M1006 W
M400
M18 X Y Z
M73 P100 R0
; EXECUTABLE_BLOCK_END
"""

# Minimal machine-start variant WITH the native
# '; MACHINE_START_GCODE_END' marker + a purge strip after it (mirrors real
# A1 output): pre = heat/home, post = purge/cali lines (front-of-plate).
GCODE_WITH_PURGE = """; HEADER_BLOCK_START
; BambuStudio 02.08.02.61
; model printing time: 1h 6m 48s; total estimated time: 1h 13m 5s
; total layer number: 150
; total filament length [mm] : 11247.87
; total filament weight [g] : 33.55
; max_z_height: 30.00
; HEADER_BLOCK_END

; EXECUTABLE_BLOCK_START
M140 S65
M104 S220
M109 S220
G28
;===== auto extrude cali start =========================
M1002 judge_flag extrude_cali_flag
M622 J1
    G0 X128 E10 F720
    G0 X148 E.3742 F4800
M623
;===== auto extrude cali end ========================
; MACHINE_START_GCODE_END
G1 X120 Y1.2 F6000
G1 X140 Y1.2 E.5 F6000 ; purge line on plate edge
G1 X100 Y100 E1.0 F6000
G1 X150 Y100 E2.0 F6000
G1 X150 Y150 E3.0 F6000
M140 S0 ; turn off bed
M18 X Y Z
; EXECUTABLE_BLOCK_END
"""


# 1x1 transparent PNG (smallest valid plate render placeholder)
PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xfc\xff\xff?\x00\x05"
    b"\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82"
)


SLICE_INFO_TWO_PLATES = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="index" value="1"/>
    <metadata key="printer_model_id" value="N2S"/>
    <metadata key="prediction" value="4020"/>
    <metadata key="weight" value="33.55"/>
    <object identify_id="11" name="Plate1.stl" />
    <filament id="1" type="PLA" color="#D5B6A4" used_m="11.25" used_g="33.55"/>
  </plate>
  <plate>
    <metadata key="index" value="2"/>
    <metadata key="printer_model_id" value="N2S"/>
    <metadata key="prediction" value="5740"/>
    <metadata key="weight" value="42.92"/>
    <object identify_id="22" name="Plate2.stl" />
    <filament id="1" type="PETG" color="#1A1A1A" used_m="14.39" used_g="42.92"/>
  </plate>
</config>
"""


def make_multi_plate_3mf(h1: float = 30.0, h2: float = 54.0) -> bytes:
    """Two-plate gcode 3MF: distinct objects, heights, temps and filaments."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr(
            "Metadata/model_settings.config",
            '<config>'
            '<plate><metadata key="plater_id" value="1"/>'
            '<metadata key="gcode_file" value="Metadata/plate_1.gcode"/></plate>'
            '<plate><metadata key="plater_id" value="2"/>'
            '<metadata key="gcode_file" value="Metadata/plate_2.gcode"/></plate>'
            '</config>',
        )
        zf.writestr(
            "Metadata/_rels/model_settings.config.rels",
            '<?xml version="1.0"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/Metadata/plate_1.gcode" Id="rel-1" '
            'Type="http://schemas.bambulab.com/package/2021/gcode"/>'
            '<Relationship Target="/Metadata/plate_2.gcode" Id="rel-2" '
            'Type="http://schemas.bambulab.com/package/2021/gcode"/>'
            '</Relationships>',
        )
        zf.writestr("Metadata/plate_1.json", json.dumps({
            "bbox_all": [100, 150, 100, 150],
            "bbox_objects": [{"area": 100.0, "bbox": [100, 150, 100, 150], "id": 11, "name": "Plate1.stl"}],
            "version": 2,
        }))
        zf.writestr("Metadata/plate_2.json", json.dumps({
            "bbox_all": [60, 110, 80, 150],
            "bbox_objects": [{"area": 100.0, "bbox": [60, 110, 80, 150], "id": 22, "name": "Plate2.stl"}],
            "version": 2,
        }))
        zf.writestr("Metadata/plate_1.png", PNG_1PX)
        zf.writestr("Metadata/plate_2.png", PNG_1PX)
        zf.writestr("Metadata/plate_no_light_1.png", PNG_1PX)
        zf.writestr("Metadata/plate_no_light_2.png", PNG_1PX)
        zf.writestr("Metadata/top_2.png", PNG_1PX)
        zf.writestr("Metadata/pick_2.png", PNG_1PX)
        g1 = (
            GCODE_WITH_PURGE.replace("30.00", f"{h1:.2f}")
            .replace("G1 X100 Y100 E1.0 F6000", "; OBJECT_ID: 11\nG1 X100 Y100 E1.0 F6000")
        )
        g2 = (
            GCODE_WITH_PURGE.replace("30.00", f"{h2:.2f}")
            .replace("; total filament weight [g] : 33.55", "; total filament weight [g] : 42.92")
            .replace("; total filament length [mm] : 11247.87", "; total filament length [mm] : 14320.50")
            .replace("M140 S65", "M140 S60")
            .replace("M104 S220", "M104 S230")
            .replace("G1 X100 Y100 E1.0 F6000", "; OBJECT_ID: 22\nG1 X60 Y80 E1.0 F6000")
            .replace("G1 X150 Y100 E2.0 F6000", "G1 X110 Y80 E2.0 F6000")
            .replace("G1 X150 Y150 E3.0 F6000", "G1 X110 Y150 E3.0 F6000")
        )
        zf.writestr("Metadata/plate_1.gcode", g1)
        zf.writestr("Metadata/plate_2.gcode", g2)
        zf.writestr("Metadata/plate_1.gcode.md5", "0" * 32)
        zf.writestr("Metadata/plate_2.gcode.md5", "0" * 32)
        zf.writestr("Metadata/slice_info.config", SLICE_INFO_TWO_PLATES)
        zf.writestr("Metadata/filament_sequence.json", '{"plate_1": {}, "plate_2": {}}')
    return buf.getvalue()


def make_overlap_3mf() -> bytes:
    """Single plate, two objects sharing one push lane (X bands overlap)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr(
            "Metadata/model_settings.config",
            '<config><plate><metadata key="gcode_file" value="Metadata/plate_1.gcode"/></plate></config>',
        )
        zf.writestr("Metadata/plate_1.json", json.dumps({
            "bbox_all": [80, 180, 40, 150],
            "bbox_objects": [
                {"area": 1.0, "bbox": [100, 150, 120, 150], "id": 11, "name": "Rear.stl"},
                {"area": 1.0, "bbox": [120, 180, 40, 90], "id": 22, "name": "Front.stl"},
            ],
            "version": 2,
        }))
        zf.writestr("Metadata/plate_1.png", PNG_1PX)
        g = GCODE.replace(
            "G1 X100 Y100 E1.0 F6000", "; OBJECT_ID: 11\nG1 X100 Y120 E1.0 F6000"
        ).replace(
            "G1 X150 Y100 E2.0 F6000", "G1 X150 Y150 E2.0 F6000"
        ).replace(
            "G1 X150 Y150 E3.0 F6000",
            "; OBJECT_ID: 22\nG1 X120 Y40 E3.0 F6000\nG1 X180 Y40 E4.0 F6000\nG1 X180 Y90 E5.0 F6000",
        )
        zf.writestr("Metadata/plate_1.gcode", g)
    return buf.getvalue()


def make_project_3mf() -> bytes:
    """Unsliced project 3MF: 2 plates, 3 objects with distinct heights.

    Mirrors the real Bambu/Orca layout: model_settings.config maps plates to
    object ids (metadata plater_id / object_id), 3dmodel.model maps object
    ids to part files (component p:path) or carries an inline mesh.
    """
    core = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    prod = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"

    def mesh(x: float, y: float, z: float) -> str:
        return (
            "<mesh><vertices>"
            '<vertex x="0" y="0" z="0"/>'
            f'<vertex x="{x}" y="0" z="0"/>'
            f'<vertex x="0" y="{y}" z="0"/>'
            f'<vertex x="0" y="0" z="{z}"/>'
            "</vertices><triangles/></mesh>"
        )

    model3d = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<model unit="millimeter" xmlns="{core}" xmlns:p="{prod}">'
        "<resources>"
        '<object id="1" type="model"><components>'
        '<component p:path="/3D/Objects/Short.stl.model" objectid="10"/>'
        "</components></object>"
        '<object id="2" type="model"><components>'
        '<component p:path="/3D/Objects/Tall.stl.model" objectid="20"/>'
        "</components></object>"
        f'<object id="3" type="model">{mesh(5, 5, 15)}</object>'
        "</resources>"
        '<build><item objectid="1" printable="1"/><item objectid="2" printable="1"/>'
        '<item objectid="3" printable="1"/></build>'
        "</model>"
    )
    model_settings = (
        "<config>"
        '<object id="1"><metadata key="name" value="Short.stl"/></object>'
        '<object id="2"><metadata key="name" value="Tall.stl"/></object>'
        '<object id="3"><metadata key="name" value="Inline.stl"/></object>'
        "<plate>"
        '<metadata key="plater_id" value="1"/>'
        '<model_instance><metadata key="object_id" value="1"/></model_instance>'
        '<model_instance><metadata key="object_id" value="2"/></model_instance>'
        '<model_instance><metadata key="object_id" value="3"/></model_instance>'
        "</plate>"
        "<plate>"
        '<metadata key="plater_id" value="2"/>'
        '<model_instance><metadata key="object_id" value="2"/></model_instance>'
        "</plate>"
        "</config>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("3D/3dmodel.model", model3d)
        zf.writestr("3D/Objects/Short.stl.model", f'<model>{mesh(10, 20, 30)}</model>')
        zf.writestr("3D/Objects/Tall.stl.model", f'<model>{mesh(40, 50, 60)}</model>')
        zf.writestr("Metadata/model_settings.config", model_settings)
    return buf.getvalue()


def make_gcode_3mf(height: float = 30.0, depth: float = 45.44, with_purge: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr(
            "Metadata/model_settings.config",
            '<config><plate><metadata key="gcode_file" value="Metadata/plate_1.gcode"/></plate></config>',
        )
        objects = {
            "bbox_all": [50, 100, 150, 200],
            "bbox_objects": [
                {"area": 1756.13, "bbox": [50, 100, 150, 200], "id": 223, "layer_height": 0.2, "name": "Lenker.stl"}
            ],
            "bed_type": "textured_plate",
            "version": 2,
        }
        zf.writestr("Metadata/plate_1.json", json.dumps(objects))
        zf.writestr("Metadata/plate_1.png", PNG_1PX)
        tpl = GCODE_WITH_PURGE if with_purge else GCODE
        tpl = tpl.replace("30.00", f"{height:.2f}").replace("Y150 E3.0", f"Y{150 + depth:.2f} E3.0")
        zf.writestr(
            "Metadata/plate_1.gcode",
            tpl,
        )
    return buf.getvalue()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def tall_3mf():
    return make_gcode_3mf(30.0)


@pytest.fixture
def short_3mf():
    return make_gcode_3mf(5.0)


@pytest.fixture
def purge_3mf():
    return make_gcode_3mf(30.0, with_purge=True)


@pytest.fixture
def multi_3mf():
    return make_multi_plate_3mf()


@pytest.fixture
def overlap_3mf():
    return make_overlap_3mf()


@pytest.fixture
def project_3mf():
    return make_project_3mf()
