# SonyGeoTag

Sony Alpha BLE geotagging tools plus an iOS MVP for keeping a camera's GPS cache updated from phone location data.

Current verified target: Sony A7C II / `ILCE-7CM2`.

## What this repo contains

- **Python CLI (`sonygeotag`)** for BLE discovery, GATT inspection, notification logging, and Sony `DD11` location-packet encoding/sending.
- **iOS app (`ios/SonyGeoTag`)** built with SwiftUI, CoreBluetooth, and CoreLocation for foreground/background location link testing.
- **Protocol notes** for the observed A7C II BLE services and location flow in `docs/a7c2-ble-map.md`.

## Safety first

Most probe commands are read-only apart from normal BLE connection/subscription behavior.

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

Encode a Sony `DD11` GPS packet without touching BLE:

```bash
uv run sonygeotag encode-location --lat 35.681236 --lon 139.767125
```

Dry-run the location flow without writing to the camera:

```bash
uv run sonygeotag send-location --lat 35.681236 --lon 139.767125
```

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
ios/SonyGeoTag
```

Open with full Xcode:

```bash
open ios/SonyGeoTag/SonyGeoTag.xcodeproj
```

Current app capabilities:

- Camera scan/connect and Sony location-link setup.
- Foreground high-accuracy mode.
- Low Power Mode using lower CoreLocation accuracy and less frequent `DD11` writes.
- Background Link with Always Location permission, CoreBluetooth restoration/pending reconnect, and best-effort Background App Refresh.
- Hidden iOS background-location blue indicator where the OS permits it; When-In-Use permission stays foreground-only.

See `ios/SonyGeoTag/README.md` for iOS-specific build, behavior, and limitation notes.

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
just ios-build-sim
just ios-build-device-nosign
```

## Project layout

```text
src/sonygeotag/          Python CLI and protocol helpers
tests/                   Python tests
docs/                    Protocol notes and implementation plans
ios/SonyGeoTag/          SwiftUI iOS app and smoke test
justfile                 Local command shortcuts
```

## Limitations

- A7C II / `ILCE-7CM2` is the only verified target so far.
- BLE behavior may differ across Sony models and firmware versions.
- iOS background delivery is opportunistic; force-quitting the app can prevent background relaunch.
- Physical-device testing is required for real BLE, camera writes, and background-location behavior.
