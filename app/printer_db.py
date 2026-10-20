"""Printer database: per-model bed + toolhead geometry for collision-safe
ejection paths.

Data lives in printers.json (same directory) - THIS module is the loader.
Geometry sources: BambuStudio/OrcaSlicer BBL machine profiles and, since
schema v5, the OrcaSlicer machine profiles for Prusa, Creality, Elegoo,
Anycubic, QIDI, Sovol and Voron. Full provenance, URLs and commit hashes:
see PRINTER_DB_SOURCES.md in the repo root.

Schema (printers.json, version 5 - grouped + vendor):

    "A1": {
      "name": "A1",
      "vendor": "bambu",                 # key into top-level "vendors"
      "machine": {
        "printable":  {"x", "y", "z"},   # slicer printable_area/-height, mm
        "structure":  "bedslinger",      # key into top-level "structures"
        "bed_exclude": {"x", "y"}        # front-left keep-free area, 0/0 = none
      },
      "kinematics": {
        "rear_command_limit": 262.5,     # parked toolhead Y behind the plate
        "rear_command_limit_source":     # key into "provenance_levels"
            "verified_gcode" | "end_gcode_park" | "no_park" | "estimate",
        "front_lip_y": 0.0               # commanded Y at the front edge (push end)
      },
      "toolhead": {
        "body_height": 4.76,             # slicer nozzle_height (nozzle tip -> body)
        "clearance_radius": 73.0,        # slicer extruder_clearance_max_radius
        "dist_to_rod": 56.5,             # slicer extruder_clearance_dist_to_rod
        "footprint": {"width", "depth", # APP ESTIMATE (animation + push-lane
                      "estimated": true},# warning) - no official vendor data
        "estimated": true                # non-Bambu: body height/footprint assumed
      },
      "suitability": "verified",         # key into "suitability_levels"
      "suitability_note": "...",
      "source": "..."                    # provenance of the values above
    }

Top-level maps are the single source of truth for display text:
"vendors" (label + name prefixes + order), "structures" (label +
mechanism_short), "suitability_levels" (label + description),
"provenance_levels" (label + description).

`no_park` means the vendor's native end gcode has no rear position beyond
the printable area: the pusher starts at the bed's rear edge. Keep the
rear-most part clear of the toolhead footprint.

Coordinate conventions for this app (A1 bedslinger, derived from native
gcode):
- Commanded Y = toolhead position in bed coords. Bed slides forward =>
  toolhead reaches plate-rear positions (Y~262 = behind-plate brush zone).
- The bed is 256 deep (A1); commanded Y > printable depth reaches the
  behind-plate brush/wipe zone.
"""
import json
from pathlib import Path

_JSON_PATH = Path(__file__).with_name("printers.json")
_DATA = json.loads(_JSON_PATH.read_text(encoding="utf-8"))

PRINTERS: dict = _DATA["printers"]
ALIASES: dict = _DATA.get("aliases", {})
DEFAULT_PRINTER: str = _DATA.get("default", "A1")
STRUCTURES: dict = _DATA.get("structures", {})
SUITABILITY_LEVELS: dict = _DATA.get("suitability_levels", {})
PROVENANCE_LEVELS: dict = _DATA.get("provenance_levels", {})
VENDORS: dict = _DATA.get("vendors", {})


def find_printer_key(model_id: str | None) -> str | None:
    """DB key for a model id, plain name or vendor-prefixed full name.

    Handles slicer model ids ('N2S', 'Creality-K1', 'MK4S'), DB names and
    full printer names ('Bambu Lab A1', 'Prusa MK4S'). None = unknown.
    """
    if not model_id:
        return None
    value = str(model_id).strip()
    if value in PRINTERS:
        return value
    if value in ALIASES and ALIASES[value] in PRINTERS:
        return ALIASES[value]
    for vendor in VENDORS.values():
        for prefix in vendor.get("prefixes", []):
            if value.startswith(prefix):
                stripped = value[len(prefix):].strip()
                if stripped in PRINTERS:
                    return stripped
                if stripped in ALIASES and ALIASES[stripped] in PRINTERS:
                    return ALIASES[stripped]
    return None


def is_known_printer(model_id: str | None) -> bool:
    """True when the model id/name/alias exists in the printer database."""
    return find_printer_key(model_id) is not None


def get_printer(model_id: str) -> dict:
    """model_id: printer_model_id from slice_info.config (e.g. 'N2S' = A1,
    'Creality-K1' = K1) or the plain/full printer name. Unknown models fall
    back to the default printer (A1 geometry) - callers should surface that
    via is_known_printer() instead of assuming the geometry is correct."""
    key = find_printer_key(model_id)
    return PRINTERS.get(key, PRINTERS[DEFAULT_PRINTER])


def resolve_display_name(model_id: str | None, full_name: str = "") -> str:
    """Human-readable model name for the UI (single truth for all pages).

    Priority: printers.json name (via key, alias, or vendor-prefixed full
    name) > spelled-out slicer name ('X1 Carbon' -> 'X1C') > raw model id
    (visible, never silent-wrong).
    """
    for candidate in (model_id, full_name):
        key = find_printer_key(candidate)
        if key:
            return PRINTERS[key].get("name", key)
    if full_name:
        stripped = full_name
        for vendor in VENDORS.values():
            for prefix in vendor.get("prefixes", []):
                if stripped.startswith(prefix):
                    stripped = stripped[len(prefix):].strip()
                    break
        # map slicer model names (incl. spelled-out variants) to DB names,
        # longest match first so 'X1 Carbon' wins over 'X1'
        spelled = {"X1 Carbon": "X1C", "X1": "X1"}
        if stripped in spelled:
            return spelled[stripped]
        candidates = [kn for kn in (p.get("name", "") for p in PRINTERS.values())
                      if kn and stripped == kn]
        if candidates:
            return candidates[0]
    return full_name or model_id or ""