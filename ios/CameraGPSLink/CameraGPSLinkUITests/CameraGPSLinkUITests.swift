import XCTest

final class CameraGPSLinkUITests: XCTestCase {
    private var app: XCUIApplication!

    override func tearDown() {
        app?.terminate()
        XCUIDevice.shared.orientation = .portrait
        app = nil
        super.tearDown()
    }

    func testNotConnectedPrioritizesStartAndHidesProtocolDetails() {
        launch("not-connected")

        XCTAssertTrue(app.navigationBars["Camera GPS Link"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Not Connected"].exists)
        XCTAssertTrue(app.buttons["Start Geotagging"].exists)
        XCTAssertTrue(app.staticTexts["Camera"].exists)
        XCTAssertTrue(app.staticTexts["iPhone Location"].exists)
        XCTAssertFalse(app.staticTexts["DD11 timezone"].exists)
        XCTAssertFalse(app.staticTexts["Pending reconnect"].exists)
    }

    func testLoadingCanCancelAndRetryAfterTimeout() {
        launch("searching")
        XCTAssertTrue(app.staticTexts["Looking for Camera…"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.descendants(matching: .any)["connection-progress"].exists)

        app.buttons["Cancel"].tap()
        XCTAssertTrue(app.staticTexts["Stopped"].waitForExistence(timeout: 2))
        XCTAssertTrue(app.buttons["Start Geotagging"].exists)

        app.terminate()
        launch("timeout")
        XCTAssertTrue(app.staticTexts["Connection Needs Attention"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS 'timed out'" )).firstMatch.exists)
        app.buttons["Retry"].tap()
        XCTAssertTrue(app.staticTexts["Looking for Camera…"].waitForExistence(timeout: 2))
    }

    func testFirstRunDefersThenStartsAfterPermissionIntent() {
        launch("first-run")
        XCTAssertTrue(app.buttons["Start Geotagging"].waitForExistence(timeout: 5))

        app.buttons["Start Geotagging"].tap()

        XCTAssertTrue(app.staticTexts["Looking for Camera…"].waitForExistence(timeout: 2))
        XCTAssertTrue(app.buttons["Cancel"].exists)
    }

    func testIntermediateAndTerminalFixturesExposeClearState() {
        let fixtures: [(String, String)] = [
            ("connecting", "Connecting…"),
            ("connected-before-send", "Sending First Location…"),
            ("waiting-for-location", "Waiting for iPhone Location"),
            ("stopping", "Stopping…"),
            ("stopped", "Stopped"),
        ]

        for (scenario, title) in fixtures {
            launch(scenario)
            XCTAssertTrue(app.staticTexts[title].waitForExistence(timeout: 5), "scenario: \(scenario)")
            app.terminate()
        }
    }

    func testReadySupportsManualSendAndReversibleStop() {
        launch("ready")

        XCTAssertTrue(app.staticTexts["Ready to Geotag"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Send Current Location"].exists)
        XCTAssertTrue(app.buttons["Stop Geotagging"].exists)
        XCTAssertFalse(app.buttons["Stop Geotagging"].isSelected)

        app.buttons["Send Current Location"].tap()
        XCTAssertTrue(app.staticTexts["Just now"].waitForExistence(timeout: 2))

        app.buttons["Stop Geotagging"].tap()
        XCTAssertTrue(app.staticTexts["Stopped"].waitForExistence(timeout: 2))
        XCTAssertTrue(app.buttons["Start Geotagging"].exists)
    }

    func testBackgroundWaitingAndPartialPermissionAreDistinct() {
        launch("background-waiting")
        XCTAssertTrue(app.staticTexts["Waiting for Camera"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.descendants(matching: .any)["connection-progress"].exists)
        XCTAssertFalse(app.buttons["Cancel"].exists)

        app.terminate()
        launch("background-partial")
        XCTAssertTrue(app.staticTexts["Ready to Geotag"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Background Permission Needed"].exists)
        XCTAssertTrue(app.buttons["Allow Background Location"].exists)
        app.buttons["Allow Background Location"].tap()
        waitForDisappearance(app.staticTexts["Background Permission Needed"])
    }

    func testPermissionDeniedShowsActionableRecovery() {
        launch("permission-denied")

        XCTAssertTrue(app.staticTexts["Location Access Needed"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Review Location Permission"].exists)
        XCTAssertFalse(app.buttons["Start Geotagging"].exists)
    }

    func testSettingsCancelHasNoSideEffectsAndApplyUpdatesSummary() {
        launch("not-connected")
        openLinkSettings()
        selectSetting("Continue in Background")
        selectSetting("Best Accuracy")
        XCTAssertTrue(app.staticTexts["Background · Best Accuracy"].exists)

        app.buttons["settings-cancel"].tap()
        XCTAssertTrue(app.staticTexts["While Open · Battery Saver"].waitForExistence(timeout: 2))
        XCTAssertTrue(app.buttons["link-settings"].isHittable)
        XCTAssertTrue(app.buttons["Start Geotagging"].exists)

        openLinkSettings()
        selectSetting("Continue in Background")
        dismissSheetInteractively()
        XCTAssertTrue(app.staticTexts["While Open · Battery Saver"].waitForExistence(timeout: 2))

        openLinkSettings()
        selectSetting("Continue in Background")
        selectSetting("Best Accuracy")
        app.buttons["settings-apply"].tap()

        XCTAssertTrue(app.staticTexts["Background · Best Accuracy"].waitForExistence(timeout: 2))
        XCTAssertTrue(app.staticTexts["Background Permission Needed"].exists)
    }

    func testSettingsFailurePreservesPreviousSummary() {
        launch("settings-failure")
        openLinkSettings()
        selectSetting("Best Accuracy")
        app.buttons["settings-apply"].tap()

        XCTAssertTrue(app.staticTexts["Changes couldn’t be applied. Your previous settings are still active."].waitForExistence(timeout: 2))
        app.buttons["settings-cancel"].tap()
        XCTAssertTrue(app.staticTexts["While Open · Battery Saver"].waitForExistence(timeout: 2))
    }

    func testDiagnosticsPreservesDetailsAndWarnsBeforeCopy() {
        launch("ready")
        app.buttons["diagnostics-link"].tap()

        XCTAssertTrue(app.navigationBars["Diagnostics"].waitForExistence(timeout: 2))
        XCTAssertTrue(app.staticTexts["DD11 timezone"].exists)
        XCTAssertTrue(app.staticTexts["Mode"].exists)
        scrollUntilVisible(app.buttons["copy-diagnostics"])
        XCTAssertTrue(app.staticTexts["Diagnostic logs may include recent coordinates. Review them before sharing."].exists)
        XCTAssertTrue(app.buttons["copy-diagnostics"].exists)
        app.buttons["copy-diagnostics"].tap()
        XCTAssertTrue(app.buttons["Copied Diagnostic Log"].exists)

        app.navigationBars.buttons.element(boundBy: 0).tap()
        XCTAssertTrue(app.staticTexts["Ready to Geotag"].waitForExistence(timeout: 2))
    }

    func testDiagnosticsEmptyStateAndBoundedDenseLogRemainNavigable() {
        launch("empty-diagnostics")
        app.buttons["diagnostics-link"].tap()
        scrollUntilVisible(app.descendants(matching: .any)["diagnostics-empty-log"])
        XCTAssertTrue(app.descendants(matching: .any)["diagnostics-empty-log"].exists)

        app.terminate()
        launch("dense-diagnostics")
        app.buttons["diagnostics-link"].tap()
        scrollUntilVisible(app.buttons["copy-diagnostics"])
        XCTAssertTrue(app.buttons["copy-diagnostics"].isHittable)
    }

    func testAccessibilityLabelsAndReadingOrderFollowThePrimaryWorkflow() {
        launch("ready")
        let status = app.staticTexts["Ready to Geotag"]
        let camera = app.staticTexts["Camera"]
        let location = app.staticTexts["iPhone Location"]
        let update = app.staticTexts["Last Camera Update"]
        let settings = app.buttons["link-settings"]
        let diagnostics = app.buttons["diagnostics-link"]

        XCTAssertTrue(status.waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Connected · ILCE-7CM2"].exists)
        XCTAssertTrue(app.staticTexts["Ready · ±8 m"].exists)
        XCTAssertTrue(app.staticTexts["12 seconds ago"].exists)
        XCTAssertLessThan(status.frame.minY, camera.frame.minY)
        XCTAssertLessThan(camera.frame.minY, location.frame.minY)
        XCTAssertLessThan(location.frame.minY, update.frame.minY)
        XCTAssertLessThan(update.frame.minY, settings.frame.minY)
        XCTAssertLessThan(settings.frame.minY, diagnostics.frame.minY)
    }

    func testAccessibilityAuditAtLargestTextSize() throws {
        launch(
            "ready",
            arguments: ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityExtraExtraExtraLarge"]
        )
        XCTAssertTrue(app.staticTexts["Ready to Geotag"].waitForExistence(timeout: 5))
        try app.performAccessibilityAudit(
            for: [.contrast, .hitRegion, .sufficientElementDescription, .textClipped, .trait]
        )
    }

    func testDarkIncreasedContrastAndReducedMotionAudit() throws {
        launch(
            "ready",
            arguments: [
                "-AppleInterfaceStyle", "Dark",
                "-UIAccessibilityDarkerSystemColorsEnabled", "YES",
                "-UIAccessibilityReduceMotionEnabled", "YES",
            ]
        )
        XCTAssertTrue(app.staticTexts["Ready to Geotag"].waitForExistence(timeout: 5))
        try app.performAccessibilityAudit(
            for: [.contrast, .hitRegion, .sufficientElementDescription, .textClipped, .trait]
        )
    }

    func testLandscapeKeepsPrimaryActionReachable() {
        XCUIDevice.shared.orientation = .landscapeLeft
        launch("not-connected")

        XCTAssertTrue(app.buttons["Start Geotagging"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Start Geotagging"].isHittable)
    }

    private func launch(_ scenario: String, arguments: [String] = []) {
        app = XCUIApplication()
        app.launchEnvironment["SONYGEOTAG_UI_SCENARIO"] = scenario
        app.launchArguments += arguments
        app.launch()
    }

    private func waitForDisappearance(_ element: XCUIElement, timeout: TimeInterval = 2) {
        let expectation = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "exists == false"),
            object: element
        )
        XCTAssertEqual(XCTWaiter.wait(for: [expectation], timeout: timeout), .completed)
    }

    private func openLinkSettings() {
        let button = app.buttons["link-settings"]
        scrollUntilVisible(button)
        XCTAssertTrue(button.isHittable)
        button.tap()
        XCTAssertTrue(app.navigationBars["Link Settings"].waitForExistence(timeout: 3))
    }

    private func selectSetting(_ label: String) {
        let button = app.buttons[label]
        let text = app.staticTexts[label]
        for _ in 0..<5 {
            if button.exists {
                button.tap()
                return
            }
            if text.exists {
                text.tap()
                return
            }
            app.swipeUp()
        }
        XCTFail("Missing setting option: \(label)")
    }

    private func dismissSheetInteractively() {
        let start = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.08))
        let end = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.9))
        start.press(forDuration: 0.1, thenDragTo: end)
        XCTAssertFalse(app.navigationBars["Link Settings"].waitForExistence(timeout: 1))
    }

    private func scrollUntilVisible(_ element: XCUIElement) {
        for _ in 0..<8 {
            if element.exists && element.isHittable { return }
            app.swipeUp()
        }
    }
}
