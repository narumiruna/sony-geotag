# SonyGeoTag iOS MVP

Minimal SwiftUI iOS app for sending iPhone GPS updates to a Sony Alpha camera over BLE.

## Current scope

- Targets Sony A7C II / `ILCE-7CM2` first.
- Scans BLE advertisements for Sony camera name/manufacturer data.
- Connects with CoreBluetooth.
- Performs the verified modern Sony location flow:
  1. subscribe `DD01`
  2. optional `EE01` vendor pairing init
  3. write `DD30 = 01`
  4. write `DD31 = 01`
  5. read `DD32`, `DD33`, `DD21`
  6. send `DD11` GPS packets periodically to keep the camera's cached GPS state fresh
  7. cleanup with `DD31 = 00`, `DD30 = 00`
- Uses CoreLocation for foreground/background GPS.
- Offers a Background Link toggle and Low Power Mode.
- Shows camera/GPS status and debug logs.

## Open in Xcode

```bash
open ios/SonyGeoTag/SonyGeoTag.xcodeproj
```

Requirements:

- Full Xcode installation, not only Command Line Tools.
- Physical iPhone for real BLE + background-location testing.
- Camera Bluetooth pairing mode for the first connection.

CLI build verification commands:

```bash
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
  xcodebuild -project ios/SonyGeoTag/SonyGeoTag.xcodeproj \
  -target SonyGeoTag \
  -sdk iphonesimulator \
  -configuration Debug build

DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
  xcodebuild -project ios/SonyGeoTag/SonyGeoTag.xcodeproj \
  -target SonyGeoTag \
  -sdk iphoneos \
  -configuration Debug \
  CODE_SIGNING_ALLOWED=NO build
```

Use Xcode GUI with a physical iPhone for signing, installation, and real BLE/background-location validation.

## Background and low power behavior

The camera does not ask the app for GPS right before shutter release. Instead, SonyGeoTag proactively writes `DD11` location packets so the camera can use the most recently cached GPS fix when a new photo is captured.

- High accuracy / foreground shooting: best GPS accuracy and a 30-second DD11 interval.
- Low Power Mode: lower CoreLocation accuracy, significant-location-change monitoring, and a 120-second DD11 interval.
- Background Link: asks for Always Location permission, remembers the last camera peripheral, enables CoreBluetooth restoration, and arms a pending CoreBluetooth reconnect so iOS can wake the app when the remembered camera returns.
- If Background Link only has When-In-Use permission, GPS stays foreground-only so iOS does not need to show the blue background-location indicator.
- Background App Refresh: registers a `BGAppRefreshTask` as a best-effort maintenance wake-up to reschedule and attempt reconnect/location delivery.

Limitations:

- iOS may throttle background scans, timers, and `BGAppRefreshTask` delivery.
- `BGAppRefreshTask` is opportunistic; it is not a continuous daemon and cannot guarantee GPS delivery before shutter release.
- Force-quitting the app can prevent background relaunch.
- Background behavior must be verified on a physical iPhone with the camera.

Useful background reconnect log lines:

```text
Arming pending reconnect to remembered camera ...
Pending reconnect already armed for remembered camera
CoreBluetooth restored state
Connected
DD11 location OK
```

## Local protocol smoke test

```bash
swiftc \
  ios/SonyGeoTag/SonyGeoTag/SonyProtocol.swift \
  ios/SonyGeoTag/SonyGeoTagTests/main.swift \
  -o /tmp/SonyProtocolSmoke
/tmp/SonyProtocolSmoke
```

## iOS permissions

`SonyGeoTag/Info.plist` includes:

- Bluetooth usage descriptions
- When-in-use and always location usage descriptions
- `UIBackgroundModes`: `bluetooth-central`, `location`

## MVP limitations

- No App Store metadata or signing team configured yet.
- No multi-camera UI.
- No persistent paired-camera storage yet.
- Background delivery still needs physical iPhone validation.
- The app sends the latest GPS fix; it does not modify already-captured images.
