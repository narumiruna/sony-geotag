# Agent Instructions

- If any program source file exceeds 1000 lines, split it immediately.
- Use `justfile` for common project commands:
  - `just --list` to see available recipes.
  - `just check` for the full local Python verification gate.
  - `just py-check` for Python lint, type checks, and tests.
- Prefer targeted `just` recipes over repeating long shell commands.
- Do not run `just location-write` unless the user explicitly asks to write GPS data to the camera.

## Gotchas

- Symptom: macOS BLE commands launched through SSH fail or hang with CoreBluetooth authorization `notDetermined`.
  Cause: Bluetooth TCC approval is tied to a GUI-launched responsible process.
  Fix: launch the BLE command from local Terminal or a signed local app and approve Bluetooth access before retrying.
