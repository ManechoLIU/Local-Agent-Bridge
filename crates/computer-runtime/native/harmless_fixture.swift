import AppKit
import Foundation

final class FixtureDelegate: NSObject, NSApplicationDelegate, NSTextFieldDelegate {
    let markerDir: String
    let windowTitle: String
    var typedWatch: Timer?
    let window = NSWindow(
        contentRect: NSRect(x: 48, y: 48, width: 440, height: 180),
        styleMask: [.titled, .closable],
        backing: .buffered,
        defer: false
    )
    let button = NSButton(title: "LAB-COMP-01 Click Me", target: nil, action: nil)
    let field = NSTextField(string: "")

    init(markerDir: String, windowTitle: String) {
        self.markerDir = markerDir
        self.windowTitle = windowTitle
        super.init()
        button.target = self
        button.action = #selector(markClicked)
        button.setButtonType(.momentaryPushIn)
        button.bezelStyle = .rounded
        button.frame = NSRect(x: 20, y: 96, width: 400, height: 32)
        button.setAccessibilityLabel("LAB-COMP-01 Click Me")

        field.frame = NSRect(x: 20, y: 44, width: 400, height: 24)
        field.placeholderString = "LAB-COMP-01 Type Here"
        field.setAccessibilityLabel("LAB-COMP-01 Type Here")
        field.delegate = self

        let content = NSView(frame: NSRect(x: 0, y: 0, width: 440, height: 180))
        content.addSubview(button)
        content.addSubview(field)
        window.contentView = content
        window.title = windowTitle
        window.isReleasedWhenClosed = false
        window.level = .floating
        window.center()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        window.makeKeyAndOrderFront(nil)
        window.makeFirstResponder(field)
        ()
        typedWatch = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in
            guard let self else { return }
            let value = self.field.stringValue
            if !value.isEmpty {
                self.write("typed", data: Data(value.utf8))
            }
        }
        write("ready.json", data: try! JSONSerialization.data(withJSONObject: [
            "ok": true,
            "pid": Int(ProcessInfo.processInfo.processIdentifier),
            "title": windowTitle
        ], options: []))
        DispatchQueue.main.asyncAfter(deadline: .now() + 20) {
            NSApp.terminate(nil)
        }
    }

    func controlTextDidChange(_ obj: Notification) {
        write("typed", data: Data(field.stringValue.utf8))
    }

    @objc func markClicked() {
        button.title = "LAB-COMP-01 Clicked"
        write("clicked", data: Data("clicked\n".utf8))
    }

    func write(_ name: String, data: Data) {
        let url = URL(fileURLWithPath: markerDir).appendingPathComponent(name)
        try? data.write(to: url, options: .atomic)
    }
}

let args = CommandLine.arguments
if args.count < 3 {
    fputs("usage: harmless_fixture <marker_dir> <window_title>\n", stderr)
    exit(2)
}
try FileManager.default.createDirectory(
    atPath: args[1],
    withIntermediateDirectories: true
)

let retainedDelegate = FixtureDelegate(markerDir: args[1], windowTitle: args[2])
let app = NSApplication.shared
app.setActivationPolicy(.regular)
app.delegate = retainedDelegate
withExtendedLifetime(retainedDelegate) {
    app.run()
}
