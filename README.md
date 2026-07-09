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

JSON output for logs/diffs:

```bash
uv run sonygeotag scan --json > scan.json
uv run sonygeotag gatt-dump --json > gatt.json
uv run sonygeotag read-values --json > read-values.json
uv run sonygeotag notify-log --duration 60 > notify-log.jsonl
```

These commands are read-only except for the BLE connection/subscription behavior performed by the OS/Bleak while discovering GATT metadata and enabling notifications. Do not add write probes until the packet format is understood.

## Notes

See `docs/a7c2-ble-map.md` for the current observed A7C II BLE map.
