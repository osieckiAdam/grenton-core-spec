# Changelog

## Spec 0.4.4 — 2026-09-30 (suite unchanged, 0.4.3)

- The spec text states rules, not a verification log.
  - Dates, runs, counts and "verified live" notes are removed from §5.3.2, §5.4.2, §6.2, §6.3.12, §6.6 and §10. Their evidence lives in this changelog and in commit messages.
  - Short rationales stay where a rule would otherwise look arbitrary.
- The open thermostat questions (modes 1/2, writing Mode directly) move to §10 as known limitations.

## 0.4.3 — 2026-09-30

- Removed identifiers of the installation used for live testing from the spec text and fixtures. That covers CLU names, object names, labels and counts. The evidence stays, described generically. Fixture labels and object names are now synthetic.

## 0.4.2 — 2026-09-30

- **§5.4.2: subscription chunks are registered one at a time, not in parallel (Δ vs the reference).**
  - With parallel chunks, the two CLUs needing 4–5 chunks timed out on some of them in every live run, while single-chunk CLUs never did.
  - With sequential chunks there were no timeouts in three consecutive runs.
- No case changes. The session profile that would test this does not exist yet.

## 0.4.1 — 2026-09-30

- **Fix, §6.2 `boolFromOnOff`:** the number 1 (`int` 1, `double` 1.0) now means on.
  - The reference's `value == True` is also true for 1 and 1.0 in Python.
  - The spec had translated it as "boolean true only", so every relay reporting `1` read as off.
  - Found live on a switched-on light.
- Corrected cases `int-one` and `double-one`. New cases `int-zero`, `int-two`, `double-one-and-a-half` and `string-one`.
- `tools/suite.py check` now re-derives both coercions with the reference's own Python expressions. The wrong cases had been derived from the spec text alone, with no oracle to catch them.

## 0.4.0 — 2026-09-30

- New §6.6, physical objects. Devices stay per widget, and Core also groups them by the CLU object each entity operates.
  - The key is `{cluId, kind: object|variable|script, name, index}`. The feature index keeps the channels of one LED module apart.
  - Every object lists the widgets that show it, each with its name, page and entities.
  - Motivation: in a live installation every thermostat and most shutters appear on two pages under different names.
- Devices carry `page` (`{index, name}`).
- The mapping projection gains `physicalObjects` and `devices[].page`. All mapping cases are re-derived.
- New case `shared-objects`.

## 0.3.0 — 2026-09-30

- New widget types, modelled on a live Object Manager export:
  - `THERMOSTAT_V2` → `thermostat` device (§6.3.12). Feature semantics follow Grenton's Thermostat manual.
  - `SCENE_DOUBLE` → `scene` device. Entities are suffixed by list position because both components carry `rowId` 0.
- `HEADER` is a known layout widget: it produces no device and is not reported as skipped. This is a behaviour change from 0.2.0.
- `EVENT_SCHEDULER` is documented as known but unsupported.
- §3.3 documents the `$value$` placeholder in exported set actions.
- New mapping case `thermostat-scene-double-header`. The `widget-tolerance` fixture uses `FUTURE_WIDGET_V9` as its unknown type.

## 0.2.0 — 2026-09-29

- New conformance profile `mapping` with 4 cases on 4 interface fixtures:
  - every widget type
  - completeness rules
  - widget-level tolerance
  - root-level rejection
- Expected values come from `tools/oracles/mapping.py`, written independently from the spec.
- Spec §6.1 now defines, as normative:
  - device fields `id`, `widgetType`, `kind`, `name`
  - the device-kind table
  - the name rule
  - device and entity order
- Spec §3.6: skipped widgets must be observable as per-page type lists, and a root-level error rejects the whole interface.
- Spec §6.3.1: when there are several ON/OFF actions, the first one wins.
- Spec §6.3.9: the first component must have an action.
- Manifest lists fixtures with SHA-256.

## 0.1.0 — 2026-09-28

- Specification moved here from `grenton-core-swift`.
- Spec clarifications, all matching the reference implementation:
  - §3.1: every leading and trailing quote is stripped.
  - §4.1: the PIN whitespace set and path-segment encoding are defined, and a URL-building failure maps to `provisioning.connection`.
  - §5.3.5: the example exchange uses sid 0.
- Conformance suite 0.1.0, `stateless` profile, 144 cases:
  - AES-CBC
  - frames
  - register, action and report payloads
  - value casting
  - Object Manager URL and PIN
  - coercions
  - number formatting
