# SonyGeoTag for iPhone

SonyGeoTag sends the iPhone’s current location to a supported Sony Alpha camera over Bluetooth so newly captured photos can use the camera’s latest cached GPS fix.

Current verified camera: Sony A7C II / `ILCE-7CM2`.

## Geotagging workflow

The home screen is organized around shooting readiness rather than BLE protocol details:

1. Turn on the camera and make its Bluetooth location link available.
2. Tap **Start Geotagging**.
3. Grant location access when iOS asks. SonyGeoTag does not start the camera write flow before usable permission is available.
4. Follow the visible stages: looking for the camera, connecting, preparing location, and sending the first location.
5. Wait for **Ready to Geotag** before taking photos that need location data.

**Ready to Geotag** appears only after the camera has successfully received at least one location packet in the current session. The Readiness group separately reports the camera, iPhone location, and last successful camera update.

During a foreground connection attempt, **Cancel** remains available. Camera search, connection, and setup use bounded waits; a timeout preserves the remembered camera and offers **Retry** instead of leaving the interface indefinitely busy.

While ready:

- **Send Current Location** requests an immediate refresh.
- **Stop Geotagging** safely closes the location link. It is reversible and does not delete settings or camera identity.

## Link Settings

Open **Link Settings** from the home screen. Changes are staged until **Apply**; **Cancel**, keyboard cancellation, or interactive sheet dismissal leaves persisted settings and running services unchanged.

### Connection Availability

- **While App Is Open** — runs only while SonyGeoTag is open.
- **Continue in Background** — keeps location and remembered-camera reconnect behavior active when iOS permits it. This requires Always Location permission for reliable background updates.

### Location Updates

- **Battery Saver** — targets approximately 100 m location accuracy and sends about every 120 seconds.
- **Best Accuracy** — uses the best available GPS accuracy and sends about every 30 seconds, using more battery.

The Effect Preview describes the concrete permission, accuracy, frequency, and battery consequences before Apply. Both choices are applied together. If application fails, the previous valid settings remain active.

Existing installs keep the same stored behavior: Background defaults off, Battery Saver defaults on, and the remembered CoreBluetooth peripheral remains unchanged.

## Permission and partial states

Location permission is requested after the user taps Start. If access is denied or restricted, SonyGeoTag remains disconnected and offers **Review Location Permission**.

When Continue in Background is selected but only When-In-Use permission is available:

- foreground geotagging continues to work;
- the home screen shows **Background Permission Needed**;
- **Allow Background Location** provides the recovery action;
- the app does not claim that background setup is complete.

Background reconnect is shown as **Waiting for Camera**, not as an endless foreground progress state. iOS can still throttle background scans, timers, location updates, and `BGAppRefreshTask` delivery. Force-quitting the app can prevent background relaunch.

## Diagnostics and privacy

**Diagnostics** is one level below the home screen and preserves the technical information needed for troubleshooting:

- target and raw BLE state;
- packets sent, DD11/DD21 configuration, update interval, and pending reconnect;
- remembered peripheral and last-send time;
- location permission, mode, coordinate, accuracy, and fix time;
- a bounded 120-line debug log.

Diagnostic logs can include recent coordinates. Review the warning and log contents before using **Copy Diagnostic Log** or sharing the result.

## Sony protocol behavior

The app retains the verified modern Sony location flow:

1. subscribe to `DD01` when available;
2. optionally send the `EE01` pairing initialization;
3. write `DD30 = 01` and `DD31 = 01`;
4. read `DD32`, `DD33`, and `DD21` when available;
5. send periodic `DD11` location packets;
6. clean up with `DD31 = 00` and `DD30 = 00`.

`DD21` controls whether SonyGeoTag sends the 95-byte timezone-capable packet or the 91-byte packet. Protocol fields remain in Diagnostics rather than the primary shooting workflow.

## Build and test

Open the shared project:

```bash
just ios-open
```

Run focused checks:

```bash
just ios-smoke
just ios-typecheck
just ios-unit-test
just ios-ui-test
just ios-test
```

Run the complete iOS gate:

```bash
just ios-check
```

The XCTest suite covers settings compatibility and rollback, permission sequencing, foreground timeout policy, readiness mapping, cancellation, retries, loading/partial/error states, settings preview/apply/cancel/dismissal, diagnostics, Dynamic Type, light/dark and increased-contrast appearances, reduced motion, accessibility audits, and portrait/landscape layouts. Debug-only launch fixtures make simulator UI tests deterministic and are unavailable in Release builds.

A physical iPhone is still required to validate real CoreBluetooth behavior, background restoration, and camera writes. Do not perform a real camera GPS write without explicit authorization.

## Platform and accessibility

- Deployment target: iOS 17 or later.
- Supported native device family: iPhone.
- Supported orientations: portrait and landscape.
- Layouts reflow for accessibility text sizes instead of truncating critical status or actions.
- Status uses text and symbols rather than color alone.
- Controls use accessible target sizes, semantic contrast, VoiceOver labels and restrained state announcements, keyboard default/cancel actions, and reduced-motion-safe feedback.

## Known limitations

- A7C II / `ILCE-7CM2` is the only verified camera.
- Background execution is opportunistic and cannot guarantee a fresh location immediately before every shutter release.
- The app updates the camera’s cached location for new photos; it does not modify existing images.
- Real BLE and background wake behavior cannot be fully simulated by XCUITest.
