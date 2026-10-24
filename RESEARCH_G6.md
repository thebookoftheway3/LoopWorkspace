# Restricted Loop G6 research build

This branch builds the existing Loop application target with a restricted entrypoint. It reuses LoopKitUI's glucose chart and CGMBLEKit's G6 setup/settings screens. Original Loop therapy, pump onboarding, services, watch and extension entrypoints are excluded from this target. It is not a general treatment build of Loop.

The independent Dexcom G6 phone app and t:slim Basal-IQ treatment must remain operating. The research variant has no pump manager, simulated pump, pump transport, dosing UI, or treatment settings. Its separate bundle identifier is `org.projectlive.loop.g6research`, with empty application entitlements and private local storage.

## Build and verification

Open `LoopWorkspace.xcworkspace` and select `LoopG6Research`. Every reached CGM/Share Swift target must retain `LIVE_READ_ONLY`; `Scripts/verify_g6_research.py` checks target overrides. The branch's default `LoopWorkspace` scheme is restricted as well, to avoid accidentally selecting the full treatment build.

```sh
python3 Scripts/verify_g6_research.py --app /path/to/Loop.app
swiftc Loop/Research/ResearchPolicy.swift Loop/Research/ResearchObservation.swift Loop/Research/Tests/main.swift -o /tmp/live-g6-policy-tests
/tmp/live-g6-policy-tests
xcodebuild -workspace LoopWorkspace.xcworkspace -scheme LoopG6Research -configuration Debug \
  -destination 'platform=iOS Simulator,id=YOUR_SIMULATOR_ID' -jobs 2 \
  -parallel-testing-enabled NO -onlyUsePackageVersionsFromResolvedFile \
  -disableAutomaticPackageResolution CODE_SIGNING_ALLOWED=YES CODE_SIGN_IDENTITY=- test
```

The host-side Swift test emits a **synthetic fixture of the production observation format**, not a sensor reading. Never import that fixture as captured health evidence. Native tests cover empty history, a single observation, backfill rendering, and forbidden manager identifiers. Existing upstream therapy tests are not selected by this restricted scheme.

Application characteristic writes fail closed at both G6 write entrypoints; passive mode cannot be disabled and expiry reads cannot be enabled. Cloud HTTP, credential restoration/writes and the upstream simulator glucose generator are excluded. The app only considers peripherals returned by iOS as already system-connected: no discovery scan, known-UUID reconnect or new-device fallback. A foreground retry checks that existing connection for approximately one minute. BLE service discovery and notification subscription still use operating-system protocol operations; absence of application commands does not establish absence of every possible pairing prompt or impact. Hardware coexistence and background continuity remain unverified until owner-supervised testing. Upstream matching uses the last two transmitter-ID characters and does not independently prove the full transmitter identity.

## Local observations and export

Collection requires explicit consent before G6 configuration/restoration. Glucose observations retain measurement and receipt timestamps and upstream quality, including display-only/user-entered/condition/trend. The upstream manager filters unreliable packets and clamps glucose to its supported range; the research app preserves the resulting quality condition and does not describe this as raw sensor chemistry. No simulator sample generator runs.

Private append-only daily journals retain seven UTC calendar days. Retention is checked at opening, collection and export. Export separately requires owner consent and creates `Documents/LIVE/observations-v1.jsonl` for an owner-initiated USB/Finder copy. Export is disabled when reopening the app or deleting the local G6 observer. Files use complete protection until first device unlock and are excluded from backup. No cloud upload or network server is introduced.

The versioned format is `project-live.cgm-observation`, version 1, source `loop.dexcom-g6`, source version `loop-g6-passive-v1`. IDs are stable SHA-256 pseudonyms of source and sample sync identifier; no raw transmitter ID is exported. Glucose records remain private health data. This build is strictly observational and is not validation of real-world insulin control.
