# Python Sony BLE Read-Only Camera Info Plan

## Goal

Add a strict read-only Python snapshot command that discovers and reads every currently readable Sony BLE characteristic, decodes evidence-backed fields, safely represents unknown payloads, and emits human-readable or stable schema-v1 JSON without any application-level GATT writes or notification subscriptions.

## Architecture

- Add pure Sony characteristic decoders and typed snapshot/result models in a dedicated module.
- Add a single-session BLE snapshot orchestrator that only scans, connects, discovers, calls `read_gatt_char`, and disconnects.
- Add `sonygeotag camera-info` as a thin Typer adapter while preserving every existing command grammar and output.
- Treat CoreBluetooth addresses, network values, FTP profile names, opaque identifiers, and unknown payloads as sensitive; redact them by default.
- Keep malformed, state-dependent, and per-characteristic failures local to each result.

## Non-Goals

- No GATT writes, Wi-Fi activation, notification subscription, remote control, GPS write, image transfer, PTP/IP, iOS changes, or semantic guesses for unknown payloads.
- No new third-party dependency.

## Plan

- [x] Add sanitized A7C II fixtures and failing decoder, redaction, orchestration, and CLI tests; verified the focused run failed at collection because the planned `sony_info` and `camera_snapshot` modules did not yet exist.
- [x] Implement typed schema-v1 models, pure decoder registry, confidence/sensitivity metadata, robust framing/TLV parsing, and safe serialization; verified by 19 focused decoder/redaction tests plus Ruff and ty.
- [x] Implement one-session strict read-only snapshot orchestration with isolated per-characteristic errors; verified by fake-client tests whose write/notify methods fail immediately and whose operation log contains only connect/read/disconnect.
- [x] Add the `camera-info` Typer command and text/JSON rendering with documented exit codes and sensitive/raw flags; verified by 5 `CliRunner` grammar/rendering/redaction/exit-code tests.
- [x] Update `README.md`, `docs/a7c2-ble-map.md`, and `justfile` with the strict read-only boundary, decoder coverage, schema/redaction rules, and `just ble-info`; verified by `just --list`, CLI help, and documentation review.
- [x] Run focused tests, `just py-check`, a redacted live `camera-info` snapshot, and `just check`; verified 44 tests, both iOS builds, a 45-characteristic physical A7C II snapshot, schema-v1 redaction assertions, and static/fake-client operation audits. `just location-write` was never run.

## Risks

- Sony payload layouts vary by model, firmware, and camera state. Decoders must downgrade safely to partial/unknown and retain numeric unknown tags.
- Some readable UUIDs expose network secrets or persistent identifiers. Default output must omit both decoded values and raw bytes for sensitive/unknown entries.
- Battery/media documentation may be incomplete. Only validated fields should be labeled; uncertain fields remain partial with warnings and confidence metadata.

## Completion Checklist

- [x] Existing command grammar/output remains unchanged and all existing tests pass.
- [x] `camera-info` performs no `write_gatt_char`, `start_notify`, or `stop_notify` call.
- [x] Schema-v1 JSON, human output, redaction, raw opt-in, and exit codes are tested.
- [x] Known A7C II model, firmware, status, media, battery, and location payloads decode without inventing unknown semantics.
- [x] Full local verification and redacted physical-camera verification pass.
