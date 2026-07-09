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
```

Use `--json` when saving data for diffs.

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

## Reverse-engineering gates

1. Capture read-only GATT dumps in several camera states:
   - normal powered on
   - Bluetooth pairing screen
   - Creators' App connected
   - location linkage enabled/disabled
2. Subscribe to notify characteristics and operate the camera manually.
3. Capture official Creators' App traffic with Android Bluetooth HCI snoop or an external sniffer.
4. Only after identifying the location-link characteristic and payload format, add write probes.

## Safety rule

Until the packet format is known, probe code should not write arbitrary payloads to the camera.
