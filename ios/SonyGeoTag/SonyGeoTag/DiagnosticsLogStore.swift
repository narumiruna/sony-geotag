import Foundation

final class DiagnosticsLogStore: ObservableObject {
    @Published private(set) var lines: [String] = []
    let capacity: Int

    init(capacity: Int = 120) {
        self.capacity = max(1, capacity)
    }

    func append(_ line: String) {
        lines.append(line)
        if lines.count > capacity {
            lines.removeFirst(lines.count - capacity)
        }
    }

    func removeAll() {
        lines.removeAll()
    }

    var copyText: String {
        lines.joined(separator: "\n")
    }
}
