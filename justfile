set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

xcode_dev_dir := "/Applications/Xcode.app/Contents/Developer"
ios_project := "ios/CameraGPSLink/CameraGPSLink.xcodeproj"
ios_target := "CameraGPSLink"
ios_scheme := "CameraGPSLink"
ios_smoke := "/tmp/CameraGPSLinkSmoke"
ios_test_device_name := "CameraGPSLink Tests"
ios_test_destination := "platform=iOS Simulator,name=" + ios_test_device_name + ",OS=latest"

[default]
all: check

# Show available recipes
list:
    just --list

# Run the full local verification gate
check: py-check ios-check

# Format Python code using ruff
format:
    uv run ruff format src tests

# Lint Python code using ruff and apply safe fixes
lint:
    uv run ruff check --fix src tests

# Lint Python code without modifying files
lint-check:
    uv run ruff check src tests

# Type check Python code using ty
type:
    uv run ty check src tests

# Run Python tests
test:
    uv run pytest tests

# Run Python tests with coverage and verbose output
coverage:
    uv run pytest -v -s --cov=src tests

# Run Python lint, type check, and tests
py-check: lint-check type test

# Open the iOS app project in Xcode
ios-open:
    open {{ios_project}}

# Run the Swift DD11 protocol and location policy smoke test
ios-smoke:
    swiftc ios/CameraGPSLink/CameraGPSLink/SonyProtocol.swift ios/CameraGPSLink/CameraGPSLink/LocationProvider.swift ios/CameraGPSLink/CameraGPSLinkTests/main.swift -o {{ios_smoke}}
    {{ios_smoke}}

# Type check all Swift sources
ios-typecheck:
    swiftc -typecheck ios/CameraGPSLink/CameraGPSLink/*.swift

# Lint iOS plist/project XML files
ios-lint-project:
    plutil -lint ios/CameraGPSLink/CameraGPSLink/Info.plist ios/CameraGPSLink/CameraGPSLink.xcodeproj/project.pbxproj
    xmllint --noout ios/CameraGPSLink/CameraGPSLink.xcodeproj/xcshareddata/xcschemes/CameraGPSLink.xcscheme

# Build the iOS target for Simulator
ios-build-sim:
    DEVELOPER_DIR={{xcode_dev_dir}} xcodebuild -project {{ios_project}} -target {{ios_target}} -sdk iphonesimulator -configuration Debug build

# Compile the iOS target for device without code signing
ios-build-device-nosign:
    DEVELOPER_DIR={{xcode_dev_dir}} xcodebuild -project {{ios_project}} -target {{ios_target}} -sdk iphoneos -configuration Debug CODE_SIGNING_ALLOWED=NO build

# Create a project-dedicated simulator so concurrent XCUITest suites cannot steal focus
ios-test-prepare:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! DEVELOPER_DIR={{xcode_dev_dir}} xcrun simctl list devices available | grep -Fq '{{ios_test_device_name}} ('; then
        runtime=$(DEVELOPER_DIR={{xcode_dev_dir}} xcrun simctl list runtimes available -j | python3 -c 'import json,sys; runtimes=[r for r in json.load(sys.stdin)["runtimes"] if r["platform"] == "iOS" and r.get("isAvailable", True)]; print(runtimes[-1]["identifier"])')
        DEVELOPER_DIR={{xcode_dev_dir}} xcrun simctl create '{{ios_test_device_name}}' com.apple.CoreSimulator.SimDeviceType.iPhone-17 "$runtime" >/dev/null
    fi

# Run the iOS XCTest unit suite
[no-exit-message]
ios-unit-test: ios-test-prepare
    DEVELOPER_DIR={{xcode_dev_dir}} xcodebuild test -project {{ios_project}} -scheme {{ios_scheme}} -destination '{{ios_test_destination}}' -only-testing:CameraGPSLinkUnitTests

# Run the iOS XCUITest suite
[no-exit-message]
ios-ui-test: ios-test-prepare
    DEVELOPER_DIR={{xcode_dev_dir}} xcodebuild test -project {{ios_project}} -scheme {{ios_scheme}} -destination '{{ios_test_destination}}' -only-testing:CameraGPSLinkUITests

# Run all iOS XCTest suites
[no-exit-message]
ios-test: ios-test-prepare
    DEVELOPER_DIR={{xcode_dev_dir}} xcodebuild test -project {{ios_project}} -scheme {{ios_scheme}} -destination '{{ios_test_destination}}'

# Run all iOS compile/smoke/test checks
ios-check: ios-smoke ios-typecheck ios-lint-project ios-build-sim ios-build-device-nosign ios-test

# Show Xcode destinations for the app scheme
ios-destinations:
    DEVELOPER_DIR={{xcode_dev_dir}} xcodebuild -showdestinations -project {{ios_project}} -scheme {{ios_scheme}}

# Launch the installed iOS app on a USB-connected device and attach console output
ios-console device="00008140-0001588C017B001C":
    DEVELOPER_DIR={{xcode_dev_dir}} xcrun devicectl device process launch --device {{device}} --console dev.narumi.cameragpslink

# Scan for the camera over BLE
ble-scan target="ILCE-7CM2":
    uv run sonygeotag scan --target {{target}} --timeout 15

# Dump Sony camera GATT services/characteristics
ble-gatt target="ILCE-7CM2":
    uv run sonygeotag gatt-dump --target {{target}} --timeout 10

# Decode a strict read-only camera information snapshot
ble-info target="ILCE-7CM2":
    uv run sonygeotag camera-info --target {{target}} --timeout 15 --pair

# Subscribe to notifications from the camera
ble-notify target="ILCE-7CM2" duration="60":
    uv run sonygeotag notify-log --target {{target}} --duration {{duration}}

# Dry-run encode/send a DD11 GPS packet without writing to BLE
location-dry-run lat lon:
    uv run sonygeotag send-location --lat {{lat}} --lon {{lon}}

# Write GPS to the camera; requires explicit lat/lon and camera pairing mode when needed
location-write lat lon target="ILCE-7CM2" duration="60" interval="30":
    uv run sonygeotag send-location --target {{target}} --lat {{lat}} --lon {{lon}} --write --duration {{duration}} --interval {{interval}} --pair --vendor-pair-init

# Remove local build/test artifacts
clean:
    rm -rf ios/CameraGPSLink/build .pytest_cache .ruff_cache .coverage htmlcov {{ios_smoke}}

# Build and publish the package to PyPI
publish:
    uv build --no-sources
    uv publish
