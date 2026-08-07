# SonyGeoTag

Sony Alpha BLE geotagging tools plus an iOS MVP for keeping a camera's GPS cache updated from phone location data.

Current verified target: Sony A7C II / `ILCE-7CM2`.

## What this repo contains

- **Python CLI (`sonygeotag`)** for BLE discovery, GATT inspection, notification logging, and Sony `DD11` location-packet encoding/sending.
- **Camera GPS Link iOS app (`ios/CameraGPSLink`)** built with SwiftUI, CoreBluetooth, and CoreLocation for foreground/background location linking.
- **Protocol notes** for the observed A7C II BLE services and location flow in `docs/a7c2-ble-map.md`.

## Safety first

Most probe commands are read-only apart from normal BLE connection/subscription behavior.

`camera-info` is stricter: it only scans, connects, discovers services, reads characteristics, and disconnects. It never calls an application-level GATT write or subscribes to notifications. `--pair` may ask the OS to establish BLE security, but does not authorize Sony vendor writes.

`send-location` is a dry run unless `--write` is present. With `--write`, it performs the known Sony `DD30`/`DD31`/`DD11` location flow and writes GPS data to the camera. Do not write arbitrary payloads to the camera.

## Requirements

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) for Python dependency/runtime management
- BLE-capable macOS host for Python probing
- Full Xcode installation for the iOS app
- Physical iPhone for real BLE and background-location validation
- Sony camera in the appropriate Bluetooth/location-link state; first write tests may require camera pairing mode

## Quick start

```bash
uv sync
uv run sonygeotag --help
just --list
```

Scan for the target camera:

```bash
uv run sonygeotag scan --target ILCE-7CM2 --timeout 15
```

Read a decoded, strictly read-only camera snapshot:

```bash
uv run sonygeotag camera-info --target ILCE-7CM2 --pair
uv run sonygeotag camera-info --target ILCE-7CM2 --pair --json
```

Open a live, read-only Textual dashboard (press `q` or `Ctrl+C` to quit):

```bash
uv run sonygeotag monitor --target ILCE-7CM2 --pair
```

Encode a Sony `DD11` GPS packet without touching BLE:

```bash
uv run sonygeotag encode-location --lat 35.681236 --lon 139.767125
```

Dry-run the location flow without writing to the camera:

```bash
uv run sonygeotag send-location --lat 35.681236 --lon 139.767125
```

## Read-only camera information

`camera-info` reads every characteristic currently marked `read` in one BLE session. Known payloads are decoded and grouped into identity, battery, storage, camera status, network, location, and pairing information. State-dependent failures such as Sony `0x90`/`0x9D`, insufficient encryption, and timeouts remain attached to the affected characteristic rather than failing the whole snapshot.

```bash
# Human-readable, sensitive data redacted
uv run sonygeotag camera-info --target ILCE-7CM2 --pair

# Stable schema-v1 JSON, suitable for later iOS parity work
uv run sonygeotag camera-info --target ILCE-7CM2 --pair --json

# Add public raw payloads; unknown/sensitive raw remains redacted
uv run sonygeotag camera-info --target ILCE-7CM2 --pair --json --include-raw

# Explicitly reveal network values, identifiers, and unknown raw payloads
uv run sonygeotag camera-info --target ILCE-7CM2 --pair --json --include-raw --show-sensitive
```

Sensitive values include the CoreBluetooth address, SSID, BSSID, Wi-Fi password, FTP profile names, opaque identifiers, and unknown proprietary payloads. Avoid saving or sharing output produced with `--show-sensitive`. The command never activates Wi-Fi; credentials are only readable if the camera is already in a state that exposes them.

JSON schema v1 contains `schema_version`, `captured_at`, redacted `device` metadata, parsed advertisement fields, a summary, decode-status counts, and one result per readable characteristic. Decode statuses are `decoded`, `partial`, `unknown`, `unavailable`, and `error`; confidence is reported separately as `verified`, `referenced`, `tentative`, or `unknown`.

## Live camera monitor

The `monitor` command keeps one BLE connection open and polls a focused set of readable characteristics. It displays battery, storage, recording/streaming, Wi-Fi, remote-control availability, and location-link state. If the connection drops, it returns to scanning automatically. It never performs an application-level GATT write or notification subscription.

```bash
# Poll every two seconds until q or Ctrl+C
uv run sonygeotag monitor --target ILCE-7CM2 --interval 2 --pair

# Run for 60 seconds, useful for scripted checks
uv run sonygeotag monitor --target ILCE-7CM2 --duration 60 --pair
```

You can also launch it with `just ble-monitor` when `just` is installed.

## BLE probe commands

Dump services and characteristics:

```bash
uv run sonygeotag gatt-dump --target ILCE-7CM2 --timeout 10
uv run sonygeotag list-services --target ILCE-7CM2
```

Read readable characteristics:

```bash
uv run sonygeotag read-values --target ILCE-7CM2
uv run sonygeotag read-values --target ILCE-7CM2 --pair
uv run sonygeotag read-values --target ILCE-7CM2 --characteristic cc06 --json
```

Subscribe to notify characteristics and stream packets as JSONL:

```bash
uv run sonygeotag notify-log --target ILCE-7CM2 --duration 30
uv run sonygeotag notify-log --target ILCE-7CM2 --duration 30 --pair
uv run sonygeotag notify-log --target ILCE-7CM2 --characteristic cc03 --text
```

Save JSON/JSONL logs for diffs:

```bash
uv run sonygeotag scan --json > scan.json
uv run sonygeotag gatt-dump --json > gatt.json
uv run sonygeotag read-values --json > read-values.json
uv run sonygeotag notify-log --duration 60 > notify-log.jsonl
```

## Writing GPS to the camera

Only use this when you intentionally want to update the camera's cached GPS position:

```bash
uv run sonygeotag send-location \
  --target ILCE-7CM2 \
  --lat 35.681236 \
  --lon 139.767125 \
  --write \
  --duration 60 \
  --pair \
  --vendor-pair-init
```

Useful notes:

- The app/CLI proactively writes `DD11`; the camera uses its latest cached GPS fix for newly captured photos.
- `DD21` determines whether to use the 95-byte timezone-capable packet or the 91-byte packet.
- Successful A7C II tests accepted the modern unlock flow and wrote GPS EXIF for newly captured photos.

## iOS app

The SwiftUI/CoreBluetooth/CoreLocation app lives in:

```bash
ios/CameraGPSLink
```

Open with full Xcode:

```bash
open ios/CameraGPSLink/CameraGPSLink.xcodeproj
```

The iPhone interface is organized around the shooting workflow:

- **Start Geotagging**, visible connection stages, foreground cancellation, bounded waits, and actionable Retry.
- **Ready to Geotag** only after the camera receives the first successful location update in the current session.
- A compact Readiness summary for camera, iPhone location, and the last camera update.
- Applied Link Settings for While Open/Background availability and Battery Saver/Best Accuracy updates, with a concrete preview and side-effect-free cancellation.
- Explicit partial-state recovery when Background is selected without Always Location permission.
- A separate Diagnostics screen preserving DD11/DD21, reconnect, remembered-device, location, and bounded debug-log details with a coordinate privacy warning.
- CoreBluetooth restoration/pending reconnect and best-effort Background App Refresh, subject to iOS background limits.

See `ios/CameraGPSLink/README.md` for the complete workflow, settings effects, permission states, diagnostics privacy, testing, and platform limitations.

## Development

Common commands are defined in `justfile`:

```bash
just --list
just py-check
just ios-check
just check
```

Useful iOS commands:

```bash
just ios-open
just ios-smoke
just ios-typecheck
just ios-unit-test
just ios-ui-test
just ios-test
just ios-build-sim
just ios-build-device-nosign
```

## Project layout

```text
src/sonygeotag/          Python CLI and protocol helpers
tests/                   Python tests
docs/                    Protocol notes and implementation plans
ios/CameraGPSLink/          SwiftUI iOS app and smoke test
justfile                 Local command shortcuts
```

## Limitations

- A7C II / `ILCE-7CM2` is the only verified target so far.
- BLE behavior may differ across Sony models and firmware versions.
- iOS background delivery is opportunistic; force-quitting the app can prevent background relaunch.
- The native iOS target currently supports iPhone on iOS 17 or later in portrait and landscape.
- Physical-device testing is required for real BLE, camera writes, and background-location behavior; real camera GPS writes require explicit authorization.
