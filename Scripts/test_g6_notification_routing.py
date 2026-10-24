#!/usr/bin/env python3
"""Execute the production value callback with synthetic CoreBluetooth stand-ins.

No device or health data. This tests routing/locking, not Apple's BLE delivery.
The callback body is extracted unchanged, so a disconnected helper cannot pass.
"""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "CGMBLEKit/CGMBLEKit/PeripheralManager.swift"

PRELUDE = r'''
import Foundation
final class CallbackLock {
    private let lockObject = NSLock()
    func lock() { lockObject.lock() }
    func unlock() { lockObject.unlock() }
    func `try`() -> Bool { lockObject.try() }
    func broadcast() {}
}
final class CBPeripheral {}
final class CBCharacteristic: NSObject {
    let uuid: String
    var value: Data? = Data([0x31])
    init(_ uuid: String) { self.uuid = uuid }
}
final class Receiver {
    var deliveries = 0
    func peripheralManager(_ manager: PeripheralManager, didUpdateValueFor characteristic: CBCharacteristic) {
        precondition(manager.commandLock.try(), "Delegate invoked under command lock")
        manager.commandLock.unlock()
        deliveries += 1
    }
}
final class PeripheralManager {
    enum CommandCondition {
        case valueUpdate(characteristic: CBCharacteristic, matching: ((Data?) -> Bool)?)
        case discoverServices
        case notificationStateUpdate
    }
    struct Configuration {
        var valueUpdateMacros: [String: (PeripheralManager) -> Void] = [:]
    }
    let commandLock = CallbackLock()
    var commandConditions: [CommandCondition] = []
    var commandError: Error?
    var configuration = Configuration()
    var delegate: Receiver? = Receiver()
'''

TESTS = r'''
}
let peer = CBPeripheral()
let control = CBCharacteristic("control")
let auth = CBCharacteristic("authentication")
let manager = PeripheralManager()
func expect(_ value: Bool, _ message: String) {
    if !value { print("FAIL: " + message); exit(2) }
}
// Both clock and glucose use control. Neither is a reply to setup operations.
for condition: PeripheralManager.CommandCondition in [.discoverServices, .notificationStateUpdate,
        .valueUpdate(characteristic: auth, matching: nil)] {
    manager.commandConditions = [condition]
    let before = manager.delegate!.deliveries
    manager.peripheral(peer, didUpdateValueFor: control, error: nil)
    expect(manager.delegate!.deliveries == before + 1, "Pending setup swallowed unsolicited control")
    expect(manager.commandConditions.count == 1, "Unsolicited update consumed unrelated condition")
}
manager.commandConditions = []
let before = manager.delegate!.deliveries
manager.peripheral(peer, didUpdateValueFor: control, error: NSError(domain: "synthetic", code: 1))
expect(manager.delegate!.deliveries == before, "Error callback forwarded stale value")
manager.peripheral(peer, didUpdateValueFor: control, error: nil)
expect(manager.delegate!.deliveries == before + 1, "Idle notification lost")
// Expected replies remain exclusively consumed by the synchronous operation.
manager.commandConditions = [.valueUpdate(characteristic: control, matching: { $0 == Data([0x31]) })]
manager.peripheral(peer, didUpdateValueFor: control, error: nil)
expect(manager.commandConditions.isEmpty, "Expected reply not consumed")
expect(manager.delegate!.deliveries == before + 1, "Expected reply double delivered")
manager.commandConditions = [.valueUpdate(characteristic: control, matching: { _ in false })]
manager.peripheral(peer, didUpdateValueFor: control, error: nil)
expect(manager.commandConditions.count == 1, "Unmatched reply consumed")
expect(manager.delegate!.deliveries == before + 2, "Unmatched unsolicited update lost")
var macros = 0
manager.configuration.valueUpdateMacros[control.uuid] = { _ in macros += 1 }
manager.peripheral(peer, didUpdateValueFor: control, error: nil)
expect(macros == 1 && manager.delegate!.deliveries == before + 2, "Macro routing changed")
print("PASS: production callback routing, error rejection, command ownership and unlocked delivery")
'''


def main():
    source = SOURCE.read_text()
    start = source.index("    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor")
    end = source.index("    func peripheral(_ peripheral: CBPeripheral, didReadRSSI", start)
    callback = source[start:end]
    assert "notifyDelegate = error == nil" in callback
    variants = {
        "production": (callback, 0),
        "old-pending-drop": (callback.replace("notifyDelegate = error == nil", "notifyDelegate = commandConditions.isEmpty"), 2),
        "stale-error-forwarding": (callback.replace("notifyDelegate = error == nil", "notifyDelegate = true"), 2),
    }
    with tempfile.TemporaryDirectory(prefix="live-g6-routing-") as folder:
        path = Path(folder)
        for name, (body, expected) in variants.items():
            swift = path / (name + ".swift")
            binary = path / name
            swift.write_text(PRELUDE + body + TESTS)
            subprocess.run(["xcrun", "swiftc", "-D", "LIVE_READ_ONLY", str(swift), "-o", str(binary)], check=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            assert result.returncode == expected, (name, result.returncode, result.stdout, result.stderr)
            print(f"{name}: expected exit {expected}; {result.stdout.strip()}")


if __name__ == "__main__":
    main()
