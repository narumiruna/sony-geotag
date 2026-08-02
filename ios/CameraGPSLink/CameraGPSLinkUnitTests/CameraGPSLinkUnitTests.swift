import Combine
import CoreLocation
import XCTest
@testable import CameraGPSLink

final class ForegroundConnectionTimeoutPolicyTests: XCTestCase {
    func testForegroundStagesHaveFiniteTimeouts() {
        let policy = ForegroundConnectionTimeoutPolicy()

        XCTAssertEqual(policy.timeout(for: .scanning), 15)
        XCTAssertEqual(policy.timeout(for: .connecting), 15)
        XCTAssertEqual(policy.timeout(for: .discovering), 15)
        XCTAssertEqual(policy.timeout(for: .preparing), 45)
    }

    func testStageTransitionCancelsOldTimeoutAndIgnoresLateCallback() {
        let scheduler = ManualConnectionTimeoutScheduler()
        var timedOutStages: [ForegroundConnectionStage] = []
        let session = ForegroundConnectionTimeoutSession(
            policy: ForegroundConnectionTimeoutPolicy(),
            scheduler: scheduler.scheduler,
            onTimeout: { timedOutStages.append($0) }
        )

        session.begin()
        session.transition(to: .scanning)
        session.transition(to: .connecting)
        scheduler.tokens[0].fireIgnoringCancellation()
        XCTAssertTrue(timedOutStages.isEmpty)

        scheduler.tokens[1].fireIgnoringCancellation()
        XCTAssertEqual(timedOutStages, [.connecting])
        XCTAssertFalse(session.isActive)
    }

    func testCancelAndBackgroundWithoutBeginNeverTimeout() {
        let scheduler = ManualConnectionTimeoutScheduler()
        var timeoutCount = 0
        let session = ForegroundConnectionTimeoutSession(
            policy: ForegroundConnectionTimeoutPolicy(),
            scheduler: scheduler.scheduler,
            onTimeout: { _ in timeoutCount += 1 }
        )

        session.transition(to: .scanning)
        XCTAssertTrue(scheduler.tokens.isEmpty)

        session.begin()
        session.transition(to: .preparing)
        session.end()
        scheduler.tokens[0].fireIgnoringCancellation()

        XCTAssertEqual(timeoutCount, 0)
        XCTAssertFalse(session.isActive)
    }
}

private final class ManualConnectionTimeoutScheduler {
    final class Token: ConnectionTimeoutCancellable {
        private(set) var isCancelled = false
        let action: () -> Void

        init(action: @escaping () -> Void) {
            self.action = action
        }

        func cancel() { isCancelled = true }
        func fireIgnoringCancellation() { action() }
    }

    var tokens: [Token] = []

    lazy var scheduler = ConnectionTimeoutScheduler { [weak self] _, action in
        let token = Token(action: action)
        self?.tokens.append(token)
        return token
    }
}

final class DiagnosticsLogStoreTests: XCTestCase {
    func testLogIsBoundedAndCopyTextPreservesOrder() {
        let store = DiagnosticsLogStore(capacity: 2)

        store.append("first")
        store.append("second")
        store.append("third")

        XCTAssertEqual(store.lines, ["second", "third"])
        XCTAssertEqual(store.copyText, "second\nthird")
    }
}

final class LinkSettingsTests: XCTestCase {
    private var defaults: UserDefaults!
    private var suiteName: String!

    override func setUp() {
        super.setUp()
        suiteName = "CameraGPSLinkUnitTests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
        defaults.removePersistentDomain(forName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        defaults = nil
        suiteName = nil
        super.tearDown()
    }

    func testMissingLegacyKeysUseCompatibleDefaults() throws {
        let store = UserDefaultsLinkSettingsStore(defaults: defaults)

        XCTAssertEqual(try store.load(), .default)
        XCTAssertEqual(LinkSettings.default.connectionAvailability, .whileAppIsOpen)
        XCTAssertEqual(LinkSettings.default.locationUpdates, .batterySaver)
    }

    func testLegacyBooleanCombinationsMapToTypedSettings() throws {
        let store = UserDefaultsLinkSettingsStore(defaults: defaults)
        defaults.set(true, forKey: LinkSettingsKeys.backgroundLinkEnabled)
        defaults.set(false, forKey: LinkSettingsKeys.lowPowerModeEnabled)

        XCTAssertEqual(
            try store.load(),
            LinkSettings(connectionAvailability: .continueInBackground, locationUpdates: .bestAccuracy)
        )
    }

    func testSavingSettingsPreservesUnknownDefaults() throws {
        let store = UserDefaultsLinkSettingsStore(defaults: defaults)
        defaults.set("keep-me", forKey: "futureSetting")
        defaults.set("AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE", forKey: "rememberedPeripheralID")

        try store.save(LinkSettings(connectionAvailability: .continueInBackground, locationUpdates: .bestAccuracy))

        XCTAssertTrue(defaults.bool(forKey: LinkSettingsKeys.backgroundLinkEnabled))
        XCTAssertFalse(defaults.bool(forKey: LinkSettingsKeys.lowPowerModeEnabled))
        XCTAssertEqual(defaults.string(forKey: "futureSetting"), "keep-me")
        XCTAssertEqual(
            defaults.string(forKey: "rememberedPeripheralID"),
            "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
        )
    }

    func testDraftCancelRestoresOriginalWithoutPersisting() throws {
        let store = FakeSettingsStore(settings: .default)
        var draft = LinkSettingsDraft(current: .default)
        draft.value = LinkSettings(connectionAvailability: .continueInBackground, locationUpdates: .bestAccuracy)

        draft.cancel()

        XCTAssertEqual(draft.value, .default)
        XCTAssertFalse(draft.hasChanges)
        XCTAssertTrue(store.saved.isEmpty)
    }

    func testSettingsExposeConcreteEffectPreview() {
        let settings = LinkSettings(connectionAvailability: .continueInBackground, locationUpdates: .bestAccuracy)

        XCTAssertEqual(settings.summary, "Background · Best Accuracy")
        XCTAssertTrue(settings.effectPreview.contains("30 seconds"))
        XCTAssertTrue(settings.effectPreview.contains("Always Location"))
    }
}

final class GeotaggingViewStateTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 10_000)

    func testIdlePrioritizesStartAction() {
        let state = GeotaggingViewState.make(from: .fixture(cameraState: .idle), now: now)

        XCTAssertEqual(state.phase, .notConnected)
        XCTAssertEqual(state.title, "Not Connected")
        XCTAssertEqual(state.primaryAction, .start)
        XCTAssertEqual(state.primaryActionLabel, "Start Geotagging")
    }

    func testLinkedIsNotReadyUntilFirstPacketSucceeds() {
        let state = GeotaggingViewState.make(
            from: .fixture(cameraState: .linked, packetsSent: 0, hasLocation: true),
            now: now
        )

        XCTAssertEqual(state.phase, .sendingFirstLocation)
        XCTAssertNotEqual(state.title, "Ready to Geotag")
        XCTAssertEqual(state.primaryAction, .stop)
    }

    func testSuccessfulPacketMakesSessionReady() {
        let state = GeotaggingViewState.make(
            from: .fixture(
                cameraState: .linked,
                packetsSent: 1,
                lastSentAt: now.addingTimeInterval(-12),
                hasLocation: true,
                horizontalAccuracy: 8
            ),
            now: now
        )

        XCTAssertEqual(state.phase, .ready)
        XCTAssertEqual(state.title, "Ready to Geotag")
        XCTAssertEqual(state.lastUpdateText, "12 seconds ago")
        XCTAssertEqual(state.readiness.first(where: { $0.title == "iPhone Location" })?.detail, "Ready · ±8 m")
        XCTAssertEqual(state.secondaryAction, .sendNow)
    }

    func testForegroundConnectionOffersCancel() {
        let state = GeotaggingViewState.make(
            from: .fixture(cameraState: .connecting, isForeground: true),
            now: now
        )

        XCTAssertEqual(state.phase, .connecting)
        XCTAssertEqual(state.primaryAction, .cancel)
        XCTAssertTrue(state.showsProgress)
    }

    func testBackgroundReconnectIsWaitingNotInfiniteLoading() {
        let state = GeotaggingViewState.make(
            from: .fixture(
                cameraState: .connecting,
                backgroundEnabled: true,
                isForeground: false,
                pendingReconnectArmed: true
            ),
            now: now
        )

        XCTAssertEqual(state.phase, .waitingInBackground)
        XCTAssertEqual(state.title, "Waiting for Camera")
        XCTAssertFalse(state.showsProgress)
        XCTAssertNil(state.primaryAction)
    }

    func testDeniedPermissionOffersRecoveryWithoutStarting() {
        let state = GeotaggingViewState.make(
            from: .fixture(cameraState: .idle, locationPermission: .denied, transientError: "Location access is off."),
            now: now
        )

        XCTAssertEqual(state.phase, .needsAttention)
        XCTAssertEqual(state.primaryAction, .openSettings)
        XCTAssertEqual(state.primaryActionLabel, "Review Location Permission")
    }

    func testFailureKeepsLastSuccessfulUpdateAndOffersRetry() {
        let state = GeotaggingViewState.make(
            from: .fixture(
                cameraState: .failed,
                packetsSent: 2,
                lastSentAt: now.addingTimeInterval(-90),
                transientError: "Camera connection timed out."
            ),
            now: now
        )

        XCTAssertEqual(state.phase, .needsAttention)
        XCTAssertEqual(state.primaryAction, .retry)
        XCTAssertEqual(state.lastUpdateText, "1 minute ago")
        XCTAssertEqual(state.message, "Camera connection timed out.")
    }

    func testConnectedWithoutLocationWaitsWithoutFalseReadiness() {
        let state = GeotaggingViewState.make(
            from: .fixture(cameraState: .linked, packetsSent: 0, hasLocation: false),
            now: now
        )

        XCTAssertEqual(state.phase, .waitingForLocation)
        XCTAssertEqual(state.primaryAction, .stop)
        XCTAssertFalse(state.showsProgress)
    }

    func testStaleSuccessfulUpdateBecomesActionableDegradedState() {
        let state = GeotaggingViewState.make(
            from: .fixture(
                cameraState: .linked,
                packetsSent: 1,
                lastSentAt: now.addingTimeInterval(-301),
                hasLocation: true
            ),
            now: now
        )

        XCTAssertEqual(state.phase, .needsAttention)
        XCTAssertEqual(state.title, "Location Update Delayed")
        XCTAssertEqual(state.primaryAction, .retry)
    }

    func testStoppedStateOffersStartAgain() {
        let state = GeotaggingViewState.make(from: .fixture(cameraState: .stopped), now: now)

        XCTAssertEqual(state.phase, .stopped)
        XCTAssertEqual(state.primaryAction, .start)
        XCTAssertEqual(state.primaryActionLabel, "Start Geotagging")
    }

    func testBackgroundPermissionIsPartialRatherThanReady() {
        let state = GeotaggingViewState.make(
            from: .fixture(
                cameraState: .linked,
                packetsSent: 1,
                lastSentAt: now,
                locationPermission: .whenInUse,
                hasLocation: true,
                backgroundEnabled: true
            ),
            now: now
        )

        XCTAssertEqual(state.phase, .ready)
        XCTAssertEqual(state.notice, "Background Permission Needed")
        XCTAssertEqual(state.noticeAction, .requestBackgroundPermission)
    }
}

@MainActor
final class CameraGPSLinkAppModelTests: XCTestCase {
    func testStartWaitsForPermissionBeforeStartingBLE() {
        let camera = FakeCameraService()
        let location = FakeLocationService(permission: .notDetermined)
        let model = makeModel(camera: camera, location: location)

        model.startGeotagging()

        XCTAssertEqual(location.whenInUseRequests, 1)
        XCTAssertEqual(camera.foregroundStarts, 0)
        XCTAssertEqual(model.viewState.phase, .requestingPermission)

        location.setPermission(.whenInUse)

        XCTAssertEqual(location.starts, 1)
        XCTAssertEqual(camera.foregroundStarts, 1)
    }

    func testAuthorizedPermissionsStartForegroundServices() {
        for permission in [LocationPermission.whenInUse, .always] {
            let camera = FakeCameraService()
            let location = FakeLocationService(permission: permission)
            let model = makeModel(camera: camera, location: location)

            model.startGeotagging()

            XCTAssertEqual(location.starts, 1, "permission: \(permission)")
            XCTAssertEqual(camera.foregroundStarts, 1, "permission: \(permission)")
        }
    }

    func testDeniedPermissionDoesNotStartBLEAndOffersRecovery() {
        let camera = FakeCameraService()
        let location = FakeLocationService(permission: .denied)
        let model = makeModel(camera: camera, location: location)

        model.startGeotagging()

        XCTAssertEqual(camera.foregroundStarts, 0)
        XCTAssertEqual(model.viewState.primaryAction, .openSettings)
    }

    func testRestrictedPermissionDoesNotStartBLE() {
        let camera = FakeCameraService()
        let location = FakeLocationService(permission: .restricted)
        let model = makeModel(camera: camera, location: location)

        model.startGeotagging()

        XCTAssertEqual(camera.foregroundStarts, 0)
        XCTAssertEqual(model.viewState.primaryAction, .openSettings)
    }

    func testReturningFromSettingsClearsResolvedPermissionError() {
        let camera = FakeCameraService()
        let location = FakeLocationService(permission: .denied)
        let model = makeModel(camera: camera, location: location)
        model.startGeotagging()

        location.setPermission(.whenInUse)

        XCTAssertEqual(model.viewState.phase, .notConnected)
        XCTAssertEqual(model.viewState.primaryAction, .start)
        XCTAssertFalse(model.viewState.message.contains("access is off"))
    }

    func testCancellingPendingStartHasNoLaterBLESideEffect() {
        let camera = FakeCameraService()
        let location = FakeLocationService(permission: .notDetermined)
        let model = makeModel(camera: camera, location: location)

        model.startGeotagging()
        model.cancelCurrentAttempt()
        location.setPermission(.whenInUse)

        XCTAssertEqual(camera.foregroundStarts, 0)
        XCTAssertEqual(camera.cancels, 1)
        XCTAssertEqual(location.stops, 1)
    }

    func testDuplicateScenePhaseDoesNotDuplicateBackgroundResume() {
        let settings = LinkSettings(connectionAvailability: .continueInBackground, locationUpdates: .batterySaver)
        let camera = FakeCameraService()
        let location = FakeLocationService(permission: .always)
        let model = makeModel(camera: camera, location: location, settings: settings)

        model.handleScenePhase(isForeground: true)
        model.handleScenePhase(isForeground: true)

        XCTAssertEqual(camera.backgroundResumes, 1)
        XCTAssertEqual(location.starts, 1)
    }

    func testApplyPublishesAndConfiguresOnlyFinalSettingsOnce() throws {
        let initial = LinkSettings.default
        let updated = LinkSettings(connectionAvailability: .continueInBackground, locationUpdates: .bestAccuracy)
        let store = FakeSettingsStore(settings: initial)
        let camera = FakeCameraService()
        let location = FakeLocationService(permission: .whenInUse)
        let model = makeModel(camera: camera, location: location, settingsStore: store)
        var publications: [LinkSettings] = []
        let token = model.$settings.dropFirst().sink { publications.append($0) }
        let initialCameraConfigurations = camera.configurations.count
        let initialLocationConfigurations = location.configurations.count

        XCTAssertTrue(model.applySettings(updated))

        XCTAssertEqual(publications, [updated])
        XCTAssertEqual(store.saved, [updated])
        XCTAssertEqual(camera.configurations.count, initialCameraConfigurations + 1)
        XCTAssertEqual(location.configurations.count, initialLocationConfigurations + 1)
        XCTAssertEqual(camera.configurations.last, updated)
        XCTAssertEqual(location.configurations.last?.settings, updated)
        withExtendedLifetime(token) {}
    }

    func testApplyFailureKeepsPreviousValidState() {
        let initial = LinkSettings.default
        let updated = LinkSettings(connectionAvailability: .continueInBackground, locationUpdates: .bestAccuracy)
        let store = FakeSettingsStore(settings: initial)
        store.saveError = TestFailure.expected
        let camera = FakeCameraService()
        let location = FakeLocationService(permission: .whenInUse)
        let model = makeModel(camera: camera, location: location, settingsStore: store)
        let initialCameraConfigurations = camera.configurations.count

        XCTAssertFalse(model.applySettings(updated))

        XCTAssertEqual(model.settings, initial)
        XCTAssertEqual(camera.configurations.count, initialCameraConfigurations)
        XCTAssertTrue(model.viewState.message.contains("couldn’t be applied"))
    }

    func testBackgroundPermissionRequestIsExplicit() {
        let camera = FakeCameraService()
        let location = FakeLocationService(permission: .whenInUse)
        let model = makeModel(camera: camera, location: location)

        model.requestBackgroundPermission()

        XCTAssertEqual(location.alwaysRequests, 1)
    }

    private func makeModel(
        camera: FakeCameraService,
        location: FakeLocationService,
        settings: LinkSettings = .default,
        settingsStore: FakeSettingsStore? = nil
    ) -> CameraGPSLinkAppModel {
        CameraGPSLinkAppModel(
            cameraService: camera,
            locationService: location,
            settingsStore: settingsStore ?? FakeSettingsStore(settings: settings),
            diagnosticsStore: DiagnosticsLogStore(),
            now: { Date(timeIntervalSince1970: 10_000) },
            openSettings: {}
        )
    }
}

@MainActor
private final class FakeCameraService: CameraLinkServicing {
    var onChange: (() -> Void)?
    var snapshot = CameraServiceSnapshot.fixture()
    var configurations: [LinkSettings] = []
    var foregroundStarts = 0
    var backgroundResumes = 0
    var cancels = 0
    var stops = 0
    var sends = 0
    var locationProvider: (() -> CLLocation?)?

    func configure(settings: LinkSettings) { configurations.append(settings) }
    func setLocationProvider(_ provider: @escaping () -> CLLocation?) { locationProvider = provider }
    func startForegroundLink() { foregroundStarts += 1 }
    func resumeBackgroundLink() { backgroundResumes += 1 }
    func cancelCurrentAttempt() { cancels += 1 }
    func stopLink() { stops += 1 }
    func sendLocationNow() { sends += 1 }
    func sendLocationIfDue() {}
}

@MainActor
private final class FakeLocationService: LocationServicing {
    var onChange: (() -> Void)?
    var snapshot: LocationServiceSnapshot
    var configurations: [(settings: LinkSettings, isForeground: Bool)] = []
    var whenInUseRequests = 0
    var alwaysRequests = 0
    var starts = 0
    var stops = 0

    init(permission: LocationPermission) {
        snapshot = LocationServiceSnapshot(permission: permission, currentLocation: nil, isUpdating: false, lastError: nil)
    }

    func configure(settings: LinkSettings, isForeground: Bool) {
        configurations.append((settings, isForeground))
    }

    func requestWhenInUseAuthorization() { whenInUseRequests += 1 }
    func requestAlwaysAuthorization() { alwaysRequests += 1 }
    func startUpdating() { starts += 1 }
    func stopUpdating() { stops += 1 }

    func setPermission(_ permission: LocationPermission) {
        snapshot.permission = permission
        onChange?()
    }
}

private final class FakeSettingsStore: LinkSettingsStoring {
    var settings: LinkSettings
    var saved: [LinkSettings] = []
    var saveError: Error?

    init(settings: LinkSettings) {
        self.settings = settings
    }

    func load() throws -> LinkSettings { settings }

    func save(_ settings: LinkSettings) throws {
        if let saveError { throw saveError }
        saved.append(settings)
        self.settings = settings
    }
}

private enum TestFailure: Error {
    case expected
}

private extension CameraServiceSnapshot {
    static func fixture() -> CameraServiceSnapshot {
        CameraServiceSnapshot(
            state: .idle,
            discoveredCameraName: nil,
            targetName: "ILCE-7CM2",
            packetsSent: 0,
            lastSentAt: nil,
            includeTimezone: true,
            dd21ConfigHex: nil,
            lastError: nil,
            pendingReconnectArmed: false,
            rememberedPeripheralID: nil,
            updateInterval: 120
        )
    }
}

private extension GeotaggingSnapshot {
    static func fixture(
        cameraState: CameraConnectionState,
        packetsSent: Int = 0,
        lastSentAt: Date? = nil,
        locationPermission: LocationPermission = .whenInUse,
        hasLocation: Bool = false,
        horizontalAccuracy: CLLocationAccuracy? = nil,
        backgroundEnabled: Bool = false,
        isForeground: Bool = true,
        pendingReconnectArmed: Bool = false,
        transientError: String? = nil
    ) -> GeotaggingSnapshot {
        GeotaggingSnapshot(
            cameraState: cameraState,
            cameraName: nil,
            targetName: "ILCE-7CM2",
            packetsSent: packetsSent,
            lastSentAt: lastSentAt,
            locationPermission: locationPermission,
            hasLocation: hasLocation,
            horizontalAccuracy: horizontalAccuracy,
            backgroundEnabled: backgroundEnabled,
            isForeground: isForeground,
            pendingReconnectArmed: pendingReconnectArmed,
            transientError: transientError
        )
    }
}
