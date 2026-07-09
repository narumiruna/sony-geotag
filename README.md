# sonygeotag

Sony Alpha BLE geotag protocol probe tools.

Current focus: reverse engineer enough of the Sony A7C II (`ILCE-7CM2`) BLE protocol to send phone GPS/location-link data later from an iOS app.

## Commands

Scan BLE advertisements:

```bash
uv run sonygeotag scan --target ILCE-7CM2 --timeout 15
```

Dump GATT services/characteristics:

```bash
uv run sonygeotag gatt-dump --target ILCE-7CM2 --timeout 10
# alias:
uv run sonygeotag list-services --target ILCE-7CM2
```

Read all readable characteristics:

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

Encode a Sony `DD11` GPS packet without touching BLE:

```bash
uv run sonygeotag encode-location --lat 35.681236 --lon 139.767125
```

Send GPS to the camera. This is dry-run unless `--write` is present:

```bash
uv run sonygeotag send-location --lat 35.681236 --lon 139.767125
uv run sonygeotag send-location --lat 35.681236 --lon 139.767125 --write --duration 60
```

JSON output for logs/diffs:

```bash
uv run sonygeotag scan --json > scan.json
uv run sonygeotag gatt-dump --json > gatt.json
uv run sonygeotag read-values --json > read-values.json
uv run sonygeotag notify-log --duration 60 > notify-log.jsonl
```

Most probe commands are read-only except for BLE connection/subscription behavior. `send-location --write` performs the known Sony DD30/DD31/DD11 location flow and writes GPS data to the camera.

## Developer shortcuts

Common Python, BLE, and iOS commands are available in `justfile`:

```bash
just --list
just check
just ios-open
just ios-console
```

## iOS MVP

The first SwiftUI/CoreBluetooth/CoreLocation app scaffold lives in:

```bash
ios/SonyGeoTag
```

Open with full Xcode:

```bash
open ios/SonyGeoTag/SonyGeoTag.xcodeproj
```

CLI target builds are verified with `/Applications/Xcode.app`; use Xcode GUI with a physical iPhone for signing, installation, BLE, and background-location validation.

## Notes

See `docs/a7c2-ble-map.md` for the current observed A7C II BLE map.
