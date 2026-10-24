#!/usr/bin/env python3
"""Bounded macOS checks for the restricted Loop research target and built app.

Use --app PATH for compiled simulator evidence, --unsigned for device products.
These checks do not establish BLE coexistence or clinical performance.
"""
import argparse
import json
from pathlib import Path
import plistlib
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
FRAMEWORKS = {"CGMBLEKit", "CGMBLEKitUI", "LoopKit", "LoopKitUI", "ShareClient", "ShareClientUI", "SwiftCharts"}
FORBIDDEN_SELECTORS = {
    "CGMBLEKit": {"writeValue:forCharacteristic:type:", "scanForPeripheralsWithServices:options:", "retrievePeripheralsWithIdentifiers:"},
    "ShareClient": {"dataTaskWithRequest:completionHandler:"},
}


def project(path):
    return json.loads(subprocess.check_output(["plutil", "-convert", "json", "-o", "-", str(path)], text=True))


def check_flags(data, targets):
    objects = data["objects"]
    root = objects[data["rootObject"]]
    configs = objects[root["buildConfigurationList"]]["buildConfigurations"]
    for config in configs:
        assert "LIVE_READ_ONLY" in objects[config]["buildSettings"].get("SWIFT_ACTIVE_COMPILATION_CONDITIONS", ""), "Project guard missing"
    for target in (objects[t] for t in root["targets"] if objects[t]["name"] in targets):
        for config in objects[target["buildConfigurationList"]]["buildConfigurations"]:
            conditions = objects[config]["buildSettings"].get("SWIFT_ACTIVE_COMPILATION_CONDITIONS", "$(inherited)")
            assert "LIVE_READ_ONLY" in conditions or "$(inherited)" in conditions, target["name"] + " overrides research guard"


def check_stopped_gate(bluetooth):
    queue_body = bluetooth.split("private func managerQueue_scanForPeripheral()", 1)[1].split("let currentState", 1)[0]
    assert "guard stayConnected else { return }" in queue_body, "Stopped observer can retry"


def check_connecting_gate(bluetooth):
    body = bluetooth.split("private func managerQueue_scanForPeripheral()", 1)[1].split("#else", 1)[0]
    guard = "guard currentState != .connecting else { return }"
    assert guard in body and body.index(guard) < body.index("manager.connect(candidate)"), "Duplicate pending connection requests possible"


def check_passive_readiness_wiring(transmitter):
    body = transmitter.split("isReadyWithError error: Error?)", 1)[1].split("func bluetoothManager(_ manager: BluetoothManager, shouldConnectPeripheral", 1)[0]
    research = body.split("if self.passiveModeEnabled", 1)[1].split("#else", 1)[0]
    assert "ResearchPassiveSubscriptions.subscribe(stillObserving:" in research, "Passive readiness does not enable control observation"
    assert "try peripheral.listenToCharacteristic(characteristic)" in research, "Subscription plan is not connected to the actual notification helper"


def check_foreground_collection(app):
    new_data = app.split("case .newData(let samples):", 1)[1].split("case .unreliableData", 1)[0].split("catch", 1)[0]
    assert "invalidate()" not in new_data and "cancel()" not in new_data, "A valid reading stops collection"
    lookup = app.split("private func attemptExistingConnection()", 1)[1].split("private func refreshConnectionDiagnostics()", 1)[0]
    assert "manager.transmitter.resumeScanning()" in lookup and "fetchNewDataIfNeeded" not in lookup, "Lookup must not rebuild the journal and chart every second"
    assert "UIApplication.didBecomeActiveNotification" in app and 'bool(forKey: "researchCollectionPaused")' in app, "Automatic resume must honor owner pause"


def check_untimed_evidence_wiring(transmitter, manager):
    branch = transmitter.split("case .untimed:", 1)[1].split("case .timed", 1)[0]
    assert "notifyResearchEvidenceUpdated()" in branch and "didRead:" not in branch, "Untimed evidence must notify without becoming a timed reading"
    assert "hasClock: lastTimeMessage != nil && activationDate != nil" in transmitter, "Observed clock prerequisites missing"
    callback = manager.split("func transmitterDidUpdateResearchEvidence", 1)[1].split("}", 1)[0]
    assert "updateDelegate(with: .noData)" in callback, "Untimed evidence must reach private journal without newData"


def check_sources():
    for path, names in [("CGMBLEKit/CGMBLEKit.xcodeproj/project.pbxproj", {"CGMBLEKit", "CGMBLEKitUI"}), ("dexcom-share-client-swift/ShareClient.xcodeproj/project.pbxproj", {"ShareClient", "ShareClientUI"}), ("Loop/Loop.xcodeproj/project.pbxproj", {"Loop"})]:
        check_flags(project(ROOT / path), names)
    objects = project(ROOT / "Loop/Loop.xcodeproj/project.pbxproj")["objects"]
    target = objects["43776F8B1B8022E90074EA36"]
    phases = [objects[p] for p in target["buildPhases"]]
    assert [p["isa"] for p in phases] == ["PBXSourcesBuildPhase", "PBXFrameworksBuildPhase", "PBXResourcesBuildPhase", "PBXCopyFilesBuildPhase"]
    sources = {objects[objects[b]["fileRef"]]["path"] for b in phases[0]["files"]}
    assert sources == {"Research/ResearchApp.swift", "Research/ResearchPolicy.swift", "Research/ResearchObservation.swift"}, sources
    assert not target["dependencies"], "Unexpected target dependency"
    assert plistlib.loads((ROOT / "Loop/Research/Research.entitlements").read_bytes()) == {}
    for scheme in ["LoopG6Research", "LoopWorkspace"]:
        xml = ET.parse(ROOT / f"LoopWorkspace.xcworkspace/xcshareddata/xcschemes/{scheme}.xcscheme")
        names = {r.attrib["BlueprintName"] for r in xml.findall(".//BuildActionEntry/BuildableReference")}
        assert names == {"Loop", "LoopKit", "LoopKitUI"}, names
    bluetooth = (ROOT / "CGMBLEKit/CGMBLEKit/BluetoothManager.swift").read_text()
    check_stopped_gate(bluetooth)
    check_connecting_gate(bluetooth)
    try:
        check_connecting_gate(bluetooth.replace("guard currentState != .connecting else { return }", ""))
    except AssertionError:
        pass
    else:
        raise AssertionError("Negative pending-connection control failed")
    transmitter = (ROOT / "CGMBLEKit/CGMBLEKit/Transmitter.swift").read_text()
    manager = (ROOT / "CGMBLEKit/CGMBLEKit/TransmitterManager.swift").read_text()
    check_untimed_evidence_wiring(transmitter, manager)
    try:
        check_untimed_evidence_wiring(transmitter.replace("notifyResearchEvidenceUpdated()", "disabledEvidenceNotification()"), manager)
    except AssertionError:
        pass
    else:
        raise AssertionError("Negative untimed journal-notification control failed")
    check_passive_readiness_wiring(transmitter)
    subscription = transmitter.split("enum ResearchPassiveSubscriptions", 1)[1].split("public final class Transmitter", 1)[0]
    assert "[CGMServiceCharacteristicUUID.control]" in subscription and ".authentication" not in subscription, "Research subscription must be control-only"
    app = (ROOT / "Loop/Research/ResearchApp.swift").read_text()
    check_foreground_collection(app)
    try:
        check_foreground_collection(app.replace("case .newData(let samples): do {", "case .newData(let samples): do { connectionProbe.cancel();"))
    except AssertionError:
        pass
    else:
        raise AssertionError("Negative successful-reading cancellation control failed")
    try:
        check_passive_readiness_wiring(transmitter.replace("ResearchPassiveSubscriptions.subscribe(stillObserving:", "disabledSubscription(stillObserving:"))
    except AssertionError:
        pass
    else:
        raise AssertionError("Negative passive-readiness wiring control failed")
    stop_body = transmitter.split("public func stopScanning()", 1)[1].split("public var isScanning", 1)[0]
    assert stop_body.index("stayConnected = false") < stop_body.index("disconnect()"), "Stop must disable queued reconnects before disconnect"
    # Synthetic source regression exercises the same check after removing the queue gate.
    try:
        check_stopped_gate(bluetooth.replace("guard stayConnected else { return }", ""))
    except AssertionError:
        pass
    else:
        raise AssertionError("Negative stopped-queue control failed")
    # Representative regression: Debug target overrides must not silently drop the guard.
    data = project(ROOT / "CGMBLEKit/CGMBLEKit.xcodeproj/project.pbxproj")
    objects = data["objects"]
    target = next(x for x in objects.values() if x.get("isa") == "PBXNativeTarget" and x.get("name") == "CGMBLEKitUI")
    config = objects[target["buildConfigurationList"]]["buildConfigurations"][0]
    objects[config]["buildSettings"]["SWIFT_ACTIVE_COMPILATION_CONDITIONS"] = "DEBUG"
    try:
        check_flags(data, {"CGMBLEKitUI"})
    except AssertionError:
        pass
    else:
        raise AssertionError("Negative guard-override control failed")


def selectors(binary):
    return subprocess.check_output(["otool", "-v", "-s", "__TEXT", "__objc_methname", str(binary)], text=True)


def check_app(app, unsigned):
    names = {p.stem for p in (app / "Frameworks").glob("*.framework")}
    assert names == FRAMEWORKS, names
    assert not (app / "PlugIns").exists() and not (app / "Watch").exists()
    assert plistlib.loads((app / "Info.plist").read_bytes())["CFBundleIdentifier"] == "org.projectlive.loop.g6research"
    for name in names:
        folder = app / "Frameworks" / f"{name}.framework"
        info = plistlib.loads((folder / "Info.plist").read_bytes())
        assert (folder / info["CFBundleExecutable"]).is_file()
    for name, forbidden in FORBIDDEN_SELECTORS.items():
        actual = selectors(app / "Frameworks" / f"{name}.framework" / name)
        assert not any(s in actual for s in forbidden), name + " contains forbidden transport selector"
    if not unsigned:
        result = subprocess.run(["codesign", "-d", "--entitlements", "-", "--xml", str(app)], capture_output=True, check=True)
        assert plistlib.loads(result.stdout) == {}
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path)
    parser.add_argument("--unsigned", action="store_true")
    parser.add_argument("--negative-baseline-app", type=Path)
    args = parser.parse_args()
    check_sources()
    if args.app:
        check_app(args.app, args.unsigned)
    if args.negative_baseline_app:
        # The previously built unrestricted baseline must actually contain each denied selector.
        for name, forbidden in FORBIDDEN_SELECTORS.items():
            actual = selectors(args.negative_baseline_app / "Frameworks" / f"{name}.framework" / name)
            assert all(s in actual for s in forbidden), "Baseline negative control is not representative"
    print("PASS: source selection, guard inheritance, negative override, selected compiled checks")


if __name__ == "__main__":
    main()
