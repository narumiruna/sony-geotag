# Agent Instructions

- If any program source file exceeds 1000 lines, split it immediately.
- Use `justfile` for common project commands:
  - `just --list` to see available recipes.
  - `just check` for the full local Python + iOS verification gate.
  - `just py-check` for Python-only lint/type/test.
  - `just ios-check` for iOS smoke/typecheck/project lint/build checks.
  - `just ios-open` to open the iOS Xcode project.
  - `just ios-console` to launch the installed iOS app on the default USB device and attach console output.
- Prefer targeted `just` recipes over repeating long shell commands.
- Do not run `just location-write` unless the user explicitly asks to write GPS data to the camera.
