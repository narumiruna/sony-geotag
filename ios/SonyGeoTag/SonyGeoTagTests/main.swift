import Foundation

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        fputs("Smoke test failed: \(message)\n", stderr)
        exit(1)
    }
}

let advertisement = Data([0x2D, 0x01, 0x03, 0x00, 0x65, 0x00, 0x55, 0x31])
let info = SonyProtocol.parseAdvertisement(manufacturerData: advertisement)
require(info?.isCamera == true, "Sony camera advertisement should be recognized")
require(info?.protocolVersion == 0x65, "Protocol version should be 0x65")
require(info?.requiresUnlock == true, "A7C II protocol version should require DD30/DD31")
require(SonyProtocol.parseConfigRequiresTimezone(Data([0x06, 0x10, 0x00, 0x9C, 0x02, 0x00, 0x00])), "DD21 bit should enable timezone")

var calendar = Calendar(identifier: .gregorian)
let packetTimeZone = TimeZone(secondsFromGMT: 3 * 3600)!
calendar.timeZone = packetTimeZone
let date = calendar.date(from: DateComponents(year: 2026, month: 7, day: 9, hour: 20, minute: 34, second: 56))!
let packet = try SonyProtocol.encodeLocationPacket(
    latitude: 40.614380674810334,
    longitude: 22.971624208899676,
    date: date,
    timeZone: packetTimeZone,
    includeTimezone: true
)

require(packet.count == 95, "Timezone-capable DD11 packet should be 95 bytes")
require(packet.prefix(6) == Data([0x00, 0x5D, 0x08, 0x02, 0xFC, 0x03]), "DD11 header should match")
require(packet[6..<11] == Data([0x00, 0x00, 0x10, 0x10, 0x10]), "DD11 padding should match")
require(packet[19..<26] == Data([0x07, 0xEA, 0x07, 0x09, 0x11, 0x22, 0x38]), "DD11 timestamp should be UTC")
require(packet[91..<95] == Data([0x00, 0xB4, 0x00, 0x00]), "DD11 timezone should be +180 minutes, no DST")

let noTimezonePacket = try SonyProtocol.encodeLocationPacket(
    latitude: -33.5,
    longitude: 151.2,
    date: date,
    timeZone: packetTimeZone,
    includeTimezone: false
)
require(noTimezonePacket.count == 91, "Non-timezone DD11 packet should be 91 bytes")
require(noTimezonePacket.prefix(6) == Data([0x00, 0x59, 0x08, 0x02, 0xFC, 0x00]), "91-byte DD11 header should match")

print("SonyProtocol smoke test passed")
