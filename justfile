set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

[default]
all: check

# Show available recipes
list:
    just --list

# Run the full local verification gate
check: source-line-check py-check

# Reject Python program sources over 1000 lines
source-line-check:
    uv run python scripts/check_source_lines.py

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

# Scan for the camera over BLE
ble-scan target="ILCE-7CM2":
    uv run sonygeotag scan --target {{target}} --timeout 15

# Dump Sony camera GATT services/characteristics
ble-gatt target="ILCE-7CM2":
    uv run sonygeotag gatt-dump --target {{target}} --timeout 10

# Decode a strict read-only camera information snapshot
ble-info target="ILCE-7CM2":
    uv run sonygeotag camera-info --target {{target}} --timeout 15 --pair

# Capture a sanitized, strict read-only location compatibility snapshot
compatibility-snapshot target="ILCE-7CM2":
    uv run sonygeotag compatibility-snapshot --target {{target}} --timeout 15 --pair

# Verify matching GPS EXIF in a JPEG or HEIF image captured during the DD11 session
exif-verify photo lat lon not_before:
    uv run sonygeotag verify-exif --photo {{photo}} --lat {{lat}} --lon {{lon}} --not-before {{not_before}}

# Open the live read-only camera status TUI
ble-monitor target="ILCE-7CM2" interval="2":
    uv run sonygeotag monitor --target {{target}} --interval {{interval}} --pair

# Subscribe to notifications from the camera
ble-notify target="ILCE-7CM2" duration="60":
    uv run sonygeotag notify-log --target {{target}} --duration {{duration}}

# Dry-run encode/send a DD11 GPS packet without writing to BLE
location-dry-run lat lon:
    uv run sonygeotag send-location --lat {{lat}} --lon {{lon}}

# Write GPS to an already initialized camera; requires explicit authorization and lat/lon
location-write lat lon target="ILCE-7CM2" duration="60" interval="30":
    uv run sonygeotag send-location --target {{target}} --lat {{lat}} --lon {{lon}} --write --duration {{duration}} --interval {{interval}} --pair

# Remove local build/test artifacts
clean:
    rm -rf .pytest_cache .ruff_cache .coverage htmlcov

# Build and publish the package to PyPI
publish:
    uv build --no-sources
    uv publish
