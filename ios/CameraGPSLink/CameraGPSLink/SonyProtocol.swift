import Foundation

enum SonyProtocol {
    static let sonyCompanyID = 0x012D
    static let sonyCameraDeviceType = 0x0003
    static let protocolVersionRequiresUnlock = 65
    static let coordinateScale = 10_000_000.0

    static let remoteControlServiceUUID = "8000ff00-ff00-ffff-ffff-ffffffffffff"
    static let cameraControlServiceUUID = "8000cc00-cc00-ffff-ffff-ffffffffffff"
    static let locationServiceUUID = "8000dd00-dd00-ffff-ffff-ffffffffffff"
    static let pairingServiceUUID = "8000ee00-ee00-ffff-ffff-ffffffffffff"

    static let locationStatusNotifyUUID = "0000dd01-0000-1000-8000-00805f9b34fb"
    static let locationDataWriteUUID = "0000dd11-0000-1000-8000-00805f9b34fb"
    static let locationConfigReadUUID = "0000dd21-0000-1000-8000-00805f9b34fb"
    static let locationLockUUID = "0000dd30-0000-1000-8000-00805f9b34fb"
    static let locationEnableUUID = "0000dd31-0000-1000-8000-00805f9b34fb"
    static let timeCorrectionUUID = "0000dd32-0000-1000-8000-00805f9b34fb"
    static let areaAdjustmentUUID = "0000dd33-0000-1000-8000-00805f9b34fb"
    static let pairingInitUUID = "0000ee01-0000-1000-8000-00805f9b34fb"

    static let pairingInitPayload = Data([0x06, 0x08, 0x01, 0x00, 0x00, 0x00, 0x00])

    static let locationPacketSizeWithoutTimezone = 91
    static let locationPacketSizeWithTimezone = 95
    static let timeAreaPacketSize = 13

    struct AdvertisementInfo: Equatable {
        let isCamera: Bool
        let protocolVersion: Int?
        let requiresUnlock: Bool?
    }

    enum ProtocolError: Error, LocalizedError {
        case invalidLatitude(Double)
        case invalidLongitude(Double)

        var errorDescription: String? {
            switch self {
            case let .invalidLatitude(value):
                "Latitude out of range: \(value)"
            case let .invalidLongitude(value):
                "Longitude out of range: \(value)"
            }
        }
    }

    static func parseAdvertisement(manufacturerData: Data?) -> AdvertisementInfo? {
        guard let payload = sonyPayload(from: manufacturerData), payload.count >= 2 else {
            return nil
        }

        let deviceType = Int(payload[0]) | (Int(payload[1]) << 8)
        let protocolVersion = payload.count >= 4 ? Int(payload[2]) : nil
        return AdvertisementInfo(
            isCamera: deviceType == sonyCameraDeviceType,
            protocolVersion: protocolVersion,
            requiresUnlock: protocolVersion.map { $0 >= protocolVersionRequiresUnlock }
        )
    }

    static func parseConfigRequiresTimezone(_ data: Data) -> Bool {
        guard data.count >= 5 else { return false }
        return (data[4] & 0x02) == 0x02
    }

    static func encodeLocationPacket(
        latitude: Double,
        longitude: Double,
        date: Date = Date(),
        timeZone: TimeZone = .current,
        includeTimezone: Bool = true
    ) throws -> Data {
        guard (-90.0 ... 90.0).contains(latitude) else {
            throw ProtocolError.invalidLatitude(latitude)
        }
        guard (-180.0 ... 180.0).contains(longitude) else {
            throw ProtocolError.invalidLongitude(longitude)
        }

        let packetSize = includeTimezone ? locationPacketSizeWithTimezone : locationPacketSizeWithoutTimezone
        let payloadSize = packetSize - 2
        var packet = [UInt8](repeating: 0, count: packetSize)

        writeUInt16BE(UInt16(payloadSize), into: &packet, at: 0)
        packet[2] = 0x08
        packet[3] = 0x02
        packet[4] = 0xFC
        packet[5] = includeTimezone ? 0x03 : 0x00
        packet[8] = 0x10
        packet[9] = 0x10
        packet[10] = 0x10

        writeInt32BE(Int32(latitude * coordinateScale), into: &packet, at: 11)
        writeInt32BE(Int32(longitude * coordinateScale), into: &packet, at: 15)

        var utcCalendar = Calendar(identifier: .gregorian)
        utcCalendar.timeZone = TimeZone(secondsFromGMT: 0)!
        let utcComponents = utcCalendar.dateComponents([.year, .month, .day, .hour, .minute, .second], from: date)
        writeUInt16BE(UInt16(utcComponents.year ?? 0), into: &packet, at: 19)
        packet[21] = UInt8(utcComponents.month ?? 0)
        packet[22] = UInt8(utcComponents.day ?? 0)
        packet[23] = UInt8(utcComponents.hour ?? 0)
        packet[24] = UInt8(utcComponents.minute ?? 0)
        packet[25] = UInt8(utcComponents.second ?? 0)

        if includeTimezone {
            let actualOffsetSeconds = timeZone.secondsFromGMT(for: date)
            let dstOffsetSeconds = Int(timeZone.daylightSavingTimeOffset(for: date))
            let standardOffsetMinutes = (actualOffsetSeconds - dstOffsetSeconds) / 60
            let dstOffsetMinutes = dstOffsetSeconds / 60
            writeInt16BE(Int16(standardOffsetMinutes), into: &packet, at: 91)
            writeInt16BE(Int16(dstOffsetMinutes), into: &packet, at: 93)
        }

        return Data(packet)
    }

    static func encodeTimeAreaPacket(date: Date = Date(), timeZone: TimeZone = .current) -> Data {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let components = calendar.dateComponents([.year, .month, .day, .hour, .minute, .second], from: date)
        let offset = splitTimezoneOffset(seconds: timeZone.secondsFromGMT(for: date))
        let isDST = timeZone.isDaylightSavingTime(for: date)
        var packet = [UInt8](repeating: 0, count: timeAreaPacketSize)

        packet[0] = 0x0C
        packet[1] = 0x00
        packet[2] = 0x00
        writeUInt16BE(UInt16(components.year ?? 0), into: &packet, at: 3)
        packet[5] = UInt8(components.month ?? 0)
        packet[6] = UInt8(components.day ?? 0)
        packet[7] = UInt8(components.hour ?? 0)
        packet[8] = UInt8(components.minute ?? 0)
        packet[9] = UInt8(components.second ?? 0)
        packet[10] = isDST ? 0x01 : 0x00
        packet[11] = UInt8(bitPattern: Int8(offset.hours))
        packet[12] = UInt8(offset.minutes)

        return Data(packet)
    }

    static func hex(_ data: Data) -> String {
        data.map { String(format: "%02x", $0) }.joined(separator: " ")
    }

    private static func sonyPayload(from manufacturerData: Data?) -> Data? {
        guard let manufacturerData else { return nil }
        if manufacturerData.count >= 4 {
            let companyID = Int(manufacturerData[0]) | (Int(manufacturerData[1]) << 8)
            if companyID == sonyCompanyID {
                return Data(manufacturerData.dropFirst(2))
            }
        }
        return manufacturerData
    }

    private static func splitTimezoneOffset(seconds: Int) -> (hours: Int, minutes: Int) {
        let sign = seconds < 0 ? -1 : 1
        let absoluteSeconds = abs(seconds)
        return (sign * (absoluteSeconds / 3600), (absoluteSeconds % 3600) / 60)
    }

    private static func writeUInt16BE(_ value: UInt16, into buffer: inout [UInt8], at offset: Int) {
        buffer[offset] = UInt8((value >> 8) & 0xFF)
        buffer[offset + 1] = UInt8(value & 0xFF)
    }

    private static func writeInt16BE(_ value: Int16, into buffer: inout [UInt8], at offset: Int) {
        writeUInt16BE(UInt16(bitPattern: value), into: &buffer, at: offset)
    }

    private static func writeInt32BE(_ value: Int32, into buffer: inout [UInt8], at offset: Int) {
        let unsignedValue = UInt32(bitPattern: value)
        buffer[offset] = UInt8((unsignedValue >> 24) & 0xFF)
        buffer[offset + 1] = UInt8((unsignedValue >> 16) & 0xFF)
        buffer[offset + 2] = UInt8((unsignedValue >> 8) & 0xFF)
        buffer[offset + 3] = UInt8(unsignedValue & 0xFF)
    }
}
