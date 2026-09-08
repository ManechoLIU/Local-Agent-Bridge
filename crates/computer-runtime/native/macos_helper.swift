import AppKit
import ApplicationServices
import CoreGraphics
import CryptoKit
import Foundation

let maxScreenshotBytes = 8 * 1024 * 1024
let maxInputBytes = 16 * 1024
let maxFindNodes = 3000
let maxAxDepth = 16

func emit(_ payload: [String: Any]) -> Never {
    let data = try! JSONSerialization.data(withJSONObject: payload, options: [])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
    exit(0)
}

func fail(_ message: String) -> Never {
    emit(["ok": false, "error": message])
}

func boolEnv(_ name: String) -> Bool {
    guard let value = ProcessInfo.processInfo.environment[name]?.lowercased() else { return false }
    return ["1", "true", "yes"].contains(value)
}

struct WindowRecord {
    let windowID: String
    let cgWindowID: CGWindowID
    let pid: Int32
    let owner: String
    let title: String
    let bounds: CGRect
    let frontmost: Bool
}

func appRecord(_ app: NSRunningApplication) -> [String: Any] {
    [
        "pid": Int(app.processIdentifier),
        "bundle_id": app.bundleIdentifier ?? "",
        "display_name": app.localizedName ?? "",
        "is_active": app.isActive,
        "is_hidden": app.isHidden,
        "is_terminated": app.isTerminated
    ]
}

func listApps() -> [[String: Any]] {
    NSWorkspace.shared.runningApplications
        .filter { $0.activationPolicy == .regular && !$0.isTerminated }
        .map(appRecord)
        .sorted { lhs, rhs in
            let a = (lhs["display_name"] as? String) ?? ""
            let b = (rhs["display_name"] as? String) ?? ""
            return a.localizedCaseInsensitiveCompare(b) == .orderedAscending
        }
}

func windowRecords() -> [WindowRecord] {
    let options: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    guard let raw = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] else {
        return []
    }
    let frontPid = NSWorkspace.shared.frontmostApplication?.processIdentifier
    return raw.compactMap { info in
        guard let number = info[kCGWindowNumber as String] as? NSNumber,
              let ownerPid = info[kCGWindowOwnerPID as String] as? NSNumber,
              let owner = info[kCGWindowOwnerName as String] as? String,
              let boundsDict = info[kCGWindowBounds as String] as? [String: Any],
              let bounds = CGRect(dictionaryRepresentation: boundsDict as CFDictionary)
        else { return nil }
        let layer = (info[kCGWindowLayer as String] as? NSNumber)?.intValue ?? 0
        if layer != 0 || bounds.width <= 0 || bounds.height <= 0 { return nil }
        let pid = ownerPid.int32Value
        return WindowRecord(
            windowID: "wid_\(number.intValue)",
            cgWindowID: CGWindowID(number.uint32Value),
            pid: pid,
            owner: owner,
            title: (info[kCGWindowName as String] as? String) ?? "",
            bounds: bounds,
            frontmost: frontPid == pid
        )
    }
}

func windowDictionary(_ record: WindowRecord) -> [String: Any] {
    [
        "window_id": record.windowID,
        "cg_window_id": Int(record.cgWindowID),
        "pid": Int(record.pid),
        "owner": record.owner,
        "title": record.title,
        "focused": record.frontmost,
        "bounds": [
            "x": record.bounds.origin.x,
            "y": record.bounds.origin.y,
            "width": record.bounds.size.width,
            "height": record.bounds.size.height
        ],
        "coordinate_space": "logical_points_global"
    ]
}

func listWindows() -> [[String: Any]] {
    windowRecords().map(windowDictionary)
}

func targetWindow(_ windowID: String, _ pid: Int32) -> WindowRecord {
    guard let record = windowRecords().first(where: { $0.windowID == windowID && $0.pid == pid }) else {
        fail("target window no longer matches observed window identity")
    }
    return record
}

func requireAccessibility() {
    if !AXIsProcessTrusted() { fail("accessibility permission is required") }
}

func requireScreenRecording() {
    if !CGPreflightScreenCaptureAccess() { fail("screen recording permission is required") }
}

func readStdin(maxBytes: Int) -> Data {
    let data = FileHandle.standardInput.readDataToEndOfFile()
    if data.count > maxBytes { fail("stdin payload exceeds limit") }
    return data
}

func axAttribute(_ element: AXUIElement, _ attribute: CFString) -> CFTypeRef? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, attribute, &value) == .success else { return nil }
    return value
}

func asAXElement(_ value: CFTypeRef?) -> AXUIElement? {
    guard let value, CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
    return unsafeBitCast(value, to: AXUIElement.self)
}

func asAXValue(_ value: CFTypeRef?) -> AXValue? {
    guard let value, CFGetTypeID(value) == AXValueGetTypeID() else { return nil }
    return unsafeBitCast(value, to: AXValue.self)
}

func axString(_ element: AXUIElement, _ attribute: CFString) -> String {
    (axAttribute(element, attribute) as? String) ?? ""
}

func axChildren(_ element: AXUIElement) -> [AXUIElement] {
    (axAttribute(element, kAXChildrenAttribute as CFString) as? [AXUIElement]) ?? []
}

func axPoint(_ element: AXUIElement, _ attribute: CFString) -> CGPoint? {
    guard let value = asAXValue(axAttribute(element, attribute)),
          AXValueGetType(value) == .cgPoint
    else { return nil }
    var point = CGPoint.zero
    return AXValueGetValue(value, .cgPoint, &point) ? point : nil
}

func axSize(_ element: AXUIElement, _ attribute: CFString) -> CGSize? {
    guard let value = asAXValue(axAttribute(element, attribute)),
          AXValueGetType(value) == .cgSize
    else { return nil }
    var size = CGSize.zero
    return AXValueGetValue(value, .cgSize, &size) ? size : nil
}

func axFrame(_ element: AXUIElement) -> CGRect? {
    guard let point = axPoint(element, kAXPositionAttribute as CFString),
          let size = axSize(element, kAXSizeAttribute as CFString)
    else { return nil }
    return CGRect(origin: point, size: size)
}

func approximatelyEqual(_ left: CGFloat, _ right: CGFloat, tolerance: CGFloat = 4) -> Bool {
    abs(left - right) <= tolerance
}

func windowMatches(_ axWindow: AXUIElement, _ target: WindowRecord) -> Bool {
    guard let frame = axFrame(axWindow) else { return false }
    let boundsMatch =
        approximatelyEqual(frame.origin.x, target.bounds.origin.x) &&
        approximatelyEqual(frame.origin.y, target.bounds.origin.y) &&
        approximatelyEqual(frame.size.width, target.bounds.size.width) &&
        approximatelyEqual(frame.size.height, target.bounds.size.height)
    if !boundsMatch { return false }
    let title = axString(axWindow, kAXTitleAttribute as CFString)
    return target.title.isEmpty || title.isEmpty || title == target.title
}

func targetAXWindow(_ target: WindowRecord) -> AXUIElement {
    let app = AXUIElementCreateApplication(pid_t(target.pid))
    guard let windows = axAttribute(app, kAXWindowsAttribute as CFString) as? [AXUIElement],
          let match = windows.first(where: { windowMatches($0, target) })
    else {
        fail("Accessibility window no longer matches observed target")
    }
    return match
}

func requireFocusedTargetWindow(_ target: WindowRecord) -> AXUIElement {
    guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid_t(target.pid) else {
        fail("target application is not frontmost")
    }
    let app = AXUIElementCreateApplication(pid_t(target.pid))
    guard let focused = asAXElement(axAttribute(app, kAXFocusedWindowAttribute as CFString)),
          windowMatches(focused, target)
    else {
        fail("target window is not the focused Accessibility window")
    }
    return focused
}

func normalizedText(_ value: String) -> String {
    value.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: .current)
}

func boundedLabel(_ value: String) -> String {
    let compact = value.replacingOccurrences(of: "\n", with: " ")
        .replacingOccurrences(of: "\r", with: " ")
        .trimmingCharacters(in: .whitespacesAndNewlines)
    return String(compact.prefix(256))
}

func elementFingerprint(_ element: AXUIElement) -> String {
    let role = axString(element, kAXRoleAttribute as CFString)
    let title = axString(element, kAXTitleAttribute as CFString)
    let description = axString(element, kAXDescriptionAttribute as CFString)
    let frame = axFrame(element) ?? .zero
    let source = "\(role)|\(title)|\(description)|\(Int(frame.origin.x.rounded()))|\(Int(frame.origin.y.rounded()))|\(Int(frame.width.rounded()))|\(Int(frame.height.rounded()))"
    let digest = SHA256.hash(data: Data(source.utf8))
    return digest.prefix(12).map { String(format: "%02x", $0) }.joined()
}

func elementID(path: [Int], element: AXUIElement) -> String {
    let pathText = path.map(String.init).joined(separator: ".")
    return "axp:\(pathText):\(elementFingerprint(element))"
}

func elementRecord(_ element: AXUIElement, path: [Int], target: WindowRecord) -> [String: Any] {
    let role = axString(element, kAXRoleAttribute as CFString)
    let title = boundedLabel(axString(element, kAXTitleAttribute as CFString))
    let description = boundedLabel(axString(element, kAXDescriptionAttribute as CFString))
    let frame = axFrame(element) ?? .zero
    return [
        "element_id": elementID(path: path, element: element),
        "window_id": target.windowID,
        "pid": Int(target.pid),
        "role": role,
        "label": !title.isEmpty ? title : description,
        "bounds": [
            "x": frame.origin.x,
            "y": frame.origin.y,
            "width": frame.width,
            "height": frame.height
        ]
    ]
}

func findElements(target: WindowRecord, query: String, maxResults: Int) -> [[String: Any]] {
    let root = targetAXWindow(target)
    let needle = normalizedText(query)
    var results: [[String: Any]] = []
    var visited = 0

    func visit(_ element: AXUIElement, path: [Int], depth: Int) {
        if results.count >= maxResults || visited >= maxFindNodes || depth > maxAxDepth { return }
        visited += 1
        let role = axString(element, kAXRoleAttribute as CFString)
        let title = axString(element, kAXTitleAttribute as CFString)
        let description = axString(element, kAXDescriptionAttribute as CFString)
        let value = axString(element, kAXValueAttribute as CFString)
        let haystack = normalizedText([role, title, description, value].joined(separator: " "))
        if !path.isEmpty && haystack.contains(needle) {
            results.append(elementRecord(element, path: path, target: target))
            if results.count >= maxResults { return }
        }
        for (index, child) in axChildren(element).enumerated() {
            visit(child, path: path + [index], depth: depth + 1)
            if results.count >= maxResults || visited >= maxFindNodes { return }
        }
    }

    visit(root, path: [], depth: 0)
    return results
}

func resolveElement(_ opaqueID: String, target: WindowRecord) -> AXUIElement {
    let parts = opaqueID.split(separator: ":", omittingEmptySubsequences: false)
    if parts.count != 3 || parts[0] != "axp" { fail("invalid element locator") }
    let expectedFingerprint = String(parts[2])
    var element = targetAXWindow(target)
    let pathText = String(parts[1])
    if !pathText.isEmpty {
        for component in pathText.split(separator: ".") {
            guard let index = Int(component) else { fail("invalid element locator path") }
            let children = axChildren(element)
            if index < 0 || index >= children.count { fail("element locator is stale") }
            element = children[index]
        }
    }
    if elementFingerprint(element) != expectedFingerprint {
        fail("element locator fingerprint is stale")
    }
    return element
}

func isDescendant(_ child: AXUIElement, of ancestor: AXUIElement) -> Bool {
    var current: AXUIElement? = child
    for _ in 0..<32 {
        guard let candidate = current else { return false }
        if CFEqual(candidate, ancestor) { return true }
        current = asAXElement(axAttribute(candidate, kAXParentAttribute as CFString))
    }
    return false
}

func postUnicode(_ text: String) {
    let source = CGEventSource(stateID: .hidSystemState)
    guard let down = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: true),
          let up = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: false)
    else { fail("failed to create keyboard event") }
    let units = Array(text.utf16)
    units.withUnsafeBufferPointer { buffer in
        guard let base = buffer.baseAddress else { return }
        down.keyboardSetUnicodeString(stringLength: buffer.count, unicodeString: base)
        up.keyboardSetUnicodeString(stringLength: buffer.count, unicodeString: base)
    }
    down.post(tap: .cghidEventTap)
    up.post(tap: .cghidEventTap)
}

func keyCode(_ name: String) -> CGKeyCode? {
    [
        "enter": 36, "tab": 48, "escape": 53, "space": 49, "backspace": 51,
        "delete": 117, "up": 126, "down": 125, "left": 123, "right": 124,
        "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
        "a": 0, "c": 8, "v": 9, "x": 7, "z": 6
    ][name]
}

func eventFlags(_ csv: String) -> CGEventFlags {
    var flags: CGEventFlags = []
    for value in csv.split(separator: ",").map(String.init) {
        switch value {
        case "command": flags.insert(.maskCommand)
        case "control": flags.insert(.maskControl)
        case "option": flags.insert(.maskAlternate)
        case "shift": flags.insert(.maskShift)
        case "": break
        default: fail("unsupported modifier")
        }
    }
    return flags
}

func scaledImage(_ image: CGImage, maxWidth: Int, maxHeight: Int) -> CGImage {
    let width = image.width
    let height = image.height
    let scale = min(1.0, min(Double(maxWidth) / Double(width), Double(maxHeight) / Double(height)))
    if scale >= 1.0 { return image }
    let outputWidth = max(1, Int((Double(width) * scale).rounded(.down)))
    let outputHeight = max(1, Int((Double(height) * scale).rounded(.down)))
    guard let context = CGContext(
        data: nil,
        width: outputWidth,
        height: outputHeight,
        bitsPerComponent: 8,
        bytesPerRow: 0,
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    ) else { fail("failed to allocate screenshot scaling context") }
    context.interpolationQuality = .high
    context.draw(image, in: CGRect(x: 0, y: 0, width: outputWidth, height: outputHeight))
    guard let result = context.makeImage() else { fail("failed to scale screenshot") }
    return result
}

func captureWindow(_ target: WindowRecord, maxWidth: Int, maxHeight: Int) -> [String: Any] {
    requireScreenRecording()
    guard let image = CGWindowListCreateImage(
        .null,
        .optionIncludingWindow,
        target.cgWindowID,
        [.boundsIgnoreFraming, .bestResolution]
    ) else { fail("failed to capture target window") }
    let bounded = scaledImage(image, maxWidth: maxWidth, maxHeight: maxHeight)
    let bitmap = NSBitmapImageRep(cgImage: bounded)
    guard let png = bitmap.representation(using: .png, properties: [:]) else {
        fail("failed to encode screenshot")
    }
    if png.count > maxScreenshotBytes { fail("encoded screenshot exceeds byte limit") }
    return [
        "window_id": target.windowID,
        "pid": Int(target.pid),
        "width": bounded.width,
        "height": bounded.height,
        "mime_type": "image/png",
        "bytes": png.count,
        "data_base64": png.base64EncodedString()
    ]
}


let args = CommandLine.arguments
if args.count < 2 { fail("action is required") }
let action = args[1]

switch action {
case "permissions":
    emit([
        "ok": true,
        "result": [
            "platform": "macos",
            "screen_recording": CGPreflightScreenCaptureAccess(),
            "accessibility": AXIsProcessTrusted(),
            "native_helper": "computer.runtime.macos.v1",
            "legacy_unauthenticated_allowed": boolEnv("COMPUTER_RUNTIME_ALLOW_LEGACY_UNAUTHENTICATED")
        ]
    ])
case "list_apps":
    emit(["ok": true, "result": listApps()])
case "list_windows":
    emit(["ok": true, "result": listWindows()])
case "active_window":
    let windows = windowRecords()
    if let first = windows.first(where: { $0.frontmost }) {
        emit(["ok": true, "result": windowDictionary(first)])
    }
    emit(["ok": true, "result": NSNull()])
case "activate_app":
    if args.count < 4 { fail("activate_app requires pid and bundle_id") }
    guard let pidInt = Int32(args[2]) else { fail("invalid pid") }
    let bundleId = args[3]
    guard let app = NSRunningApplication(processIdentifier: pid_t(pidInt)) else {
        fail("target process is not running")
    }
    if (app.bundleIdentifier ?? "") != bundleId { fail("target bundle_id no longer matches pid") }
    let ok = app.activate(options: [.activateAllWindows, .activateIgnoringOtherApps])
    emit(["ok": ok, "result": ["activated": ok, "pid": Int(pidInt), "bundle_id": bundleId]])
case "screenshot":
    if args.count < 6 { fail("screenshot requires window_id, pid, max_width, max_height") }
    guard let pid = Int32(args[3]), let maxWidth = Int(args[4]), let maxHeight = Int(args[5]),
          maxWidth > 0, maxHeight > 0, maxWidth <= 4096, maxHeight <= 4096
    else { fail("invalid screenshot arguments") }
    let target = targetWindow(args[2], pid)
    emit(["ok": true, "result": captureWindow(target, maxWidth: maxWidth, maxHeight: maxHeight)])
case "find":
    if args.count < 5 { fail("find requires window_id, pid, max_results") }
    requireAccessibility()
    guard let pid = Int32(args[3]), let maxResults = Int(args[4]), maxResults > 0, maxResults <= 100 else {
        fail("invalid find arguments")
    }
    let queryData = readStdin(maxBytes: 4096)
    guard let query = String(data: queryData, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
          !query.isEmpty, query.count <= 256
    else { fail("invalid find query") }
    let target = targetWindow(args[2], pid)
    emit(["ok": true, "result": findElements(target: target, query: query, maxResults: maxResults)])
case "click":
    if args.count < 5 { fail("click requires element_id, window_id, pid") }
    requireAccessibility()
    guard let pid = Int32(args[4]) else { fail("invalid pid") }
    let target = targetWindow(args[3], pid)
    _ = requireFocusedTargetWindow(target)
    let element = resolveElement(args[2], target: target)
    let error = AXUIElementPerformAction(element, kAXPressAction as CFString)
    if error != .success { fail("Accessibility press failed: \(error.rawValue)") }
    emit(["ok": true, "result": ["clicked": true, "element_id": args[2], "window_id": target.windowID, "pid": Int(pid)]])
case "type":
    if args.count < 4 { fail("type requires window_id and pid") }
    requireAccessibility()
    guard let pid = Int32(args[3]) else { fail("invalid pid") }
    let target = targetWindow(args[2], pid)
    let axWindow = requireFocusedTargetWindow(target)
    let app = AXUIElementCreateApplication(pid_t(pid))
    guard let focused = asAXElement(axAttribute(app, kAXFocusedUIElementAttribute as CFString)),
          isDescendant(focused, of: axWindow)
    else { fail("focused input is not inside target window") }
    let data = readStdin(maxBytes: maxInputBytes)
    guard let text = String(data: data, encoding: .utf8), !text.isEmpty, text.count <= 4096 else {
        fail("invalid type payload")
    }
    postUnicode(text)
    emit(["ok": true, "result": ["typed": true, "characters": text.count, "window_id": target.windowID, "pid": Int(pid)]])
case "key":
    if args.count < 6 { fail("key requires window_id, pid, key, modifiers") }
    requireAccessibility()
    guard let pid = Int32(args[3]), let code = keyCode(args[4]) else { fail("invalid key") }
    let target = targetWindow(args[2], pid)
    _ = requireFocusedTargetWindow(target)
    let source = CGEventSource(stateID: .hidSystemState)
    guard let down = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: true),
          let up = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: false)
    else { fail("failed to create key event") }
    let flags = eventFlags(args[5])
    down.flags = flags
    up.flags = flags
    down.post(tap: .cghidEventTap)
    up.post(tap: .cghidEventTap)
    emit(["ok": true, "result": ["keyed": true, "key": args[4], "window_id": target.windowID, "pid": Int(pid)]])
case "scroll":
    if args.count < 6 { fail("scroll requires window_id, pid, delta_x, delta_y") }
    requireAccessibility()
    guard let pid = Int32(args[3]), let deltaX = Int32(args[4]), let deltaY = Int32(args[5]),
          abs(Int(deltaX)) <= 2000, abs(Int(deltaY)) <= 2000
    else { fail("invalid scroll arguments") }
    let target = targetWindow(args[2], pid)
    _ = requireFocusedTargetWindow(target)
    let source = CGEventSource(stateID: .hidSystemState)
    guard let event = CGEvent(
        scrollWheelEvent2Source: source,
        units: .pixel,
        wheelCount: 2,
        wheel1: deltaY,
        wheel2: deltaX,
        wheel3: 0
    ) else { fail("failed to create scroll event") }
    event.location = CGPoint(x: target.bounds.midX, y: target.bounds.midY)
    event.post(tap: .cghidEventTap)
    emit(["ok": true, "result": ["scrolled": true, "delta_x": Int(deltaX), "delta_y": Int(deltaY), "window_id": target.windowID, "pid": Int(pid)]])
default:
    fail("unsupported action: \(action)")
}
