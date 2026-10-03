"""API tests: happy paths + edge cases (corrupt, empty, invalid params).

Uses V2 ejection params: t_lift, push_z, cool_temp_bed, safety_wait_s.
"""
import io
import sys
import zipfile

sys.path.insert(0, ".")


def test_index(client):
    r = client.get("/")
    assert r.status_code == 200 and "bambu-sequencer" in r.text


def test_inspect(client, tall_3mf):
    r = client.post("/api/inspect", files={"file": ("m.gcode.3mf", tall_3mf, "application/octet-stream")})
    assert r.status_code == 200
    d = r.json()
    assert d["is_gcode_3mf"]
    assert d["plates"][0]["min_object_height"] >= 30
    assert abs(d["max_z_height"] - 30.0) < 0.01


def test_stats(client, tall_3mf):
    r = client.post("/api/stats", files={"file": ("m.gcode.3mf", tall_3mf, "application/octet-stream")})
    assert r.status_code == 200
    s = r.json()
    assert s["print_time_human"] == "1h 6m"
    assert abs(s["filament_weight_g"] - 33.55) < 0.5
    assert s["layer_count"] == 150
    assert s["first_layer_bed_temp"] == 65
    assert s["thumbnail"].startswith("data:image/png")
    assert abs(s["max_z_height"] - 30.0) < 0.01


def test_thumbnail_endpoint(client, tall_3mf):
    r = client.post("/api/thumbnail", files={"file": ("m.gcode.3mf", tall_3mf, "application/octet-stream")})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_sequence_five_prints(client, tall_3mf):
    r = client.post(
        "/api/sequence",
        files={"file": ("m.gcode.3mf", tall_3mf, "application/octet-stream")},
        data={"plate_prints": '{"1": 5}', "t_lift": "5", "push_z": "0.4",
              "cool_temp_bed": "45", "safety_wait_s": "60", "min_height_mm": "7"},
    )
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        gc = zf.read([n for n in zf.namelist() if n.endswith(".gcode")][0]).decode()
    ls = gc.splitlines()
    starts = sum(1 for ln in ls if ln.strip() == "; EXECUTABLE_BLOCK_START")
    ends = sum(1 for ln in ls if ln.strip() == "; EXECUTABLE_BLOCK_END")
    assert starts == ends == 5
    assert sum(1 for ln in ls if ln.strip().startswith("M140 S0")) == 1
    assert "M190 S45" in gc
    assert "G4 S60" in gc, "G4 S<seconds> format required"


def test_sequence_v2_params(client, tall_3mf):
    """V2 params: t_lift, push_z, safety_wait_s all take effect."""
    r = client.post(
        "/api/sequence",
        files={"file": ("m.gcode.3mf", tall_3mf, "application/octet-stream")},
        data={"plate_prints": '{"1": 2}', "t_lift": "8", "push_z": "0.6",
              "cool_temp_bed": "50", "safety_wait_s": "30", "min_height_mm": "0"},
    )
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        gc = zf.read([n for n in zf.namelist() if n.endswith(".gcode")][0]).decode()
    # safe_z = max_z_height(30) + t_lift(8) = 38
    assert "G1 Z38" in gc, "safe_z should be 38 (30+8): not found"
    # push_z = 0.6
    assert "G1 Z0.6 F600" in gc, "push_z should be 0.6"
    # cool_temp = 50
    assert "M190 S50" in gc, "cool temp should be 50"
    # safety_wait = 30s
    assert "G4 S30" in gc, "safety wait should be 30s"


# ===== edge cases =====

def test_corrupt_zip_400(client):
    r = client.post("/api/stats", files={"file": ("m.gcode.3mf", b"this is not a zip", "application/octet-stream")})
    assert r.status_code == 400


def test_empty_file_400(client):
    r = client.post("/api/stats", files={"file": ("m.gcode.3mf", b"", "application/octet-stream")})
    assert r.status_code == 400


def test_wrong_extension_400(client, tall_3mf):
    r = client.post("/api/stats", files={"file": ("m.gcode.stl", tall_3mf, "application/octet-stream")})
    assert r.status_code == 400


def test_project_3mf_without_gcode_400(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("3D/3dmodel.model", "<model/>")
    r = client.post(
        "/api/sequence",
        files={"file": ("m.gcode.3mf", buf.getvalue(), "application/octet-stream")},
        data={"plate_prints": '{"1": 1}'},
    )
    assert r.status_code == 400
    assert "slice" in r.json()["detail"].lower()


def test_negative_push_z_422(client, tall_3mf):
    r = client.post(
        "/api/sequence",
        files={"file": ("m.gcode.3mf", tall_3mf, "application/octet-stream")},
        data={"plate_prints": '{"1": 1}', "push_z": "-5", "min_height_mm": "0"},
    )
    assert r.status_code == 422


def test_plate_prints_negative_422(client, tall_3mf):
    r = client.post(
        "/api/sequence",
        files={"file": ("m.gcode.3mf", tall_3mf, "application/octet-stream")},
        data={"plate_prints": '{"1": -1}', "min_height_mm": "0"},
    )
    assert r.status_code == 422


def test_plate_prints_required_422(client, tall_3mf):
    r = client.post(
        "/api/sequence",
        files={"file": ("m.gcode.3mf", tall_3mf, "application/octet-stream")},
        data={"min_height_mm": "0"},
    )
    assert r.status_code == 422


def test_height_check_rejects(client, short_3mf):
    r = client.post(
        "/api/sequence",
        files={"file": ("s.gcode.3mf", short_3mf, "application/octet-stream")},
        data={"plate_prints": '{"1": 1}', "min_height_mm": "7"},
    )
    assert r.status_code == 422 and "below required" in r.json()["detail"]


def test_height_check_bypass(client, short_3mf):
    r = client.post(
        "/api/sequence",
        files={"file": ("s.gcode.3mf", short_3mf, "application/octet-stream")},
        data={"plate_prints": '{"1": 1}', "min_height_mm": "0"},
    )
    assert r.status_code == 200


def test_animation(client, tall_3mf):
    r = client.post(
        "/api/animation",
        files={"file": ("m.gcode.3mf", tall_3mf, "application/octet-stream")},
        data={"t_lift": "5", "push_z": "0.4", "cool_temp_bed": "45", "safety_wait_s": "60"},
    )
    assert r.status_code == 200
    k = r.json()
    assert k["eject_enabled"] is True
    assert len(k["frames"]) >= 10
    assert abs(k["safe_z"] - 35.0) < 0.01, "safe_z should be 35 (30+5)"
    assert abs(k["max_z_height"] - 30.0) < 0.01


def test_animation_on_project_3mf_400(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
    r = client.post(
        "/api/animation",
        files={"file": ("m.gcode.3mf", buf.getvalue(), "application/octet-stream")},
        data={"t_lift": "5", "push_z": "0.4"},
    )
    assert r.status_code == 400


def test_output_filename_quoted(client, tall_3mf):
    r = client.post(
        "/api/sequence",
        files={"file": ("über lenker.gcode.3mf", tall_3mf, "application/octet-stream")},
        data={"plate_prints": '{"1": 1}', "min_height_mm": "0"},
    )
    assert r.status_code == 200
    assert r.headers["content-disposition"].count("filename") >= 1
    assert "UTF-8''" in r.headers["content-disposition"]


def test_cooling_bed_target_in_gcode(client, tall_3mf):
    r = client.post(
        "/api/sequence",
        files={"file": ("m.gcode.3mf", tall_3mf, "application/octet-stream")},
        data={"plate_prints": '{"1": 1}', "cool_temp_bed": "65", "min_height_mm": "0"},
    )
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        gc = zf.read([n for n in zf.namelist() if n.endswith(".gcode")][0]).decode()
    assert "M190 S65" in gc


def test_stats_true_size(client, tall_3mf):
    r = client.post("/api/stats", files={"file": ("m.gcode.3mf", tall_3mf, "application/octet-stream")})
    assert r.status_code == 200
    s = r.json()
    o = s["objects"][0]
    assert o["width_mm"] == 50.0
    assert o["depth_mm"] == 95.44
    assert o["x_min"] == 100.0
    assert o["y_max"] == 195.44


# ===== multi-plate =====


def test_stats_warns_push_lane_overlap(client, overlap_3mf):
    """Objects in the same X lane must produce the cascade/jam warning."""
    r = client.post("/api/stats", files={"file": ("m.gcode.3mf", overlap_3mf, "application/octet-stream")})
    assert r.status_code == 200
    warnings = " | ".join(r.json()["warnings"])
    assert "push lane" in warnings, warnings
    assert "Object 11" in warnings and "Object 22" in warnings, warnings


def test_printers_api_flat_contract(client):
    """Nested printers.json is flattened for the API: labels + park provenance."""
    r = client.get("/api/printers")
    assert r.status_code == 200
    rows = r.json()["printers"]
    assert len(rows) >= 14
    a1 = next(p for p in rows if p["name"] == "A1")
    assert a1["printable_x"] == 256.0 and a1["printable_z"] == 256.0
    assert a1["rear_command_limit"] == 262.5
    assert a1["rear_command_limit_source"] == "verified_gcode"
    assert a1["rear_command_limit_label"] == "verified"
    assert a1["structure"] == "bedslinger"
    assert a1["structure_label"] == "Bedslinger"
    assert a1["mechanism_short"] == "bed slides under the toolhead"
    assert a1["suitability_label"] == "Verified"
    assert a1["note"]
    h2d = next(p for p in rows if p["name"] == "H2D")
    assert h2d["rear_command_limit_label"] == "estimated"


def test_stats_project_3mf_per_object_heights(client, project_3mf):
    """Unsliced project: stats show each object's own size, not the tallest."""
    r = client.post("/api/stats", files={"file": ("p.3mf", project_3mf, "application/octet-stream")})
    assert r.status_code == 200
    s = r.json()
    assert s["is_gcode_3mf"] is False
    plate1 = {o["name"]: o["height_mm"] for o in s["objects"] if o["plate"] == 1}
    assert plate1 == {"Short.stl": 30.0, "Tall.stl": 60.0, "Inline.stl": 15.0}


def test_stats_multi_plate(client, multi_3mf):
    r = client.post("/api/stats", files={"file": ("m.gcode.3mf", multi_3mf, "application/octet-stream")})
    assert r.status_code == 200
    s = r.json()
    assert s["plate_count"] == 2
    assert [p["index"] for p in s["plates"]] == [1, 2]
    assert abs(s["plates"][0]["max_z_height"] - 30.0) < 0.01
    assert abs(s["plates"][1]["max_z_height"] - 54.0) < 0.01
    assert s["plates"][0]["first_layer_bed_temp"] == 65
    assert s["plates"][1]["first_layer_bed_temp"] == 60
    assert [f["type"] for f in s["plates"][0]["filaments"]] == ["PLA"]
    assert [f["type"] for f in s["plates"][1]["filaments"]] == ["PETG"]
    assert s["plates"][0]["thumbnail"].startswith("data:image/png")
    assert s["plates"][0]["print_time_human"] != "—"
    assert len(s["objects"]) == 2
    assert {o["plate"] for o in s["objects"]} == {1, 2}
    assert {o["name"] for o in s["objects"]} == {"Plate1.stl", "Plate2.stl"}


def test_sequence_multi_plate(client, multi_3mf):
    r = client.post(
        "/api/sequence",
        files={"file": ("m.gcode.3mf", multi_3mf, "application/octet-stream")},
        data={"plate_prints": '{"1": 2, "2": 1}', "t_lift": "5", "push_z": "0.4",
              "cool_temp_bed": "45", "safety_wait_s": "60", "min_height_mm": "7"},
    )
    assert r.status_code == 200, r.text
    import hashlib
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        assert "Metadata/plate_2.gcode" not in names
        assert "Metadata/plate_2.png" not in names
        gc = zf.read("Metadata/plate_1.gcode").decode()
        md5 = zf.read("Metadata/plate_1.gcode.md5").decode()
        si = zf.read("Metadata/slice_info.config").decode()
    assert md5 == hashlib.md5(gc.encode()).hexdigest().upper()
    # sequence totals: 2x plate1 (PLA) + 1x plate2 (PETG)
    assert 'key="weight" value="110.02"' in si, "weight metadata must carry the sequence total"
    assert 'used_g="67.10"' in si, "PLA filament must keep its own aggregated use (2x 33.55)"
    ls = gc.splitlines()
    assert sum(1 for ln in ls if ln.strip() == "; EXECUTABLE_BLOCK_START") == 3
    assert sum(1 for ln in ls if ln.strip() == "; EXECUTABLE_BLOCK_END") == 3
    assert sum(1 for ln in ls if ln.strip().startswith("M140 S0")) == 1
    assert gc.count("G0 X128 E10") == 1  # purge strip once
    assert gc.count("auto-ejection (generated)") == 3
    assert "safe_z=35" in gc and "safe_z=59" in gc
    assert "_x3.gcode.3mf" in r.headers.get("content-disposition", "")


def test_sequence_multi_plate_skip_and_validation(client, multi_3mf):
    files = {"file": ("m.gcode.3mf", multi_3mf, "application/octet-stream")}
    # skip plate 1 -> output keeps the FIRST ACTIVE plate (2)
    r = client.post("/api/sequence", files=files,
                    data={"plate_prints": '{"1": 0, "2": 2}', "min_height_mm": "0"})
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        assert "Metadata/plate_2.gcode" in names and "Metadata/plate_1.gcode" not in names
        gc = zf.read("Metadata/plate_2.gcode").decode()
    assert sum(1 for ln in gc.splitlines() if "step 7" in ln) == 2
    assert all("X85" in ln for ln in gc.splitlines() if "step 7" in ln)
    # all skipped -> 422
    r = client.post("/api/sequence", files=files, data={"plate_prints": '{"1": 0, "2": 0}'})
    assert r.status_code == 422
    # unknown plate -> 422
    r = client.post("/api/sequence", files=files, data={"plate_prints": '{"9": 1}'})
    assert r.status_code == 422
    # invalid JSON -> 422
    r = client.post("/api/sequence", files=files, data={"plate_prints": "nope"})
    assert r.status_code == 422
    # out-of-range prints -> 422
    r = client.post("/api/sequence", files=files, data={"plate_prints": '{"1": 200}'})
    assert r.status_code == 422
    # non-integer prints -> 422 (no silent truncation)
    r = client.post("/api/sequence", files=files, data={"plate_prints": '{"1": 1.5}'})
    assert r.status_code == 422
    r = client.post("/api/sequence", files=files, data={"plate_prints": '{"1": true}'})
    assert r.status_code == 422


def test_animation_multi_plate_selector(client, multi_3mf):
    r = client.post(
        "/api/animation",
        files={"file": ("m.gcode.3mf", multi_3mf, "application/octet-stream")},
        data={"plate": "2", "t_lift": "5"},
    )
    assert r.status_code == 200
    k = r.json()
    assert k["plate"] == 2 and k["plate_count"] == 2
    assert abs(k["max_z_height"] - 54.0) < 0.01
    assert abs(k["safe_z"] - 59.0) < 0.01
    # unknown plate -> 404
    r = client.post(
        "/api/animation",
        files={"file": ("m.gcode.3mf", multi_3mf, "application/octet-stream")},
        data={"plate": "9"},
    )
    assert r.status_code == 404
