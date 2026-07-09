## Goal

Create an iOS MVP that can use iPhone GPS and Sony A7C II BLE location-sync protocol to geotag newly captured photos, with a simple SwiftUI status screen and a verified protocol encoder.

## Context

Python POC confirmed A7C II accepts Sony DD30/DD31/DD11 flow after pairing and writes sent coordinates into new photo EXIF.

## Architecture

- SwiftUI app shell for status and controls.
- CoreLocation provider for foreground/background GPS updates.
- CoreBluetooth central manager for scanning, pairing-assisted connection, Sony location session setup, and periodic DD11 writes.
- Shared Swift protocol encoder matching the verified Python DD11/CC13 packet format.

## Plan

- [x] Add iOS app source under `ios/SonyGeoTag` with SwiftUI entry point, status view, location provider, BLE manager, and Sony protocol encoder; verified by `find ios/SonyGeoTag -name '*.swift'` and line-count check showing all Swift files below 1000 lines.
- [x] Add iOS permissions/background-mode plist entries for Bluetooth central and location; verified by `plutil -lint ios/SonyGeoTag/SonyGeoTag/Info.plist` and plist content containing CoreBluetooth/CoreLocation usage keys plus `UIBackgroundModes`.
- [x] Add a minimal Xcode project scaffold for opening/building in Xcode; verified by `plutil -lint ios/SonyGeoTag/SonyGeoTag.xcodeproj/project.pbxproj` and source references in the project file.
- [x] Add a protocol smoke test executable source and run it with `swiftc`/`swift` to verify DD11 packet encoding; verified by `SonyProtocol smoke test passed`.
- [x] Update README/docs with iOS MVP status and build/run notes; verified by `README.md` and `ios/SonyGeoTag/README.md` documenting the iOS path, Xcode CLI build commands, and physical-iPhone validation requirement.

## Risks

- Xcode GUI signing and install require a physical iPhone with Developer Mode enabled. Mitigated by verifying simulator/device-SDK target builds with `xcodebuild` and documenting the physical-device validation step.
- iOS background BLE behavior must be validated on a physical iPhone. Accepted as the next hardware validation step for the MVP.

## Completion Checklist

- [x] iOS MVP source and Xcode scaffold are present under `ios/SonyGeoTag`; verified by repository files and `plutil -lint` on the project/plist.
- [x] Sony protocol encoder is covered by a runnable Swift smoke test; verified by `swiftc ios/SonyGeoTag/SonyGeoTag/SonyProtocol.swift ios/SonyGeoTag/SonyGeoTagTests/main.swift -o /tmp/SonyProtocolSmoke && /tmp/SonyProtocolSmoke`.
- [x] Existing Python quality gates still pass; verified by `uv run ruff check src tests`, `uv run ty check src tests`, and `uv run pytest tests` with 17 passing tests.
- [x] iOS build verification is documented; verified by successful `xcodebuild -target SonyGeoTag -sdk iphonesimulator` and `xcodebuild -target SonyGeoTag -sdk iphoneos CODE_SIGNING_ALLOWED=NO` runs plus `ios/SonyGeoTag/README.md`.
