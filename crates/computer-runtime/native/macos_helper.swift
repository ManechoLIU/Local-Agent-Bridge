import AppKit
import ApplicationServices
import CoreGraphics
import Foundation

func emit(_ result: [String: Any]) -> Never {
    let data = try! JSONSerialization.data(withJSONObject: result, options: [])
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

func appRecord(_ app: NSRunningApplication) -> [String: Any] {
    return [
        "pid": Int(app.processIdentifier),
        "bundle_id": app.bundleIdentifier ?? "",
        "display_name": app.localizedName ?? "",
        "is_active": app.isActive,
        "is_hidden": app.isHidden,
        "is_terminated": app.isTerminated
    ]
}

func listApps() -> [[String: Any]] {
    return NSWorkspace.shared.runningApplications
        .filter { $0.activationPolicy == .regular && !$0.isTerminated }
        .map(appRecord)
        .sorted { lhs, rhs in
            let a = (lhs["display_name"] as? String) ?? ""
            let b = (rhs["display_name"] as? String) ?? ""
            return a.localizedCaseInsensitiveCompare(b) == .orderedAscending
        }
}

func listWindows() -> [[String: Any]] {
    let options: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    guard let raw = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] else {
        return []
    }
    let frontPid = NSWorkspace.shared.frontmostApplication?.processIdentifier
    return raw.compactMap { info in
        guard let number = info[kCGWindowNumber as String] as? NSNumber,
              let ownerPid = info[kCGWindowOwnerPID as String] as? NSNumber,
              let owner = info[kCGWindowOwnerName as String] as? String,
              let boundsDict = info[kCGWindowBounds as String] as? [String: Any]
        else { return nil }
        let layer = (info[kCGWindowLayer as String] as? NSNumber)?.intValue ?? 0
        if layer != 0 { return nil }
        let bounds = CGRect(dictionaryRepresentation: boundsDict as CFDictionary)
        let title = (info[kCGWindowName as String] as? String) ?? ""
        let pid = ownerPid.int32Value
        return [
            "window_id": "wid_\(number.intValue)",
            "cg_window_id": number.intValue,
            "pid": Int(pid),
            "owner": owner,
            "title": title,
            "focused": frontPid == pid,
            "bounds": [
                "x": bounds?.origin.x ?? 0,
                "y": bounds?.origin.y ?? 0,
                "width": bounds?.size.width ?? 0,
                "height": bounds?.size.height ?? 0
            ],
            "coordinate_space": "logical_points_global"
        ]
    }
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
            "native_helper": "computer.runtime.macos.v0",
            "legacy_unauthenticated_allowed": boolEnv("COMPUTER_RUNTIME_ALLOW_LEGACY_UNAUTHENTICATED")
        ]
    ])
case "list_apps":
    emit(["ok": true, "result": listApps()])
case "list_windows":
    emit(["ok": true, "result": listWindows()])
case "active_window":
    let windows = listWindows()
    if let first = windows.first(where: { ($0["focused"] as? Bool) == true }) {
        emit(["ok": true, "result": first])
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
default:
    fail("unsupported action: \(action)")
}
