# Printer verification protocol

PlateLoop changes the printer's motion to push finished parts off the plate.
Only the Bambu Lab A1 is currently verified on real hardware. This protocol
describes how to test another model and submit a report for the printer
database.

You do **not** need a video to participate. A short written report is enough to
start the review; video and photos are optional. Partial or failed tests are
welcome too — they help document the model's limits.

> [!WARNING]
> Run the first complete cycle supervised. Keep clear of the printer, place the
> container where the part will land, and stop the machine if the toolhead
> collides with a part or the part jams. Testing is at your own risk.

## 1. Who can test

You can submit a report if you own the printer and can run a supervised test.
No remote access is needed or accepted. Test one model at a time and use the
exact model name from the Printers tab.

Good first candidates are one model per vendor, for example Prusa MK4S,
Creality K1, Elegoo Centauri Carbon, Anycubic Kobra, QIDI, Sovol or Voron.

## 2. Test object

A simple, sturdy part is best. You do not need a special test model:

- a 30 × 30 × 30 mm cube or a 30 mm diameter × 30 mm cylinder is recommended
- at least 7 mm tall (the default minimum-height check)
- PLA or PETG; avoid flexibles and engineering materials for the first test
- enough bed adhesion for the push, but no glue mess that hides the result
- **3 copies** in one sequence is ideal; one copy is still a useful partial report

Do not use a tiny or fragile part for the first verification. It is hard to
judge whether a failed push was caused by the geometry or the sequence.

## 3. Slice and generate

1. Slice in **OrcaSlicer** (or Bambu Studio for Bambu Lab printers).
2. Use a known-good machine profile and a 0.4 mm nozzle, 0.2 mm layers,
   3 walls and about 15 % infill.
3. Export **Plate as .gcode.3mf**.
4. Upload it to PlateLoop and set:
   - prints per plate: `3`
   - push height: `0.4 mm`
   - lift height: `5 mm`
   - ejection bed temp: `45 °C`
   - safety wait: `60 s`
   - minimum height: `7 mm`
5. Build the sequence and download the generated `.gcode.3mf`.
6. Do not edit the generated gcode by hand. The defaults above are fine; if you
   change a setting, mention it in the report.

## 4. Physical setup

- Put a container at the **front** of the printer where the part will land.
- Keep the push lane clear. Remove clips, tools, and other objects.
- Models without a native rear park zone need about **35 mm** of rear
  clearance; check the Printers tab for the model's rear Y limit.
- Check that the part is at least 7 mm tall.
- Make sure the plate is clean and the part adheres reliably.
- Dry the filament if the material is moisture-sensitive.

## 5. Run the test

1. Start the generated sequence.
2. Watch the first print, cooldown, push, and reheat.
3. Record a video **if it is easy for you**. A phone video of the first
   complete cycle is helpful, but optional. A photo after the push is also
   optional.
4. Stop the printer if:
   - the toolhead hits the part,
   - the part jams instead of sliding,
   - the part misses the container,
   - the plate or toolhead is damaged.
5. Let the sequence finish all three copies if the first cycle is clean.

You do not need to set up a camera or edit a video. A few sentences describing
the cycle are enough to start.

## 6. Submit the report

Open a **Printer verification report**:

<https://github.com/joshmadison/plateloop/issues/new?template=printer-verification.yml>

Include only what you know:

- exact printer model (required)
- result and a short description of what happened (required)
- firmware, slicer, nozzle, bed temperature, plate type, material and sequence
  settings (optional, but helpful)
- a video/photo link (optional)
- the safety checklist (required)

Do not include serial numbers, addresses, or private data. Blur the background
if necessary.

## 7. What counts as verified

A model is marked **verified** when a maintainer has reviewed a report showing:

- a complete multi-copy sequence,
- a clean push into the container,
- no toolhead collision or manual intervention,
- the setup documented as far as known.

A video or photo is helpful but **not required**. If the written report is
detailed enough to judge the cycle, it can be accepted. If the evidence is thin,
the maintainer may keep the model as `expected` and ask a follow-up question.

A model stays **expected** when the kinematics and geometry are close to a
verified model but a full physical test is still missing.

A model stays **needs-test** when there is no complete physical evidence.

One print or a manually edited file is usually not enough for `verified`, but a
partial or failed report is still useful. It helps document the model's limits.

## 8. Updating the database

When a report is accepted, maintainers update `app/printers.json` with:

- `suitability`
- `suitability_note`
- `verified_by`
- `verified_at`
- `evidence_url`
- `verified_firmware`
- `verified_material`

`evidence_url` is optional; only add it when the report includes a link. Only
add fields that are supported by the report. Do not silently turn an `expected`
model into `verified`.

## 9. Privacy

PlateLoop does not need your printer serial number, account credentials, or
remote access. Reports are public GitHub issues. Only submit what you are
comfortable publishing, and credit yourself with a handle or leave the credit
field empty.
