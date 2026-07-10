set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

xcode_dev_dir := "/Applications/Xcode.app/Contents/Developer"
ios_project := "ios/SonyGeoTag/SonyGeoTag.xcodeproj"
ios_target := "SonyGeoTag"
ios_scheme := "SonyGeoTag"
ios_smoke := "/tmp/SonyGeoTagSmoke"

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
    swiftc ios/SonyGeoTag/SonyGeoTag/SonyProtocol.swift ios/SonyGeoTag/SonyGeoTag/LocationProvider.swift ios/SonyGeoTag/SonyGeoTagTests/main.swift -o {{ios_smoke}}
    {{ios_smoke}}

# Type check all Swift sources
ios-typecheck:
    swiftc -typecheck ios/SonyGeoTag/SonyGeoTag/*.swift

# Lint iOS plist/project XML files
ios-lint-project:
    plutil -lint ios/SonyGeoTag/SonyGeoTag/Info.plist ios/SonyGeoTag/SonyGeoTag.xcodeproj/project.pbxproj
    xmllint --noout ios/SonyGeoTag/SonyGeoTag.xcodeproj/xcshareddata/xcschemes/SonyGeoTag.xcscheme

# Build the iOS target for Simulator
ios-build-sim:
    DEVELOPER_DIR={{xcode_dev_dir}} xcodebuild -project {{ios_project}} -target {{ios_target}} -sdk iphonesimulator -configuration Debug build

# Compile the iOS target for device without code signing
ios-build-device-nosign:
    DEVELOPER_DIR={{xcode_dev_dir}} xcodebuild -project {{ios_project}} -target {{ios_target}} -sdk iphoneos -configuration Debug CODE_SIGNING_ALLOWED=NO build

# Run all iOS compile/smoke checks
ios-check: ios-smoke ios-typecheck ios-lint-project ios-build-sim ios-build-device-nosign

# Show Xcode destinations for the app scheme
ios-destinations:
    DEVELOPER_DIR={{xcode_dev_dir}} xcodebuild -showdestinations -project {{ios_project}} -scheme {{ios_scheme}}

# Launch the installed iOS app on a USB-connected device and attach console output
ios-console device="00008140-0001588C017B001C":
    DEVELOPER_DIR={{xcode_dev_dir}} xcrun devicectl device process launch --device {{device}} --console com.narumi.SonyGeoTag

# Scan for the camera over BLE
ble-scan target="ILCE-7CM2":
    uv run sonygeotag scan --target {{target}} --timeout 15

# Dump Sony camera GATT services/characteristics
ble-gatt target="ILCE-7CM2":
    uv run sonygeotag gatt-dump --target {{target}} --timeout 10

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
    rm -rf ios/SonyGeoTag/build .pytest_cache .ruff_cache .coverage htmlcov {{ios_smoke}}

# Build and publish the package to PyPI
publish:
    uv build --no-sources
    uv publish
