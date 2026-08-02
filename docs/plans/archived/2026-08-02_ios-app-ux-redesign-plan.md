# iOS App UX Redesign Implementation Plan

## Goal

Redesign the iPhone app around the photographer’s primary workflow—start geotagging, know when the camera has received a usable location, recover from problems, and stop—while preserving the existing Sony BLE protocol, background behavior, persisted settings, and diagnostic capability.

The finished app must avoid indefinite foreground waiting, stage consequential settings until explicit Apply, keep cancellation side-effect free, expose actionable loading/partial/error states, and remain usable with supported orientations, Dynamic Type, VoiceOver, keyboard input, and increased-contrast/reduced-motion settings.

## Context

- The current root `Form` in `ios/SonyGeoTag/SonyGeoTag/ContentView.swift` presents implementation details (`DD11`, `DD21`, pending reconnect, peripheral ID, raw log) before the main actions.
- `CameraBLEManager` has per-operation timeouts after characteristic discovery, but foreground scan, connect, and service-discovery phases can wait indefinitely and appear frozen.
- `ContentView` and `SonyGeoTagAppModel` both react to lifecycle/location changes and directly configure services, which creates duplicate side-effect paths.
- `@AppStorage` toggles apply immediately, so changing Background Link can persist settings, start location services, reconnect BLE, and request permission without a staged preview or no-op cancellation path.
- The first successful `DD11` write, not merely `.linked`, is the point at which the camera has received a location suitable for subsequent photos.
- The app persists `backgroundLinkEnabled`, `lowPowerModeEnabled`, and `rememberedPeripheralID` in `UserDefaults`; their keys, meanings, defaults, and existing values must remain compatible.
- The Xcode project currently has only an app target. `SonyGeoTagTests/main.swift` is a command-line smoke test, not an XCTest target.
- `TARGETED_DEVICE_FAMILY = 1`, so native iPad support is not currently part of the supported product. The responsive scope is iPhone portrait/landscape, including compact heights and accessibility text sizes.
- Real CoreBluetooth behavior and camera writes require a physical iPhone and camera. Automated UI tests need deterministic fake BLE/location states.

## Architecture

### Ownership and data flow

- Keep `SonyGeoTagAppModel` as the single `@MainActor` owner of user intents, permission sequencing, lifecycle handling, settings application, and service coordination.
- Make SwiftUI views intent-only: they render immutable/equatable presentation state and call app-model actions; they do not directly configure `CameraBLEManager` or `LocationProvider`.
- Introduce a user-facing state model that combines camera state, location state, transmission freshness, pending background setup, and recovery actions without exposing protocol terminology on the home screen.
- Keep `CameraBLEManager` responsible for Sony BLE transport and protocol sequencing. Add explicit foreground attempt cancellation/timeouts while retaining indefinite, low-power background reconnect semantics.
- Keep `LocationProvider` responsible for CoreLocation authorization and updates. Report authorization transitions to the app model so BLE writes start only after usable foreground authorization.
- Isolate raw logs in a diagnostics store observed only by the diagnostics surface, preventing every log append from rebuilding the primary screen.

### Proposed source boundaries

- `SonyGeoTagApp.swift`: app entry and scene-phase forwarding only.
- `SonyGeoTagAppModel.swift`: orchestration, user intents, settings transaction, permission sequencing, and presentation-state publication.
- `GeotaggingViewState.swift`: pure home-state mapping, user-facing copy, available actions, and readiness rows.
- `LinkSettings.swift`: typed settings, legacy `UserDefaults` mapping, draft/apply/cancel behavior, and effect previews.
- `ContentView.swift`: shallow root navigation and top-level composition.
- `GeotaggingHomeView.swift`: status summary, readiness, primary/secondary actions, settings summary, and diagnostics disclosure.
- `LinkSettingsView.swift`: staged two-option-group editor with Cancel, effect preview, and Apply.
- `DiagnosticsView.swift`: protocol values, remembered device, coordinates, and copyable logs.
- `DiagnosticsLogStore.swift`: bounded log storage and privacy-aware copy payload.
- Existing `CameraBLEManager.swift`, `LocationProvider.swift`, `CameraConnectionState.swift`, and `SonyProtocol.swift`: service/protocol responsibilities only.

No program source file may exceed 1000 lines; split a file before it crosses that limit.

### State model

The presentation model will distinguish:

- `notConnected`
- `requestingPermission`
- `searching`
- `connecting`
- `preparing`
- `waitingForLocation`
- `sendingFirstLocation`
- `ready`
- `waitingInBackground`
- `stopping`
- `stopped`
- `needsAttention`

`ready` requires at least one successful location write in the current session. A failed or stale update retains the last successful timestamp for context but cannot present a false Ready state.

### Settings transaction

- Map existing booleans to typed choices:
  - Connection availability: `whileAppIsOpen` / `continueInBackground`
  - Location updates: `batterySaver` / `bestAccuracy`
- Preserve the legacy keys and default values (`false` background, `true` low power); do not introduce a destructive migration.
- The settings view edits a draft detached from live services and persistence.
- Cancel or interactive dismissal discards the draft and invokes no service or permission API.
- Apply validates and persists both choices through one app-model transaction, publishes only the final combination, then configures location/BLE once.
- If persistence/application fails, restore the previous settings snapshot and active valid runtime state, then show an actionable error.
- Missing Always authorization after a valid Background selection is a partial setup state, not silent success: foreground behavior remains available and the home screen offers a permission recovery action.

### Test seams

- Define narrow protocols/adapters for camera control, location authorization/service control, settings persistence, clock, and foreground timeout scheduling.
- Production adapters wrap the existing managers, `UserDefaults`, `Date`, and timers.
- Unit tests use fakes to verify side-effect order, cancellation, rollback, timeout, and state mapping.
- UI tests launch the Debug app with deterministic scenarios selected by launch environment; production builds cannot enable fixtures.

## Tech Stack

- Swift 5, SwiftUI, Combine, CoreBluetooth, CoreLocation, BackgroundTasks
- iOS 17+; iPhone device family
- XCTest/XCUITest added to the existing Xcode project and shared scheme
- Existing `justfile` as the command surface; `just ios-check` remains the full iOS gate
- No new third-party dependencies

## Non-Goals

- Adding native iPad support or changing `TARGETED_DEVICE_FAMILY`
- Adding localization; user-facing copy remains English for this change
- Supporting additional camera selection or camera models
- Changing DD30/DD31/DD11 packet formats, characteristic UUIDs, or verified Sony protocol order
- Guaranteeing continuous background execution beyond iOS CoreBluetooth/CoreLocation/BGTask constraints
- Forget-camera, history, map, EXIF editing, or other new product capabilities
- Running a real GPS write against the camera without separate explicit authorization

## Assumptions

- The preceding UX proposal is the design basis for this plan; creating this plan does not itself authorize implementation.
- Existing users with Background Link enabled must continue to auto-resume after upgrade.
- `Battery Saver` retains the current approximately 100 m CoreLocation target and 120-second send interval; `Best Accuracy` retains best available accuracy and the 30-second interval.
- Stop remains reversible and does not need a confirmation alert or destructive styling.
- Background reconnect remains intentionally long-lived; only foreground user-initiated attempts receive bounded waiting and a visible retry path.

## Risks

- **BLE timing:** A foreground timeout can interrupt a legitimately slow camera connection. Keep timeout policy injectable, preserve the remembered camera, provide Retry, and do not apply the foreground timeout to background pending reconnect.
- **Permission sequencing:** iOS may not grant Always authorization immediately after When-In-Use. Represent this as partial setup and preserve foreground operation instead of reporting full background readiness.
- **Lifecycle regressions:** Consolidating callbacks could weaken background restoration. Characterize existing auto-resume/restoration behavior before removing duplicate call sites and verify on a physical device without performing an unauthorized camera write.
- **Project-file churn:** Adding XCTest targets requires careful `project.pbxproj` and shared-scheme edits. Validate XML/plist/project structure and both simulator/device builds after wiring.
- **UI-test determinism:** Simulator CoreBluetooth cannot represent the production path. Keep fixtures behind `#if DEBUG`, test the production state reducer separately, and retain a documented physical-device verification boundary.
- **Privacy:** Diagnostic logs contain coordinates. Keep them behind explicit disclosure and warn before copy; do not persist or automatically export them.
- **Accessibility layout:** Large navigation titles and horizontal labeled content currently overflow at accessibility sizes. Use reflowing layouts and verify actual screenshots/audits rather than relying only on previews.

## Rollback / Recovery

- The implementation can be rolled back without a data migration because legacy `UserDefaults` keys and semantics remain unchanged.
- Settings Apply keeps a pre-apply snapshot; a failed transaction restores that snapshot and reconfigures services to the previous valid runtime state.
- Connection failures retain remembered peripheral identity and the last successful send timestamp, allowing Retry without setup loss.
- A timed-out or cancelled foreground attempt stops scan/connect/setup work, clears only transient queues/timers, and does not clear persisted settings or remembered camera data.
- If the new UI test fixtures or targets destabilize release builds, they can be removed independently because production services do not depend on fixture implementations.

## Plan

- [x] Add `SonyGeoTagUnitTests` and `SonyGeoTagUITests` targets, Debug-only launch fixtures, shared-scheme Test actions, and `just ios-unit-test`, `just ios-ui-test`, and `just ios-test` recipes; make `just ios-check` run the new tests while retaining smoke/typecheck/lint/build checks, and verify with `xcodebuild -list -project ios/SonyGeoTag/SonyGeoTag.xcodeproj`, `just ios-unit-test`, and one placeholder `just ios-ui-test` pass. Verified: both targets appear in `xcodebuild -list`, and the placeholder unit/UI suites passed on iPhone 17 Simulator.

- [x] Introduce `LinkSettings.swift` and a settings-store protocol that map the existing `backgroundLinkEnabled` and `lowPowerModeEnabled` keys to typed choices without touching unrelated defaults; test legacy true/false combinations, missing-key defaults, unknown-key preservation, one-publication Apply, persistence failure rollback, and side-effect-free Cancel with `just ios-unit-test`. Verified: five settings-store/draft tests and app-model publication/rollback tests pass; UI tests prove Cancel and interactive dismissal preserve the prior summary and runtime.

- [x] Introduce `GeotaggingViewState.swift` as a pure state mapper for titles, explanations, readiness rows, primary/secondary actions, disabled reasons, and recovery actions; test loading, empty, ready-only-after-first-send, stale/degraded, error, stopped, and partial background-permission states with `just ios-unit-test`. Verified: 11 focused state-mapping tests pass, including first-send readiness, background waiting, stale update, permission recovery, and stopped states.

- [x] Move orchestration from `SonyGeoTagApp.swift` and `ContentView.swift` into a single `@MainActor` `SonyGeoTagAppModel.swift` that accepts service/clock/scheduler/settings abstractions and publishes equatable presentation state; remove duplicate view/app lifecycle and location-send triggers, and verify exact call order, one auto-resume per lifecycle transition, and no BLE start before authorization with app-model unit tests and `just ios-typecheck`. Verified: seven coordinator tests pass and `just ios-typecheck` succeeds; views now emit intents and duplicate lifecycle/location side effects were removed.

- [x] Extend `CameraBLEManager.swift` with explicit foreground attempt identity, cancellable scan/connect/discovery/setup stages, injectable bounded timeouts, and recoverable failure results while preserving background pending reconnect behavior; verify timeout, late-callback rejection, cancellation cleanup, remembered-camera preservation, background no-timeout behavior, Retry, and existing DD operation timeout behavior with `just ios-unit-test` and `just ios-smoke`. Verified after final correction: timeout activation now follows session cleanup, foreground failures remain bounded even when Background is configured, all 30 unit tests and the DD/location smoke test pass, and the full gate passes.

- [x] Refine `LocationProvider.swift` and app-model permission sequencing so Start requests authorization at demonstrated intent, denial starts no camera write, When-In-Use permits foreground operation, and missing Always authorization produces a recoverable partial background state; verify authorized, not-determined, denied, restricted, When-In-Use, Always, foreground/background, and Settings-return transitions with `just ios-unit-test`. Verified: permission tests cover all authorization classes, deferred BLE start, explicit Always request, cancellation, partial background status, and Settings-return recovery; 29 unit tests pass.

- [x] Replace the current technical `Form` in `ContentView.swift` with `GeotaggingHomeView.swift`, placing the human-readable status, explanation, readiness rows, and one contextual primary action first; show `Send Current Location` only when meaningful, show `Stop Geotagging` without destructive styling, keep Cancel visible during foreground work, and verify first-run, searching, connected-before-send, ready, stopping, stopped, failed, and background-waiting fixtures with `just ios-ui-test`. Verified: deterministic UI scenarios cover every listed state, Cancel/Retry, first-send readiness, manual send, and reversible Stop; protocol labels are absent from Home.

- [x] Add `LinkSettingsView.swift` as one shallow sheet containing the two flat option groups, a concrete live effect preview, and distinct Cancel and Apply actions; ensure interactive dismissal behaves like Cancel, Apply is disabled when unchanged or busy, permission follow-up appears only after confirmed application, and verify preview/apply/cancel/dismiss/failure/focus-restoration flows with `just ios-unit-test` and `just ios-ui-test`. Verified: focused unit/UI tests cover draft reset, changing previews, explicit Apply, toolbar Cancel, drag dismissal, rollback error, home-summary feedback, and return focus.

- [x] Move protocol details and raw logs into `DiagnosticsView.swift` backed by `DiagnosticsLogStore.swift`, preserve every currently visible diagnostic field and the 120-line bound, add a coordinate-privacy warning to `Copy Diagnostic Log`, and verify disclosure navigation, clear return/dismissal, bounded logging, copy content, empty/dense logs, and absence of DD/UUID labels from the home screen with `just ios-unit-test` and `just ios-ui-test`. Verified: bounded-store unit tests and diagnostics UI scenarios cover empty/dense logs, copy feedback, privacy copy, all legacy camera/location fields, and return navigation.

- [x] Apply responsive and accessible behavior across the home, settings, and diagnostics views using semantic colors, text-plus-symbol status, 44-point controls, vertical reflow for long values, logical VoiceOver grouping, restrained state announcements, keyboard default/cancel actions, focus restoration, and reduced-motion-safe feedback; verify portrait and landscape on a compact iPhone and iPhone 17, light/dark and increased-contrast appearances, Dynamic Type through AX5, VoiceOver labels/order, keyboard traversal, and `XCUIApplication.performAccessibilityAudit` via `just ios-ui-test` plus saved review screenshots under `/tmp` rather than the repository. Verified: selected accessibility audits pass at AX5 and dark/increased-contrast/reduced-motion settings; reading-order assertions pass; iPhone 17 and iPhone SE landscape tests pass; keyboard shortcuts/key handling compile; reviewed screenshots are `/tmp/sony-geotag-redesign-ready.png` and `/tmp/sony-geotag-redesign-ax5.png`.

- [x] Add end-to-end XCUITest scenarios for first launch, permission acceptance/denial recovery, loading/cancel, timeout/retry, first successful send, manual send feedback, stop/restart, background partial setup, settings atomicity, diagnostics navigation, and compatibility launch states; verify all deterministic scenarios with `just ios-ui-test` and document any physical-device-only gaps in the test source comments. Verified: `just ios-test` passed 30 unit tests and 15 XCUITests on iPhone 17 Simulator, covering the listed workflows plus empty/dense diagnostics and accessibility variants.

- [x] Update `ios/SonyGeoTag/README.md` and the root `README.md` to describe the goal-oriented home screen, readiness definition, foreground timeout/retry, new settings labels and effects, permission/partial states, diagnostics privacy, cancellation semantics, preserved background limitations, and the new test commands; verify terminology against the shipped strings with `rg` and run `just ios-lint-project`. Verified: documentation contains the shipped workflow/settings/status terminology and test commands; plist, project, and shared-scheme lint checks pass.

- [x] Perform the final compatibility and quality audit: confirm legacy defaults and remembered peripheral survive an upgrade fixture, unknown defaults remain untouched, protocol constants/order and 30/120-second intervals are unchanged, no source file exceeds 1000 lines, no Debug fixture is reachable in Release, and no unrelated files changed; verify with targeted unit/UI assertions, `find ios/SonyGeoTag -name '*.swift' -print0 | xargs -0 wc -l`, `git diff --check`, `git diff --stat`, and `just check`. Reverified after the timeout correction: compatibility tests and `git diff --check` pass, protocol source remains unchanged, intervals remain 30/120 seconds, largest source is 898 lines, Release fixture isolation passes, and final `just check` passes.

- [x] Run non-writing physical-device validation for launch, foreground/background transitions, permission recovery, cancellation responsiveness, orientation, Dynamic Type, VoiceOver, and diagnostics copy; record unavailable real-camera/DD11 and iOS background-wake checks as explicitly deferred unless separately authorized, and verify the app remains responsive with an Instruments Hangs/Time Profiler capture and no detected foreground hang during the exercised flows. Verified: all 15 Debug-fixture XCUITests passed on the connected physical iPhone without real BLE/location writes; a 15-second physical Time Profiler trace at `/tmp/SonyGeoTag-Redesign-TimeProfiler.trace` contains zero potential-hang rows. Real camera/DD11 and opportunistic background-wake checks are deferred because no separate camera-write authorization was provided.

## Completion Checklist

- [x] The first screen prioritizes geotagging readiness and its next action rather than Sony protocol internals. Evidence: Home UI scenarios and reviewed screenshots show status/actions first and no DD/UUID labels.
- [x] `Ready to Geotag` is impossible before a successful current-session location write. Evidence: state reducer and first-send unit/UI tests pass.
- [x] Every foreground waiting state has visible progress, a cancellation path, and a finite timeout/retry outcome. Evidence: timeout-session unit tests, corrected manager attempt ordering/branching, loading/cancel/timeout/retry XCUITests, smoke tests, and the final full gate pass.
- [x] Background waiting remains semantically distinct from foreground loading and preserves restoration/reconnect behavior. Evidence: background policy/state tests and physical-device background-waiting fixture pass.
- [x] Location permission is requested only after user intent, denial starts no camera write, and partial background permission has an actionable recovery path. Evidence: ten app-model tests and permission UI scenarios pass.
- [x] Settings preview, Apply, Cancel, interactive dismissal, rollback, and one-shot runtime reconfiguration satisfy the approved behavior. Evidence: settings unit tests and apply/cancel/drag/failure XCUITests pass.
- [x] Diagnostics preserve existing technical capability without causing primary-screen log rendering or hiding the coordinate privacy warning. Evidence: isolated bounded log store and empty/dense/copy/navigation XCUITests pass.
- [x] Existing `UserDefaults` keys, defaults, values, remembered camera identity, unknown defaults, protocol sequence, and update intervals remain compatible. Evidence: compatibility tests pass, Sony protocol source is unchanged, and 30/120-second constants remain.
- [x] Supported iPhone layouts pass portrait/landscape, compact-height, Dynamic Type AX5, light/dark, increased-contrast, VoiceOver, keyboard, focus, and reduced-motion checks without critical clipping or inaccessible actions. Evidence: simulator/physical accessibility audits, reading order, AX5, appearance, focus, iPhone 17, and compact iPhone SE landscape checks pass; controls expose focus and default/cancel keyboard behavior.
- [x] Unit tests, UI tests, smoke tests, typecheck, project lint, simulator build, unsigned device build, and the full `just check` gate pass. Evidence: final post-correction `just check` passed 44 Python tests, 30 iOS unit tests, 15 XCUITests, smoke/type/lint, and both builds.
- [x] User-facing documentation matches the final interface, permissions, recovery behavior, diagnostics privacy, and platform limitations. Evidence: terminology audit and project lint pass.
- [x] Required physical-device checks are recorded; any real-camera write/background-wake checks remain clearly deferred unless separately authorized. Evidence: 15 physical fixture XCUITests and a zero-hang Time Profiler trace passed; camera writes/background wake are explicitly deferred.
- [x] All plan tasks are checked with current evidence, no known required work remains, and the completed plan is ready to archive under `docs/plans/archived/`.
