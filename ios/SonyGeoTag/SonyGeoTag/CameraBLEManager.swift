import Combine
import CoreBluetooth
import CoreLocation
import Foundation
import OSLog

final class CameraBLEManager: NSObject, ObservableObject {
    @Published private(set) var state: CameraConnectionState = .idle
    @Published private(set) var discoveredCameraName: String?
    @Published private(set) var lastSentAt: Date?
    @Published private(set) var packetsSent = 0
    @Published private(set) var includeTimezone = true
    @Published private(set) var dd21ConfigHex: String?
    @Published private(set) var lastError: String?
    @Published private(set) var logLines: [String] = []
    @Published private(set) var backgroundLinkEnabled = UserDefaults.standard.bool(
        forKey: CameraBLEDefaults.backgroundLinkEnabled
    )
    @Published private(set) var lowPowerModeEnabled = UserDefaults.standard.object(
        forKey: CameraBLEDefaults.lowPowerModeEnabled
    ) as? Bool ?? true
    @Published private(set) var rememberedPeripheralID = UserDefaults.standard.string(
        forKey: CameraBLEDefaults.rememberedPeripheralID
    )
    @Published private(set) var pendingReconnectArmed = false

    var targetName = "ILCE-7CM2"
    var updateInterval: TimeInterval = CameraBLEDefaults.foregroundUpdateInterval

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
    private var resumeWhenBluetoothPowersOn = false
    private var manualStopRequested = false
    private var reconnectRetryTimer: Timer?
    private let operationTimeout: TimeInterval = 12
    private let logger = Logger(subsystem: "com.narumi.SonyGeoTag", category: "BLE")
    private let connectOptions: [String: Any] = [
        CBConnectPeripheralOptionNotifyOnConnectionKey: true,
        CBConnectPeripheralOptionNotifyOnDisconnectionKey: true,
        CBConnectPeripheralOptionNotifyOnNotificationKey: true,
    ]

    override init() {
        super.init()
        updateInterval = lowPowerModeEnabled
            ? CameraBLEDefaults.lowPowerUpdateInterval
            : CameraBLEDefaults.foregroundUpdateInterval
        centralManager = CBCentralManager(
            delegate: self,
            queue: nil,
            options: [CBCentralManagerOptionRestoreIdentifierKey: CameraBLEDefaults.restorationIdentifier]
        )
    }

    var canStart: Bool {
        state != .scanning && state != .connecting && state != .discovering && state != .enablingLocation && state != .linked
    }

    func configure(backgroundLinkEnabled: Bool, lowPowerModeEnabled: Bool) {
        let didChange = self.backgroundLinkEnabled != backgroundLinkEnabled
            || self.lowPowerModeEnabled != lowPowerModeEnabled
        self.backgroundLinkEnabled = backgroundLinkEnabled
        self.lowPowerModeEnabled = lowPowerModeEnabled
        updateInterval = lowPowerModeEnabled
            ? CameraBLEDefaults.lowPowerUpdateInterval
            : CameraBLEDefaults.foregroundUpdateInterval
        UserDefaults.standard.set(backgroundLinkEnabled, forKey: CameraBLEDefaults.backgroundLinkEnabled)
        UserDefaults.standard.set(lowPowerModeEnabled, forKey: CameraBLEDefaults.lowPowerModeEnabled)

        if didChange {
            appendLog(
                "BLE settings: backgroundLink=\(backgroundLinkEnabled) lowPower=\(lowPowerModeEnabled) interval=\(Int(updateInterval))s"
            )
        }
        if state == .linked {
            restartSendTimer()
        }
        if backgroundLinkEnabled, canStart, locationProvider != nil {
            armBackgroundReconnect(reason: "Background Link setting enabled")
        }
        if !backgroundLinkEnabled {
            disarmPendingReconnect()
        }
    }

    func setLocationProvider(_ locationProvider: @escaping () -> CLLocation?) {
        self.locationProvider = locationProvider
        if backgroundLinkEnabled, canStart {
            armBackgroundReconnect(reason: "Location provider became available")
        }
    }

    func startLink(locationProvider: @escaping () -> CLLocation?) {
        setLocationProvider(locationProvider)
        manualStopRequested = false
        prepareForNewSession(resetCounters: true)
        appendLog("Starting Sony location link")

        guard centralManager.state == .poweredOn else {
            resumeWhenBluetoothPowersOn = backgroundLinkEnabled
            state = .bluetoothUnavailable
            appendLog("Bluetooth is not powered on: \(centralManager.state.rawValue)")
            return
        }
        connectToRememberedCameraOrScan()
    }

    func resumeBackgroundLink(locationProvider: @escaping () -> CLLocation?) {
        setLocationProvider(locationProvider)
        guard backgroundLinkEnabled else { return }
        guard canStart else { return }
        manualStopRequested = false
        prepareForNewSession(resetCounters: false)
        appendLog("Background link enabled; attempting camera reconnect")
        armBackgroundReconnect(reason: "Background Link resume")
    }

    func stopLink() {
        appendLog("Stopping Sony location link")
        manualStopRequested = true
        resumeWhenBluetoothPowersOn = false
        centralManager.stopScan()
        disarmPendingReconnect()
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

    private func prepareForNewSession(resetCounters: Bool) {
        if resetCounters {
            packetsSent = 0
            lastSentAt = nil
        }
        lastError = nil
        dd21ConfigHex = nil
        characteristics.removeAll()
        didStartLocationSetup = false
        stopTimer()
        stopOperationTimeout()
        reconnectRetryTimer?.invalidate()
        reconnectRetryTimer = nil
        operationQueue.removeAll()
        pendingOperation = nil
        onQueueEmpty = nil
    }

    private func connectToRememberedCameraOrScan() {
        if connectToRememberedCamera(reason: "remembered camera reconnect") {
            return
        }
        scanForCamera()
    }

    @discardableResult
    private func armBackgroundReconnect(reason: String) -> Bool {
        guard backgroundLinkEnabled, !manualStopRequested else { return false }
        guard centralManager.state == .poweredOn else {
            resumeWhenBluetoothPowersOn = true
            state = .bluetoothUnavailable
            appendLog("Waiting for Bluetooth before pending reconnect: \(centralManager.state.rawValue)")
            return false
        }
        if connectToRememberedCamera(reason: reason) {
            return true
        }
        appendLog("No remembered camera for pending reconnect; falling back to scan")
        scanForCamera()
        return false
    }

    private func connectToRememberedCamera(reason: String) -> Bool {
        guard let rememberedPeripheralID,
              let identifier = UUID(uuidString: rememberedPeripheralID)
        else {
            return false
        }
        let peripherals = centralManager.retrievePeripherals(withIdentifiers: [identifier])
        guard let rememberedPeripheral = peripherals.first else {
            appendLog("Remembered camera not available for direct reconnect")
            return false
        }

        switch rememberedPeripheral.state {
        case .connected:
            appendLog("Remembered camera already connected; discovering services")
            pendingReconnectArmed = false
            peripheral = rememberedPeripheral
            beginServiceDiscovery(for: rememberedPeripheral)
        case .connecting:
            appendLog("Pending reconnect already armed for remembered camera")
            pendingReconnectArmed = true
            state = .connecting
            peripheral = rememberedPeripheral
        case .disconnected, .disconnecting:
            appendLog("Arming pending reconnect to remembered camera \(identifier.uuidString) (\(reason))")
            pendingReconnectArmed = true
            state = .connecting
            peripheral = rememberedPeripheral
            rememberedPeripheral.delegate = self
            centralManager.connect(rememberedPeripheral, options: connectOptions)
        @unknown default:
            appendLog("Arming pending reconnect to remembered camera \(identifier.uuidString) (\(reason))")
            pendingReconnectArmed = true
            state = .connecting
            peripheral = rememberedPeripheral
            rememberedPeripheral.delegate = self
            centralManager.connect(rememberedPeripheral, options: connectOptions)
        }
        return true
    }

    private func disarmPendingReconnect() {
        reconnectRetryTimer?.invalidate()
        reconnectRetryTimer = nil
        pendingReconnectArmed = false
    }

    private func scheduleReconnectRetry(reason: String) {
        guard backgroundLinkEnabled, !manualStopRequested else { return }
        reconnectRetryTimer?.invalidate()
        let retryInterval = lowPowerModeEnabled
            ? CameraBLEDefaults.lowPowerReconnectRetryInterval
            : CameraBLEDefaults.foregroundReconnectRetryInterval
        appendLog("Scheduling pending reconnect retry in \(Int(retryInterval))s (\(reason))")
        reconnectRetryTimer = Timer.scheduledTimer(withTimeInterval: retryInterval, repeats: false) { [weak self] _ in
            self?.armBackgroundReconnect(reason: "scheduled retry")
        }
    }

    private func scanForCamera() {
        state = .scanning
        let mode = backgroundLinkEnabled ? "background-capable" : "foreground"
        appendLog("Scanning for \(targetName) (\(mode); iOS may throttle background scans)")
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
        appendLog(
            "Location link active; interval=\(Int(updateInterval))s; send photos only after DD11 location OK / Packets sent > 0"
        )
        sendLocationOnce()
        restartSendTimer()
    }

    private func restartSendTimer() {
        stopTimer()
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
        let line = "\(timestamp)  \(message)"
        logLines.append(line)
        print(line)
        logger.info("\(line, privacy: .public)")
        if logLines.count > 120 {
            logLines.removeFirst(logLines.count - 120)
        }
    }

    private func remember(peripheral: CBPeripheral) {
        let identifier = peripheral.identifier.uuidString
        guard rememberedPeripheralID != identifier else { return }
        rememberedPeripheralID = identifier
        UserDefaults.standard.set(identifier, forKey: CameraBLEDefaults.rememberedPeripheralID)
        appendLog("Remembered camera peripheral \(identifier)")
    }

    private func characteristic(_ uuid: String) -> CBCharacteristic? {
        characteristics[normalized(uuid)]
    }

    private func normalized(_ uuid: String) -> String {
        let lowercased = uuid.lowercased()
        let bluetoothBaseSuffix = "-0000-1000-8000-00805f9b34fb"
        if lowercased.hasPrefix("0000"), lowercased.hasSuffix(bluetoothBaseSuffix) {
            return String(lowercased.dropFirst(4).prefix(4))
        }
        return lowercased
    }

    private func normalized(_ uuid: CBUUID) -> String {
        normalized(uuid.uuidString)
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
            if resumeWhenBluetoothPowersOn || (backgroundLinkEnabled && canStart) {
                resumeWhenBluetoothPowersOn = false
                guard locationProvider != nil else {
                    appendLog("Background link waiting for location provider")
                    return
                }
                armBackgroundReconnect(reason: "Bluetooth powered on")
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
        pendingReconnectArmed = false
        central.stopScan()
        self.peripheral = peripheral
        remember(peripheral: peripheral)
        central.connect(peripheral, options: connectOptions)
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        appendLog("Connected")
        pendingReconnectArmed = false
        reconnectRetryTimer?.invalidate()
        reconnectRetryTimer = nil
        remember(peripheral: peripheral)
        beginServiceDiscovery(for: peripheral)
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        appendLog(error?.localizedDescription ?? "Failed to connect")
        pendingReconnectArmed = false
        self.peripheral = nil
        guard backgroundLinkEnabled, !manualStopRequested else {
            fail(error?.localizedDescription ?? "Failed to connect")
            return
        }
        scheduleReconnectRetry(reason: error?.localizedDescription ?? "connect failed")
        scanForCamera()
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        appendLog("Disconnected")
        self.peripheral = nil
        stopTimer()
        stopOperationTimeout()
        operationQueue.removeAll()
        pendingOperation = nil
        characteristics.removeAll()
        didStartLocationSetup = false

        guard state != .stopped, state != .stopping else { return }
        guard backgroundLinkEnabled, !manualStopRequested else {
            if let error {
                fail(error.localizedDescription)
            } else {
                state = .idle
            }
            return
        }

        appendLog("Background link will arm pending reconnect after disconnect")
        armBackgroundReconnect(reason: "peripheral disconnected")
    }

    func centralManager(_ central: CBCentralManager, willRestoreState dict: [String: Any]) {
        appendLog("CoreBluetooth restored state")
        if let peripherals = dict[CBCentralManagerRestoredStatePeripheralsKey] as? [CBPeripheral],
           let restoredPeripheral = peripherals.first {
            appendLog("Restored peripheral \(restoredPeripheral.identifier.uuidString) state=\(restoredPeripheral.state.rawValue)")
            peripheral = restoredPeripheral
            restoredPeripheral.delegate = self
            remember(peripheral: restoredPeripheral)
            if restoredPeripheral.state == .connected {
                pendingReconnectArmed = false
                beginServiceDiscovery(for: restoredPeripheral)
            } else if backgroundLinkEnabled {
                pendingReconnectArmed = true
                state = .connecting
                central.connect(restoredPeripheral, options: connectOptions)
            }
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

private enum CameraBLEDefaults {
    static let restorationIdentifier = "com.narumi.SonyGeoTag.central"
    static let backgroundLinkEnabled = "backgroundLinkEnabled"
    static let lowPowerModeEnabled = "lowPowerModeEnabled"
    static let rememberedPeripheralID = "rememberedPeripheralID"
    static let foregroundUpdateInterval: TimeInterval = 30
    static let lowPowerUpdateInterval: TimeInterval = 120
    static let foregroundReconnectRetryInterval: TimeInterval = 30
    static let lowPowerReconnectRetryInterval: TimeInterval = 120
}
