import Combine
import CoreBluetooth
import CoreLocation
import Foundation

final class CameraBLEManager: NSObject, ObservableObject {
    @Published private(set) var state: CameraConnectionState = .idle
    @Published private(set) var discoveredCameraName: String?
    @Published private(set) var lastSentAt: Date?
    @Published private(set) var packetsSent = 0
    @Published private(set) var includeTimezone = true
    @Published private(set) var dd21ConfigHex: String?
    @Published private(set) var lastError: String?
    @Published private(set) var logLines: [String] = []

    var targetName = "ILCE-7CM2"
    var updateInterval: TimeInterval = 30

    private var centralManager: CBCentralManager!
    private var peripheral: CBPeripheral?
    private var characteristics: [String: CBCharacteristic] = [:]
    private var didStartLocationSetup = false
    private var locationProvider: (() -> CLLocation?)?
    private var sendTimer: Timer?
    private var operationQueue: [QueuedBLEOperation] = []
    private var pendingOperation: PendingBLEOperation?
    private var operationTimeoutTimer: Timer?
    private var onQueueEmpty: (() -> Void)?
    private let operationTimeout: TimeInterval = 12

    override init() {
        super.init()
        centralManager = CBCentralManager(delegate: self, queue: nil)
    }

    var canStart: Bool {
        state != .scanning && state != .connecting && state != .discovering && state != .enablingLocation && state != .linked
    }

    func startLink(locationProvider: @escaping () -> CLLocation?) {
        self.locationProvider = locationProvider
        packetsSent = 0
        lastSentAt = nil
        lastError = nil
        dd21ConfigHex = nil
        characteristics.removeAll()
        didStartLocationSetup = false
        stopTimer()
        appendLog("Starting Sony location link")

        guard centralManager.state == .poweredOn else {
            state = .bluetoothUnavailable
            appendLog("Bluetooth is not powered on: \(centralManager.state.rawValue)")
            return
        }
        scanForCamera()
    }

    func stopLink() {
        appendLog("Stopping Sony location link")
        stopTimer()
        stopOperationTimeout()
        state = .stopping
        operationQueue.removeAll()
        pendingOperation = nil

        guard peripheral != nil else {
            state = .stopped
            return
        }

        enqueueWrite(name: "DD31 disable", uuid: SonyProtocol.locationEnableUUID, data: Data([0x00]), required: false)
        enqueueWrite(name: "DD30 unlock", uuid: SonyProtocol.locationLockUUID, data: Data([0x00]), required: false)
        onQueueEmpty = { [weak self] in
            guard let self else { return }
            if let peripheral = self.peripheral {
                self.centralManager.cancelPeripheralConnection(peripheral)
            }
            self.state = .stopped
        }
        runNextOperationIfNeeded()
    }

    private func scanForCamera() {
        state = .scanning
        appendLog("Scanning for \(targetName)")
        centralManager.scanForPeripherals(withServices: nil, options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])
    }

    private func beginServiceDiscovery(for peripheral: CBPeripheral) {
        state = .discovering
        peripheral.delegate = self
        let serviceUUIDs = [
            CBUUID(string: SonyProtocol.locationServiceUUID),
            CBUUID(string: SonyProtocol.pairingServiceUUID),
        ]
        peripheral.discoverServices(serviceUUIDs)
    }

    private func maybeBeginLocationSetup() {
        guard !didStartLocationSetup else { return }
        guard characteristic(SonyProtocol.locationLockUUID) != nil,
              characteristic(SonyProtocol.locationEnableUUID) != nil,
              characteristic(SonyProtocol.locationDataWriteUUID) != nil
        else {
            return
        }

        didStartLocationSetup = true
        state = .enablingLocation
        appendLog("Required Sony location characteristics found")

        if characteristic(SonyProtocol.locationStatusNotifyUUID) != nil {
            enqueueNotify(name: "DD01 notify", uuid: SonyProtocol.locationStatusNotifyUUID, enabled: true, required: false)
        }

        if characteristic(SonyProtocol.pairingInitUUID) != nil {
            enqueueWrite(
                name: "EE01 pairing init",
                uuid: SonyProtocol.pairingInitUUID,
                data: SonyProtocol.pairingInitPayload,
                required: false
            )
        }
        enqueueWrite(name: "DD30 lock", uuid: SonyProtocol.locationLockUUID, data: Data([0x01]), required: true)
        enqueueWrite(name: "DD31 enable", uuid: SonyProtocol.locationEnableUUID, data: Data([0x01]), required: true)
        enqueueRead(name: "DD32 time correction", uuid: SonyProtocol.timeCorrectionUUID, required: false)
        enqueueRead(name: "DD33 area adjustment", uuid: SonyProtocol.areaAdjustmentUUID, required: false)
        enqueueRead(name: "DD21 config", uuid: SonyProtocol.locationConfigReadUUID, required: false) { [weak self] data in
            guard let self else { return }
            self.dd21ConfigHex = SonyProtocol.hex(data)
            self.includeTimezone = SonyProtocol.parseConfigRequiresTimezone(data)
            self.appendLog("DD21 config \(SonyProtocol.hex(data)); includeTimezone=\(self.includeTimezone)")
        }

        onQueueEmpty = { [weak self] in
            self?.startSendingLocations()
        }
        runNextOperationIfNeeded()
    }

    func sendLocationNow() {
        sendLocationIfDue(force: true)
    }

    func sendLocationIfDue(force: Bool = false) {
        guard state == .linked else { return }
        if !force, let lastSentAt, Date().timeIntervalSince(lastSentAt) < updateInterval {
            return
        }
        sendLocationOnce()
    }

    private func startSendingLocations() {
        state = .linked
        appendLog("Location link active; send photos only after DD11 location OK / Packets sent > 0")
        sendLocationOnce()
        sendTimer = Timer.scheduledTimer(withTimeInterval: updateInterval, repeats: true) { [weak self] _ in
            self?.sendLocationOnce()
        }
    }

    private func sendLocationOnce() {
        guard pendingOperation == nil else {
            appendLog("Skipping location send because a BLE operation is still pending")
            return
        }
        guard let location = locationProvider?() else {
            appendLog("No GPS fix available yet; waiting for iPhone location")
            return
        }
        guard location.horizontalAccuracy >= 0 else {
            appendLog("Ignoring invalid GPS fix")
            return
        }

        do {
            let packet = try SonyProtocol.encodeLocationPacket(
                latitude: location.coordinate.latitude,
                longitude: location.coordinate.longitude,
                date: Date(),
                includeTimezone: includeTimezone
            )
            appendLog(
                String(
                    format: "Queue DD11 %.7f, %.7f acc=±%.0fm age=%.0fs bytes=%d",
                    location.coordinate.latitude,
                    location.coordinate.longitude,
                    location.horizontalAccuracy,
                    Date().timeIntervalSince(location.timestamp),
                    packet.count
                )
            )
            enqueueWrite(name: "DD11 location", uuid: SonyProtocol.locationDataWriteUUID, data: packet, required: true)
            runNextOperationIfNeeded()
        } catch {
            fail("Failed to encode location packet: \(error.localizedDescription)")
        }
    }

    private func enqueueWrite(name: String, uuid: String, data: Data, required: Bool) {
        operationQueue.append(
            QueuedBLEOperation(name: name, required: required) { [weak self] in
                self?.startWrite(name: name, uuid: uuid, data: data, required: required)
            }
        )
    }

    private func enqueueRead(
        name: String,
        uuid: String,
        required: Bool,
        onValue: ((Data) -> Void)? = nil
    ) {
        operationQueue.append(
            QueuedBLEOperation(name: name, required: required) { [weak self] in
                self?.startRead(name: name, uuid: uuid, required: required, onValue: onValue)
            }
        )
    }

    private func enqueueNotify(name: String, uuid: String, enabled: Bool, required: Bool) {
        operationQueue.append(
            QueuedBLEOperation(name: name, required: required) { [weak self] in
                self?.startNotify(name: name, uuid: uuid, enabled: enabled, required: required)
            }
        )
    }

    private func runNextOperationIfNeeded() {
        guard pendingOperation == nil else { return }
        guard !operationQueue.isEmpty else {
            let callback = onQueueEmpty
            onQueueEmpty = nil
            callback?()
            return
        }
        let operation = operationQueue.removeFirst()
        appendLog("BLE operation: \(operation.name)")
        operation.start()
    }

    private func startWrite(name: String, uuid: String, data: Data, required: Bool) {
        guard let peripheral, let characteristic = characteristic(uuid) else {
            completeOperation(name: name, error: "Missing characteristic \(uuid)", required: required)
            return
        }
        pendingOperation = .write(name: name, uuid: normalized(uuid), required: required)
        startOperationTimeout()

        if characteristic.properties.contains(.write) {
            peripheral.writeValue(data, for: characteristic, type: .withResponse)
            return
        }
        if characteristic.properties.contains(.writeWithoutResponse) {
            appendLog("\(name) uses writeWithoutResponse")
            peripheral.writeValue(data, for: characteristic, type: .withoutResponse)
            completeOperation(name: name, error: nil, required: required)
            return
        }

        completeOperation(name: name, error: "Characteristic \(uuid) is not writable", required: required)
    }

    private func startRead(name: String, uuid: String, required: Bool, onValue: ((Data) -> Void)?) {
        guard let peripheral, let characteristic = characteristic(uuid) else {
            completeOperation(name: name, error: "Missing characteristic \(uuid)", required: required)
            return
        }
        pendingOperation = .read(name: name, uuid: normalized(uuid), required: required, onValue: onValue)
        startOperationTimeout()
        peripheral.readValue(for: characteristic)
    }

    private func startNotify(name: String, uuid: String, enabled: Bool, required: Bool) {
        guard let peripheral, let characteristic = characteristic(uuid) else {
            completeOperation(name: name, error: "Missing characteristic \(uuid)", required: required)
            return
        }
        pendingOperation = .notify(name: name, uuid: normalized(uuid), required: required, enabled: enabled)
        startOperationTimeout()
        peripheral.setNotifyValue(enabled, for: characteristic)
    }

    private func completeOperation(name: String, error: String?, required: Bool) {
        pendingOperation = nil
        stopOperationTimeout()
        if let error {
            appendLog("\(name) failed: \(error)")
            if required {
                fail(error)
                return
            }
        } else {
            appendLog("\(name) OK")
            if name == "DD11 location" {
                packetsSent += 1
                lastSentAt = Date()
            }
        }
        runNextOperationIfNeeded()
    }

    private func fail(_ message: String) {
        lastError = message
        state = .failed
        stopTimer()
        stopOperationTimeout()
        operationQueue.removeAll()
        pendingOperation = nil
        appendLog("Failed: \(message)")
    }

    private func stopTimer() {
        sendTimer?.invalidate()
        sendTimer = nil
    }

    private func startOperationTimeout() {
        stopOperationTimeout()
        operationTimeoutTimer = Timer.scheduledTimer(withTimeInterval: operationTimeout, repeats: false) { [weak self] _ in
            self?.handleOperationTimeout()
        }
    }

    private func stopOperationTimeout() {
        operationTimeoutTimer?.invalidate()
        operationTimeoutTimer = nil
    }

    private func handleOperationTimeout() {
        guard let pendingOperation else { return }
        self.pendingOperation = nil
        appendLog("\(pendingOperation.name) timed out after \(Int(operationTimeout))s")
        if pendingOperation.required {
            fail("\(pendingOperation.name) timed out")
        } else {
            runNextOperationIfNeeded()
        }
    }

    private func appendLog(_ message: String) {
        let timestamp = Date().formatted(date: .omitted, time: .standard)
        logLines.append("\(timestamp)  \(message)")
        if logLines.count > 120 {
            logLines.removeFirst(logLines.count - 120)
        }
    }

    private func characteristic(_ uuid: String) -> CBCharacteristic? {
        characteristics[normalized(uuid)]
    }

    private func normalized(_ uuid: String) -> String {
        uuid.lowercased()
    }

    private func normalized(_ uuid: CBUUID) -> String {
        uuid.uuidString.lowercased()
    }
}

extension CameraBLEManager: CBCentralManagerDelegate {
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        switch central.state {
        case .poweredOn:
            appendLog("Bluetooth powered on")
            if state == .bluetoothUnavailable {
                state = .idle
            }
        case .poweredOff, .unauthorized, .unsupported, .resetting, .unknown:
            state = .bluetoothUnavailable
            appendLog("Bluetooth state changed: \(central.state.rawValue)")
        @unknown default:
            state = .bluetoothUnavailable
        }
    }

    func centralManager(
        _ central: CBCentralManager,
        didDiscover peripheral: CBPeripheral,
        advertisementData: [String: Any],
        rssi RSSI: NSNumber
    ) {
        let localName = advertisementData[CBAdvertisementDataLocalNameKey] as? String
        let name = peripheral.name ?? localName ?? ""
        let manufacturerData = advertisementData[CBAdvertisementDataManufacturerDataKey] as? Data
        let info = SonyProtocol.parseAdvertisement(manufacturerData: manufacturerData)
        let matchesName = name.localizedCaseInsensitiveContains(targetName) || name.localizedCaseInsensitiveContains("ILCE-")
        let matchesSonyCamera = info?.isCamera == true

        guard matchesName || matchesSonyCamera else { return }

        discoveredCameraName = name.isEmpty ? "Sony camera" : name
        appendLog("Found \(discoveredCameraName ?? "Sony camera") RSSI=\(RSSI)")
        if let info {
            appendLog("Sony protocolVersion=\(info.protocolVersion.map(String.init) ?? "unknown")")
        }
        state = .connecting
        central.stopScan()
        self.peripheral = peripheral
        central.connect(peripheral)
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        appendLog("Connected")
        beginServiceDiscovery(for: peripheral)
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        fail(error?.localizedDescription ?? "Failed to connect")
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        appendLog("Disconnected")
        self.peripheral = nil
        if let error, state != .stopped {
            fail(error.localizedDescription)
        } else if state != .stopped {
            state = .idle
        }
    }
}

extension CameraBLEManager: CBPeripheralDelegate {
    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        if let error {
            fail(error.localizedDescription)
            return
        }
        for service in peripheral.services ?? [] {
            let characteristicUUIDs = [
                SonyProtocol.locationStatusNotifyUUID,
                SonyProtocol.locationDataWriteUUID,
                SonyProtocol.locationConfigReadUUID,
                SonyProtocol.locationLockUUID,
                SonyProtocol.locationEnableUUID,
                SonyProtocol.timeCorrectionUUID,
                SonyProtocol.areaAdjustmentUUID,
                SonyProtocol.pairingInitUUID,
            ].map(CBUUID.init(string:))
            peripheral.discoverCharacteristics(characteristicUUIDs, for: service)
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        if let error {
            fail(error.localizedDescription)
            return
        }
        for characteristic in service.characteristics ?? [] {
            characteristics[normalized(characteristic.uuid)] = characteristic
            appendLog("Characteristic \(characteristic.uuid.uuidString) props=\(characteristic.properties.rawValue)")
        }
        maybeBeginLocationSetup()
    }

    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        guard case let .write(name, uuid, required) = pendingOperation,
              uuid == normalized(characteristic.uuid)
        else {
            return
        }
        completeOperation(name: name, error: error?.localizedDescription, required: required)
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        let characteristicUUID = normalized(characteristic.uuid)
        if case let .read(name, uuid, required, onValue) = pendingOperation, uuid == characteristicUUID {
            if let error {
                completeOperation(name: name, error: error.localizedDescription, required: required)
                return
            }
            let data = characteristic.value ?? Data()
            appendLog("\(name) value=\(SonyProtocol.hex(data))")
            onValue?(data)
            completeOperation(name: name, error: nil, required: required)
            return
        }

        if characteristicUUID == normalized(SonyProtocol.locationStatusNotifyUUID), let data = characteristic.value {
            appendLog("DD01 notify \(SonyProtocol.hex(data))")
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
        let characteristicUUID = normalized(characteristic.uuid)
        if case let .notify(name, uuid, required, enabled) = pendingOperation, uuid == characteristicUUID {
            if let error {
                completeOperation(name: name, error: error.localizedDescription, required: required)
                return
            }
            guard characteristic.isNotifying == enabled else {
                completeOperation(name: name, error: "Notify state mismatch", required: required)
                return
            }
            completeOperation(name: name, error: nil, required: required)
            return
        }

        if let error {
            appendLog("Notify state failed for \(characteristic.uuid.uuidString): \(error.localizedDescription)")
        } else {
            appendLog("Notify state \(characteristic.uuid.uuidString) isNotifying=\(characteristic.isNotifying)")
        }
    }
}

private struct QueuedBLEOperation {
    let name: String
    let required: Bool
    let start: () -> Void
}

private enum PendingBLEOperation {
    case write(name: String, uuid: String, required: Bool)
    case read(name: String, uuid: String, required: Bool, onValue: ((Data) -> Void)?)
    case notify(name: String, uuid: String, required: Bool, enabled: Bool)

    var name: String {
        switch self {
        case let .write(name, _, _), let .read(name, _, _, _), let .notify(name, _, _, _):
            name
        }
    }

    var required: Bool {
        switch self {
        case let .write(_, _, required), let .read(_, _, required, _), let .notify(_, _, required, _):
            required
        }
    }
}
