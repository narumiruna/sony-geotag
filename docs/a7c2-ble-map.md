# Sony A7C II BLE map

Target camera:

- Model/name observed over BLE: `ILCE-7CM2`
- macOS CoreBluetooth UUID observed: `FDEB1973-4261-02AF-B843-5027972A709B`
- Advertisement local name: `ILCE-7CM2`
- Advertisement service UUIDs: `00001800-0000-1000-8000-00805f9b34fb`
- Manufacturer company ID: `0x012d` (Sony)
- Example manufacturer payload: `03 00 65 00 55 31 22 ff c0 23 b7 ac 21 60 00 00 00 00 00 00`

> The address above is a macOS CoreBluetooth identifier, not necessarily a stable public Bluetooth MAC address.

## Probe commands

```bash
uv run sonygeotag scan --target ILCE-7CM2 --timeout 15
uv run sonygeotag gatt-dump --target ILCE-7CM2 --timeout 10
uv run sonygeotag read-values --target ILCE-7CM2 --pair --json > logs/read-values.json
uv run sonygeotag notify-log --target ILCE-7CM2 --duration 60 --pair > logs/notify-log.jsonl
uv run sonygeotag camera-info --target ILCE-7CM2 --pair
uv run sonygeotag camera-info --target ILCE-7CM2 --pair --json > camera-info.json
```

Use `--json` when saving scan/GATT/read data for diffs. `notify-log` streams JSONL by default so each notification packet is one line. If reads/subscriptions fail with insufficient authentication/encryption, retry with `--pair` and accept the camera pairing prompt.

## Strict read-only camera snapshot

`camera-info` uses one BLE session and is limited to scan/connect, service discovery, `read_gatt_char`, and disconnect. It does not call `write_gatt_char`, `start_notify`, or `stop_notify`; it never activates camera Wi-Fi or writes location, remote-control, pairing-init, or other Sony commands.

The schema-v1 JSON result keeps decoding status separate from confidence:

- status: `decoded`, `partial`, `unknown`, `unavailable`, or `error`
- confidence: `verified`, `referenced`, `tentative`, or `unknown`
- state-dependent GATT errors such as `0x90`, `0x9D`, insufficient encryption, and timeout are per-characteristic `unavailable` results
- unknown payloads remain uninterpreted and are treated as sensitive

CoreBluetooth addresses, SSID/BSSID/password, FTP profile names, opaque identifiers, and unknown payloads are redacted by default. `--include-raw` adds public raw bytes; unknown or sensitive raw bytes require both `--include-raw --show-sensitive`. Do not publish or commit unredacted captures.

Current decoder coverage:

| UUID | Information | Confidence | Notes |
| --- | --- | --- | --- |
| `CC0A`, `CC0B`, `CC11` | firmware and camera model | verified | A7C II fixtures decode as firmware `2.01`, model `ILCE-7CM2` |
| `CC03` | push-transfer readiness | referenced | current state only; no notification subscription |
| `CC09` | Wi-Fi, transfer, remote, time, streaming, and recording states | referenced | unknown and duplicate TLV tags are retained numerically |
| `CC0F` | primary media status and remaining capacity | tentative | only the evidence-backed primary prefix is labeled; trailing bytes remain partial |
| `CC10` | battery packs, percentage, level code, and external-power state | referenced | truncated/unknown pack layouts remain partial |
| `CC06`, `CC07`, `CC0C` | SSID, password, BSSID | referenced | sensitive and state-dependent; the command never starts Wi-Fi |
| `CC40` | FTP profile names | referenced | sensitive |
| `CCAB` | Wi-Fi band/status code | referenced | code retained numerically until its A7C II mapping is verified |
| `DD21`, `DD30`-`DD33` | location packet capability and session flags | verified | read-only; `DD11` is never written |
| `EE02`, `EE04`, other `BBxx`/`CCxx` | framing/opaque vendor data | unknown | no semantic guess; sensitive raw opt-in only |

## Observed primary services

```text
8000ff00-ff00-ffff-ffff-ffffffffffff
8000cc00-cc00-ffff-ffff-ffffffffffff
8000dd00-dd00-ffff-ffff-ffffffffffff
8000ee00-ee00-ffff-ffff-ffffffffffff
8000bb00-bb00-ffff-ffff-ffffffffffff
```

## Candidate characteristics to investigate first

These are interesting because they form simple write/notify pairs or belong to smaller services:

```text
8000ff00...
  ff01 write
  ff02 notify

8000bb00...
  bb01 write  -> bb02 notify
  bb03 write  -> bb04 notify
  bb05 write  -> bb06 notify
  bb08 notify
  bb21-bb27 read

8000dd00...
  dd01 notify
  dd11 write
  dd21 read
  dd30-dd33 write/read

8000ee00...
  ee01 write
  ee02 write/read
  ee03 notify
  ee04 read

8000cc00...
  cc02 write  -> cc03 read/notify
  cc08 write  -> cc09 read/notify
  cc0f/cc10/cc0e notify/read variants
  cc11/cc12 write/read
  many cc23-cc34 write-only controls
```

## Protocol notes from existing implementations

Useful external references found during probing:

- `rock3r/CameraSync` documents the modern Sony Creators' App BLE flow.
- `anoulis/sony_camera_bluetooth_external_gps` documents the older DD11 GPS payload and MTU 158 behavior.
- `Saschl/Alpha-GPS` confirms an Android/iOS geotag app is feasible.

Current working assumptions for A7C II:

- Advertisement manufacturer payload starts with little-endian device type `03 00` = Sony camera.
- Advertisement byte 2 is protocol version; observed `0x65` / `101`.
- Protocol version `>= 65` requires modern location unlock flow.
- GPS/location write characteristic is `0000dd11-0000-1000-8000-00805f9b34fb`.
- Modern location flow:
  1. subscribe `DD01`
  2. write `01` to `DD30` lock
  3. write `01` to `DD31` enable
  4. read `DD32`, `DD33`, `DD21`
  5. write `DD11` GPS packet every ~30 seconds
  6. cleanup: write `00` to `DD31`, then `00` to `DD30`
- `DD21` byte index 4 bit `0x02` indicates 95-byte DD11 packet with timezone/DST data.
- If timezone is unsupported, use 91-byte DD11 packet.
- DD11 location packet uses UTC date/time. CC13 time-area packet uses local date/time.

Implemented local commands:

```bash
uv run sonygeotag encode-location --lat 35.681236 --lon 139.767125
uv run sonygeotag send-location --lat 35.681236 --lon 139.767125
uv run sonygeotag send-location --lat 35.681236 --lon 139.767125 --write --duration 60
```

`send-location` is dry-run unless `--write` is present.

## Live observations

- `scan` finds `ILCE-7CM2` reliably with Sony manufacturer data.
- `gatt-dump` found 5 Sony vendor services and 107 characteristics.
- `read-values` found 45 readable characteristics. Without completed pairing/bonding they failed with insufficient authentication/encryption or timeout; after pairing, a representative snapshot returned 33 values and 12 state-dependent `0x90`/`0x9D` errors.
- `camera-info` decodes known values while preserving all 45 readable results and redacting sensitive/unknown payloads by default.
- `notify-log` can subscribe to all 20 notify characteristics without Sony application-data writes, though BLE notification subscription configures CCCDs. No notifications were emitted during idle/manual camera operation, so location sync likely requires the DD30/DD31/DD11 flow.
- `send-location --write --pair --vendor-pair-init` succeeded after putting the camera in Bluetooth pairing mode.
- Successful A7C II write details:
  - advertisement protocol version: `0x65` / `101`, unlock required
  - `EE01` pairing init payload accepted: `06 08 01 00 00 00 00`
  - `DD30=01`, `DD31=01` accepted
  - `DD32` read: `01`
  - `DD33` read: `01`
  - `DD21` read: `06 10 00 9c 02 00 00`; byte 4 has `0x02`, so A7C II uses the 95-byte timezone-capable DD11 packet
  - Two 95-byte `DD11` packets were accepted
  - Test photo EXIF showed the sent Eiffel Tower coordinate, confirming camera-side geotag write for newly captured photos.

## Reverse-engineering gates

1. Capture read-only GATT dumps in several camera states:
   - normal powered on
   - Bluetooth pairing screen
   - Creators' App connected
   - location linkage enabled/disabled
2. Capture read values with `read-values` in the same states and diff the JSON output.
3. Subscribe to notify characteristics with `notify-log` and operate the camera manually.
4. ✅ Test `send-location --write` with explicit coordinates while the camera Location Info Link setting is enabled.
5. ✅ Confirm success by taking a photo and checking GPS EXIF.
6. Next: turn the working Python POC into the iOS CoreBluetooth/CoreLocation implementation.

## Safety rule

`camera-info` must remain strict read-only: no application-level GATT writes and no notification subscriptions. Do not write arbitrary payloads to the camera. Only use documented Sony payloads, and keep location writes explicit via `--write`.
