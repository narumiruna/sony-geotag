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

JSON output for logs/diffs:

```bash
uv run sonygeotag scan --json > scan.json
uv run sonygeotag gatt-dump --json > gatt.json
```

These commands are read-only except for the BLE connection/subscription behavior performed by the OS/Bleak while discovering GATT metadata. Do not add write probes until the packet format is understood.

## Notes

See `docs/a7c2-ble-map.md` for the current observed A7C II BLE map.
