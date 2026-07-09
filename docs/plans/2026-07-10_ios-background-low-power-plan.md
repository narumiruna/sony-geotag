## Goal

Add an iOS MVP mode that can keep Sony location-link useful while the app is backgrounded, with explicit low-power behavior and clear user-visible limitations.

Success means a user can enable background linking in the app, grant Always Location permission, connect once to the camera, then let the app continue/recover BLE location sync in background as iOS permits while using lower GPS/BLE duty cycle than foreground linking.

## Context

The app already declares `bluetooth-central` and `location` background modes and can write DD11 successfully while foregrounded. iOS does not allow arbitrary always-on background work; background BLE depends on CoreBluetooth restoration, pending connect/scan operations, and system scheduling. Sony A7C II advertisements observed so far include only Generic Access service `1800`, so background discovery may be slower or constrained.

## Architecture

- Add persistent app preferences for background linking and low-power mode.
- Initialize `CBCentralManager` with a restoration identifier and handle `willRestoreState`.
- Remember the last camera peripheral UUID and try direct reconnect before scanning.
- Add background-aware BLE behavior: continue active link, reconnect/scan when enabled, avoid duplicate scans, and increase send interval in low-power mode.
- Add low-power location behavior: significant-change monitoring when background/low-power, standard GPS only when actively linking or foreground.
- Surface permission and background-mode requirements in UI/logs.

## Non-Goals

- No guarantee that iOS will relaunch the app from a force-quit state.
- No App Store/background policy polish beyond MVP clarity.
- No multi-camera pairing database yet.

## Plan

- [x] Add `BackgroundLinkSettings` storage for background-link and low-power preferences; verified by Swift typecheck and UI toggles backed by `@AppStorage`.
- [x] Update `LocationProvider` with configurable low-power/active modes using significant-location changes plus standard updates only when needed; verified by `just check` and UI status showing active vs low-power mode.
- [x] Update `CameraBLEManager` with CoreBluetooth restoration identifier, `willRestoreState`, last-peripheral persistence, reconnect-first behavior, pending reconnect arming, retry scheduling, and low-power send interval; verified with `just check`.
- [x] Add best-effort `BGAppRefreshTask` registration and Info.plist permissions for opportunistic background maintenance; verified with `just ios-check`.
- [x] Update SwiftUI controls to enable background linking, low-power mode, Always Location guidance, and clear background limitations; verified with `just check`.
- [x] Update docs for background behavior and validation limitations; verified by `README.md` and `ios/SonyGeoTag/README.md` diff.
- [ ] Validate background reconnect/location delivery on a physical iPhone with the A7C II; verify by locking/backgrounding the app, reconnecting the camera, seeing `DD11 location OK`, and confirming GPS EXIF on a new photo.

## Risks

- iOS may suspend scans or not relaunch if the user force-quits the app; document this as an OS limit.
- Background scanning may not rediscover Sony cameras reliably because the camera may not advertise the DD service UUID; direct reconnect to remembered peripheral mitigates this after first successful link.
- Continuous GPS + BLE can drain battery; low-power defaults should use longer intervals and significant-change monitoring.

## Completion Checklist

- [x] Background/low-power preferences are implemented and visible in `ios/SonyGeoTag/SonyGeoTag/ContentView.swift`, verified by `just check`.
- [x] CoreBluetooth state restoration/reconnect support, including pending reconnect arming, is implemented in `CameraBLEManager.swift`, verified by `just check`.
- [x] Best-effort Background App Refresh support is implemented in `SonyGeoTagApp.swift` and `Info.plist`, verified by `just ios-check`.
- [x] Low-power location behavior is implemented in `LocationProvider.swift`, verified by `just check`.
- [x] Background behavior limitations and validation steps are documented, verified by README changes.
- [x] Full local verification passes with `just check`.
- [ ] Physical background behavior is verified on iPhone + A7C II by user acceptance/log evidence.
