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
    let layer: Int
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

func windowRecords(maxLayer: Int = 3) -> [WindowRecord] {
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
        if bounds.width <= 0 || bounds.height <= 0 { return nil }
        let isRuntimeFixture = owner.hasPrefix("computer-runtime")
        if !isRuntimeFixture && (layer < 0 || layer > maxLayer) { return nil }
        let pid = ownerPid.int32Value
        return WindowRecord(
            windowID: "wid_\(number.intValue)",
            cgWindowID: CGWindowID(number.uint32Value),
            pid: pid,
            owner: owner,
            title: (info[kCGWindowName as String] as? String) ?? "",
            bounds: bounds,
            frontmost: frontPid == pid,
            layer: layer
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

func listDisplays() -> [[String: Any]] {
    var ids = [CGDirectDisplayID](repeating: 0, count: 16)
    var count: UInt32 = 0
    CGGetActiveDisplayList(16, &ids, &count)
    return (0..<Int(count)).map { index in
        let id = ids[index]
        let bounds = CGDisplayBounds(id)
        let scale = NSScreen.screens.first { screen in
            (screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value == id
        }?.backingScaleFactor ?? 1.0
        return [
            "display_id": Int(id),
            "main": CGDisplayIsMain(id) != 0,
            "bounds": [
                "x": bounds.origin.x,
                "y": bounds.origin.y,
                "width": bounds.size.width,
                "height": bounds.size.height
            ],
            "scale": scale,
            "coordinate_space": "logical_points_global"
        ]
    }
}

func requirePointInWindow(_ point: CGPoint, _ target: WindowRecord) {
    if !target.bounds.insetBy(dx: -1, dy: -1).contains(point) {
        fail("point is outside the current target window bounds")
    }
}

func isSystemOverlay(_ record: WindowRecord) -> Bool {
    let owners: Set<String> = [
        "Dock", "Window Server", "WindowServer", "Control Center",
        "Notification Center", "Notification Centre", "SystemUIServer",
        "Wallpaper", "Backstop", "Screenshot", "Spotlight", "Control Strip"
    ]
    if owners.contains(record.owner) { return true }
    if record.owner.hasPrefix("computer-runtime") { return false }
    return record.layer >= 20
}

func frontWindow(at point: CGPoint) -> WindowRecord? {
    windowRecords(maxLayer: 1000).first { record in
        !isSystemOverlay(record) && record.bounds.insetBy(dx: -1, dy: -1).contains(point)
    }
}

func requireTargetFront(at point: CGPoint, _ target: WindowRecord) {
    let deadline = Date().addingTimeInterval(2.0)
    while Date() < deadline {
        activateTargetApp(target)
        if let axWindow = optionalAXWindow(target) {
            raiseAXWindow(axWindow)
        }
        let current = targetWindow(target.windowID, target.pid)
        requirePointInWindow(point, current)
        if let front = frontWindow(at: point),
           front.windowID == current.windowID,
           front.pid == current.pid {
            return
        }
        Thread.sleep(forTimeInterval: 0.05)
    }
    let occluder = frontWindow(at: point)
    let desc = occluder.map { "\($0.owner) \($0.windowID) layer=\($0.layer) title=\($0.title)" } ?? "none"
    fail("target window is occluded at the requested point by \(desc)")
}

func postMouse(point: CGPoint, button: String) {
    let right = button == "right"
    let source = CGEventSource(stateID: .hidSystemState)
    CGWarpMouseCursorPosition(point)
    postMove(point)
    guard let down = CGEvent(
            mouseEventSource: source,
            mouseType: right ? .rightMouseDown : .leftMouseDown,
            mouseCursorPosition: point,
            mouseButton: right ? .right : .left
          ),
          let up = CGEvent(
            mouseEventSource: source,
            mouseType: right ? .rightMouseUp : .leftMouseUp,
            mouseCursorPosition: point,
            mouseButton: right ? .right : .left
          )
    else { fail("failed to create mouse event") }
    down.setIntegerValueField(.mouseEventClickState, value: 1)
    up.setIntegerValueField(.mouseEventClickState, value: 1)
    down.post(tap: .cghidEventTap)
    up.post(tap: .cghidEventTap)
}

func postMove(_ point: CGPoint) {
    let source = CGEventSource(stateID: .hidSystemState)
    guard let event = CGEvent(
        mouseEventSource: source,
        mouseType: .mouseMoved,
        mouseCursorPosition: point,
        mouseButton: .left
    ) else { fail("failed to create mouse move") }
    event.post(tap: .cghidEventTap)
}

func postDrag(from: CGPoint, to: CGPoint) {
    let source = CGEventSource(stateID: .hidSystemState)
    guard let down = CGEvent(
            mouseEventSource: source,
            mouseType: .leftMouseDown,
            mouseCursorPosition: from,
            mouseButton: .left
          ),
          let drag = CGEvent(
            mouseEventSource: source,
            mouseType: .leftMouseDragged,
            mouseCursorPosition: to,
            mouseButton: .left
          ),
          let up = CGEvent(
            mouseEventSource: source,
            mouseType: .leftMouseUp,
            mouseCursorPosition: to,
            mouseButton: .left
          )
    else { fail("failed to create drag event") }
    down.post(tap: .cghidEventTap)
    drag.post(tap: .cghidEventTap)
    up.post(tap: .cghidEventTap)
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
    return approximatelyEqual(frame.origin.x, target.bounds.origin.x, tolerance: 24) &&
        approximatelyEqual(frame.origin.y, target.bounds.origin.y, tolerance: 24) &&
        approximatelyEqual(frame.size.width, target.bounds.size.width, tolerance: 24) &&
        approximatelyEqual(frame.size.height, target.bounds.size.height, tolerance: 24)
}

func axWindows(of target: WindowRecord) -> [AXUIElement] {
    let app = AXUIElementCreateApplication(pid_t(target.pid))
    return (axAttribute(app, kAXWindowsAttribute as CFString) as? [AXUIElement]) ?? []
}

func targetAXWindow(_ target: WindowRecord) -> AXUIElement {
    let windows = axWindows(of: target)
    if let match = windows.first(where: { windowMatches($0, target) }) {
        return match
    }
    var best: AXUIElement?
    var bestDelta = CGFloat.greatestFiniteMagnitude
    for window in windows {
        guard let frame = axFrame(window) else { continue }
        let dx = abs(frame.midX - target.bounds.midX) + abs(frame.midY - target.bounds.midY)
        let dSize = abs(frame.width - target.bounds.width) + abs(frame.height - target.bounds.height)
        let delta = dx + dSize
        if delta < bestDelta {
            bestDelta = delta
            best = window
        }
    }
    guard let best, bestDelta < 120 else {
        fail("Accessibility window no longer matches observed target")
    }
    return best
}

func activateTargetApp(_ target: WindowRecord) {
    guard let app = NSRunningApplication(processIdentifier: pid_t(target.pid)) else {
        fail("target process is not running")
    }
    _ = app.activate(options: [.activateAllWindows, .activateIgnoringOtherApps])
}

func raiseAXWindow(_ axWindow: AXUIElement) {
    AXUIElementSetAttributeValue(axWindow, kAXMainAttribute as CFString, kCFBooleanTrue)
    _ = AXUIElementPerformAction(axWindow, kAXRaiseAction as CFString)
}

func firstElement(in root: AXUIElement, roles: Set<String>, depth: Int = 0) -> AXUIElement? {
    if depth > maxAxDepth { return nil }
    let role = axString(root, kAXRoleAttribute as CFString)
    if depth > 0 && roles.contains(role) {
        return root
    }
    for child in axChildren(root) {
        if let found = firstElement(in: child, roles: roles, depth: depth + 1) {
            return found
        }
    }
    return nil
}

func ensureFocusedTextInput(in axWindow: AXUIElement, app: AXUIElement) -> AXUIElement {
    let textRoles: Set<String> = ["AXTextField", "AXTextArea", "AXComboBox"]
    let field: AXUIElement
    if let focused = asAXElement(axAttribute(app, kAXFocusedUIElementAttribute as CFString)),
       isDescendant(focused, of: axWindow),
       textRoles.contains(axString(focused, kAXRoleAttribute as CFString)) {
        field = focused
    } else if let found = firstElement(in: axWindow, roles: textRoles) {
        field = found
        AXUIElementSetAttributeValue(field, kAXFocusedAttribute as CFString, kCFBooleanTrue)
    } else {
        fail("focused input is not inside target window")
    }
    if let frame = axFrame(field), frame.width > 1, frame.height > 1 {
        postMouse(point: CGPoint(x: frame.midX, y: frame.midY), button: "left")
        Thread.sleep(forTimeInterval: 0.05)
    }
    return field
}

func requireFocusedTargetWindow(_ target: WindowRecord) -> AXUIElement {
    activateTargetApp(target)
    let deadline = Date().addingTimeInterval(1.2)
    var last: AXUIElement?
    while Date() < deadline {
        let axWindow = targetAXWindow(target)
        last = axWindow
        raiseAXWindow(axWindow)
        let front = NSWorkspace.shared.frontmostApplication?.processIdentifier == pid_t(target.pid)
        let app = AXUIElementCreateApplication(pid_t(target.pid))
        let focused = asAXElement(axAttribute(app, kAXFocusedWindowAttribute as CFString))
        if front {
            if let focused, windowMatches(focused, target) || CFEqual(focused, axWindow) {
                return axWindow
            }
            if let focused, isDescendant(focused, of: axWindow) {
                return axWindow
            }
            if focused == nil {
                return axWindow
            }
        }
        Thread.sleep(forTimeInterval: 0.05)
    }
    if let last {
        return last
    }
    fail("failed to focus target window")
}

func optionalAXWindow(_ target: WindowRecord) -> AXUIElement? {
    let windows = axWindows(of: target)
    if let match = windows.first(where: { windowMatches($0, target) }) {
        return match
    }
    var best: AXUIElement?
    var bestDelta = CGFloat.greatestFiniteMagnitude
    for window in windows {
        guard let frame = axFrame(window) else { continue }
        let dx = abs(frame.midX - target.bounds.midX) + abs(frame.midY - target.bounds.midY)
        let dSize = abs(frame.width - target.bounds.width) + abs(frame.height - target.bounds.height)
        let delta = dx + dSize
        if delta < bestDelta {
            bestDelta = delta
            best = window
        }
    }
    guard let best, bestDelta < 120 else { return nil }
    return best
}

func focusForCoordinateAction(_ target: WindowRecord) {
    activateTargetApp(target)
    if let axWindow = optionalAXWindow(target) {
        raiseAXWindow(axWindow)
        let deadline = Date().addingTimeInterval(0.6)
        while Date() < deadline {
            if NSWorkspace.shared.frontmostApplication?.processIdentifier == pid_t(target.pid) {
                return
            }
            Thread.sleep(forTimeInterval: 0.05)
        }
    } else {
        Thread.sleep(forTimeInterval: 0.2)
    }
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
        let safeValueRoles = ["AXStaticText", "AXButton", "AXLink", "AXMenuItem", "AXCheckBox", "AXRadioButton"]
        let safeValue = safeValueRoles.contains(role)
            ? axString(element, kAXValueAttribute as CFString)
            : ""
        let haystack = normalizedText([role, title, description, safeValue].joined(separator: " "))
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
    let digest = SHA256.hash(data: png)
    let imageSha = digest.map { String(format: "%02x", $0) }.joined()
    return [
        "window_id": target.windowID,
        "pid": Int(target.pid),
        "width": bounded.width,
        "height": bounded.height,
        "mime_type": "image/png",
        "bytes": png.count,
        "data_base64": png.base64EncodedString(),
        "image_sha256": imageSha,
        "bounds": [
            "x": target.bounds.origin.x,
            "y": target.bounds.origin.y,
            "width": target.bounds.size.width,
            "height": target.bounds.size.height
        ],
        "coordinate_space": "logical_points_global"
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
case "list_displays":
    emit(["ok": true, "result": listDisplays()])
case "focus_window":
    if args.count < 4 { fail("focus_window requires window_id, pid") }
    requireAccessibility()
    guard let pid = Int32(args[3]) else { fail("invalid pid") }
    let target = targetWindow(args[2], pid)
    _ = requireFocusedTargetWindow(target)
    emit(["ok": true, "result": ["focused": true, "window_id": target.windowID, "pid": Int(pid)]])
case "active_window":
    let windows = windowRecords()
        .filter { $0.frontmost }
        .sorted { ($0.bounds.width * $0.bounds.height) > ($1.bounds.width * $1.bounds.height) }
    if let first = windows.first {
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
case "click_xy":
    if args.count < 6 { fail("click_xy requires window_id, pid, x, y") }
    requireAccessibility()
    guard let pid = Int32(args[3]), let x = Double(args[4]), let y = Double(args[5]),
          x.isFinite, y.isFinite
    else { fail("invalid click_xy arguments") }
    let button = args.count >= 7 ? args[6] : "left"
    if button != "left" && button != "right" { fail("unsupported mouse button") }
    let target = targetWindow(args[2], pid)
    if optionalAXWindow(target) != nil {
        _ = requireFocusedTargetWindow(target)
    } else {
        focusForCoordinateAction(target)
    }
    let point = CGPoint(x: x, y: y)
    requirePointInWindow(point, target)
    requireTargetFront(at: point, target)
    postMouse(point: point, button: button)
    Thread.sleep(forTimeInterval: 0.05)
    emit(["ok": true, "result": ["clicked": true, "button": button, "x": x, "y": y, "window_id": target.windowID, "pid": Int(pid)]])
case "move_xy":
    if args.count < 6 { fail("move_xy requires window_id, pid, x, y") }
    requireAccessibility()
    guard let pid = Int32(args[3]), let x = Double(args[4]), let y = Double(args[5]),
          x.isFinite, y.isFinite
    else { fail("invalid move_xy arguments") }
    let target = targetWindow(args[2], pid)
    if optionalAXWindow(target) != nil {
        _ = requireFocusedTargetWindow(target)
    } else {
        focusForCoordinateAction(target)
    }
    let point = CGPoint(x: x, y: y)
    requirePointInWindow(point, target)
    postMove(point)
    emit(["ok": true, "result": ["moved": true, "x": x, "y": y, "window_id": target.windowID, "pid": Int(pid)]])
case "drag_xy":
    if args.count < 8 { fail("drag_xy requires window_id, pid, x1, y1, x2, y2") }
    requireAccessibility()
    guard let pid = Int32(args[3]),
          let x1 = Double(args[4]), let y1 = Double(args[5]),
          let x2 = Double(args[6]), let y2 = Double(args[7]),
          x1.isFinite, y1.isFinite, x2.isFinite, y2.isFinite
    else { fail("invalid drag_xy arguments") }
    let target = targetWindow(args[2], pid)
    if optionalAXWindow(target) != nil {
        _ = requireFocusedTargetWindow(target)
    } else {
        focusForCoordinateAction(target)
    }
    let from = CGPoint(x: x1, y: y1)
    let to = CGPoint(x: x2, y: y2)
    requirePointInWindow(from, target)
    requirePointInWindow(to, target)
    postDrag(from: from, to: to)
    emit(["ok": true, "result": ["dragged": true, "from": ["x": x1, "y": y1], "to": ["x": x2, "y": y2], "window_id": target.windowID, "pid": Int(pid)]])
case "paste":
    if args.count < 4 { fail("paste requires window_id and pid") }
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
        fail("invalid paste payload")
    }
    let board = NSPasteboard.general
    board.clearContents()
    board.setString(text, forType: .string)
    let source = CGEventSource(stateID: .hidSystemState)
    guard let down = CGEvent(keyboardEventSource: source, virtualKey: 9, keyDown: true),
          let up = CGEvent(keyboardEventSource: source, virtualKey: 9, keyDown: false)
    else { fail("failed to create paste key event") }
    down.flags = .maskCommand
    up.flags = .maskCommand
    down.post(tap: .cghidEventTap)
    up.post(tap: .cghidEventTap)
    emit(["ok": true, "result": ["pasted": true, "characters": text.count, "window_id": target.windowID, "pid": Int(pid)]])
case "set_value":
    if args.count < 5 { fail("set_value requires element_id, window_id, pid") }
    requireAccessibility()
    guard let pid = Int32(args[4]) else { fail("invalid pid") }
    let target = targetWindow(args[3], pid)
    _ = requireFocusedTargetWindow(target)
    let element = resolveElement(args[2], target: target)
    let data = readStdin(maxBytes: maxInputBytes)
    guard let text = String(data: data, encoding: .utf8), text.count <= 4096 else {
        fail("invalid set_value payload")
    }
    let error = AXUIElementSetAttributeValue(element, kAXValueAttribute as CFString, text as CFTypeRef)
    if error != .success { fail("Accessibility set value failed: \(error.rawValue)") }
    emit(["ok": true, "result": ["set": true, "characters": text.count, "element_id": args[2], "window_id": target.windowID, "pid": Int(pid)]])
case "type":
    if args.count < 4 { fail("type requires window_id and pid") }
    requireAccessibility()
    guard let pid = Int32(args[3]) else { fail("invalid pid") }
    let target = targetWindow(args[2], pid)
    let axWindow = requireFocusedTargetWindow(target)
    let app = AXUIElementCreateApplication(pid_t(pid))
    let field = ensureFocusedTextInput(in: axWindow, app: app)
    let data = readStdin(maxBytes: maxInputBytes)
    guard let text = String(data: data, encoding: .utf8), !text.isEmpty, text.count <= 4096 else {
        fail("invalid type payload")
    }
    let current = axString(field, kAXValueAttribute as CFString)
    _ = AXUIElementSetAttributeValue(field, kAXValueAttribute as CFString, (current + text) as CFTypeRef)
    postUnicode(text)
    Thread.sleep(forTimeInterval: 0.05)
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
