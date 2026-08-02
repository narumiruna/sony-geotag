# iOS Project Rename Plan

## Goal

Rename the complete iOS project identity and filesystem tree from `SonyGeoTag` to `CameraGPSLink` while preserving the already-selected display name `Camera GPS Link`, bundle identifier `dev.narumi.cameragpslink`, Sony protocol behavior, and Python package identity.

## Non-Goals

- Do not rename the repository, Python package/CLI, Sony protocol types, camera compatibility references, bundle identifier, or persisted settings keys.
- Do not rewrite unrelated historical plans.

## Plan

- [x] Rename `ios/SonyGeoTag` and all app-owned nested project, source, target, scheme, test, file, Swift type, and module names to `CameraGPSLink`; verify no `SonyGeoTag` reference remains inside `ios/CameraGPSLink` with `rg`. Verified: the complete tree moved to `ios/CameraGPSLink`, app/model/test files and symbols were renamed, and scoped `rg` returns no old identity.
- [x] Update `CameraGPSLink.xcodeproj`, its shared scheme, test hosts, source paths, products, and Info.plist paths so Xcode lists only `CameraGPSLink`, `CameraGPSLinkUnitTests`, and `CameraGPSLinkUITests`; verify with `xcodebuild -list` and `just ios-lint-project`. Verified: `xcodebuild -list` reports exactly the renamed project, three targets, and shared scheme; plist/project/scheme lint passes.
- [x] Update root documentation and `justfile` paths, target/scheme names, test selectors, temporary binary/device names, and clean rules; verify old `ios/SonyGeoTag` references are absent from current runtime/tooling documentation and `just --list` succeeds. Verified: root README and every iOS recipe point to `ios/CameraGPSLink`; current runtime/tooling files contain no old path and `just --list` succeeds.
- [x] Run `just check`, confirm the built app retains display name `Camera GPS Link` and bundle identifier `dev.narumi.cameragpslink`, enforce the 1000-line source limit, and run `git diff --check`. Verified: full gate passed 44 Python tests, 30 renamed iOS unit tests, 15 renamed XCUITests, all lint/smoke/type/build checks; built product metadata matches; largest Swift source is 898 lines; diff check passes.

## Completion Checklist

- [x] The filesystem, Xcode project, scheme, app target, products, Swift module/types, and test targets consistently use `CameraGPSLink`. Evidence: scoped identity audit and `xcodebuild -list` pass.
- [x] Sony protocol/manufacturer functionality and the Python `sonygeotag` package remain unchanged. Evidence: Python/protocol paths have no content diff and all smoke/Python tests pass.
- [x] Display name and bundle identifier remain `Camera GPS Link` and `dev.narumi.cameragpslink`. Evidence: extracted from the built `CameraGPSLink.app/Info.plist`.
- [x] All local verification gates pass and no known rename work remains. Evidence: final `just check`, line-count, identity, and diff audits pass.
- [x] The completed plan is archived under `docs/plans/archived/`.
