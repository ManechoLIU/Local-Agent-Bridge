import AppKit
import Foundation

final class CanvasView: NSView {
    let markerDir: String
    var hit = false

    init(markerDir: String, frame: NSRect) {
        self.markerDir = markerDir
        super.init(frame: frame)
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    func markClicked() {
        if hit { return }
        hit = true
        needsDisplay = true
        let url = URL(fileURLWithPath: markerDir).appendingPathComponent("clicked")
        try? Data("clicked\n".utf8).write(to: url, options: .atomic)
    }

    override func draw(_ dirtyRect: NSRect) {
        NSColor.black.setFill()
        bounds.fill()
        let target = NSRect(x: 140, y: 80, width: 120, height: 80)
        (hit ? NSColor.systemGreen : NSColor.systemRed).setFill()
        target.fill()
    }

    override func mouseDown(with event: NSEvent) {
        markClicked()
        _ = event
    }

    override func hitTest(_ point: NSPoint) -> NSView? {
        bounds.contains(point) ? self : nil
    }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override var isFlipped: Bool { false }
}

final class CanvasDelegate: NSObject, NSApplicationDelegate {
    let markerDir: String
    let windowTitle: String
    let contentSize = NSSize(width: 400, height: 240)
    let targetRect = NSRect(x: 140, y: 80, width: 120, height: 80)
    let canvas: CanvasView
    let window: NSPanel
    var keepFront: Timer?

    init(markerDir: String, windowTitle: String) {
        self.markerDir = markerDir
        self.windowTitle = windowTitle
        let contentFrame = NSRect(origin: .zero, size: contentSize)
        canvas = CanvasView(markerDir: markerDir, frame: contentFrame)
        canvas.setAccessibilityElement(false)
        canvas.setAccessibilityChildren([] as [Any])
        window = NSPanel(
            contentRect: contentFrame,
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        super.init()
        window.contentView = canvas
        window.title = windowTitle
        window.isReleasedWhenClosed = false
        window.hasShadow = false
        window.isFloatingPanel = true
        window.becomesKeyOnlyIfNeeded = false
        window.worksWhenModal = true
        window.level = .floating
        window.hidesOnDeactivate = false
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        window.center()
    }

    func contentScreenRect() -> NSRect {
        let rect = canvas.convert(canvas.bounds, to: nil)
        return window.convertToScreen(rect)
    }

    func noteMouseIfInContent(_ event: NSEvent) {
        let point: NSPoint
        if let loc = event.cgEvent?.location,
           let screen = NSScreen.screens.first(where: { $0.frame.origin == .zero }) {
            point = NSPoint(x: loc.x, y: screen.frame.maxY - loc.y)
        } else {
            point = NSEvent.mouseLocation
        }
        if contentScreenRect().insetBy(dx: -2, dy: -2).contains(point) {
            canvas.markClicked()
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        window.makeKeyAndOrderFront(nil)
        window.orderFrontRegardless()
        NSApp.activate(ignoringOtherApps: true)
        _ = NSEvent.addLocalMonitorForEvents(matching: [.leftMouseDown]) { [weak self] event in
            self?.noteMouseIfInContent(event)
            return event
        }
        _ = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown]) { [weak self] event in
            self?.noteMouseIfInContent(event)
        }
        keepFront = Timer.scheduledTimer(withTimeInterval: 0.15, repeats: true) { [weak self] _ in
            guard let self, !self.canvas.hit else { return }
            self.window.level = .floating
            self.window.orderFrontRegardless()
        }
        let url = URL(fileURLWithPath: markerDir).appendingPathComponent("ready.json")
        let body = try! JSONSerialization.data(withJSONObject: [
            "ok": true,
            "pid": Int(ProcessInfo.processInfo.processIdentifier),
            "title": windowTitle,
            "content": ["width": contentSize.width, "height": contentSize.height],
            "target": [
                "x": targetRect.origin.x,
                "y": targetRect.origin.y,
                "width": targetRect.size.width,
                "height": targetRect.size.height
            ],
            "target_coordinate_space": "content_bottom_left"
        ] as [String: Any], options: [])
        try? body.write(to: url, options: .atomic)
        DispatchQueue.main.asyncAfter(deadline: .now() + 90) {
            NSApp.terminate(nil)
        }
    }
}

let args = CommandLine.arguments
if args.count < 3 {
    fputs("usage: canvas_fixture <marker_dir> <window_title>\n", stderr)
    exit(2)
}
try FileManager.default.createDirectory(atPath: args[1], withIntermediateDirectories: true)
let retained = CanvasDelegate(markerDir: args[1], windowTitle: args[2])
let app = NSApplication.shared
app.setActivationPolicy(.regular)
app.delegate = retained
withExtendedLifetime(retained) {
    app.run()
}
