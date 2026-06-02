import Cocoa

private func appRootURL() -> URL {
    let executable = Bundle.main.executableURL!
    return executable
        .deletingLastPathComponent()
        .deletingLastPathComponent()
        .deletingLastPathComponent()
        .deletingLastPathComponent()
}

if CommandLine.arguments.contains("--self-test") {
    let root = appRootURL()
    let setup = root.appendingPathComponent("setup-mac.command")
    print("root=\(root.path)")
    print("setup_exists=\(FileManager.default.fileExists(atPath: setup.path))")
    exit(FileManager.default.fileExists(atPath: setup.path) ? 0 : 1)
}

final class InstallerApp: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var window: NSWindow!
    private var statusLabel: NSTextField!
    private var textView: NSTextView!
    private var actionButton: NSButton!
    private var process: Process?
    private var didStart = false

    private lazy var rootURL: URL = appRootURL()

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        buildWindow()
        append("RAG Chat Setup\n")
        append("App folder: \(rootURL.path)\n\n")
        startSetup()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func windowWillClose(_ notification: Notification) {
        if let process, process.isRunning {
            process.terminate()
        }
    }

    private func buildWindow() {
        let rect = NSRect(x: 0, y: 0, width: 820, height: 560)
        window = NSWindow(
            contentRect: rect,
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "RAG Chat Setup"
        window.center()
        window.delegate = self

        let content = NSView(frame: rect)
        content.autoresizingMask = [.width, .height]
        window.contentView = content

        statusLabel = NSTextField(labelWithString: "Preparing setup...")
        statusLabel.font = .systemFont(ofSize: 15, weight: .semibold)
        statusLabel.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(statusLabel)

        actionButton = NSButton(title: "Stop", target: self, action: #selector(stopOrRunAgain))
        actionButton.bezelStyle = .rounded
        actionButton.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(actionButton)

        let scrollView = NSScrollView()
        scrollView.borderType = .bezelBorder
        scrollView.hasVerticalScroller = true
        scrollView.autohidesScrollers = false
        scrollView.translatesAutoresizingMaskIntoConstraints = false

        textView = NSTextView()
        textView.isEditable = false
        textView.isSelectable = true
        textView.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
        textView.textColor = .textColor
        textView.backgroundColor = .textBackgroundColor
        textView.textContainerInset = NSSize(width: 8, height: 8)
        scrollView.documentView = textView
        content.addSubview(scrollView)

        NSLayoutConstraint.activate([
            statusLabel.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 18),
            statusLabel.trailingAnchor.constraint(lessThanOrEqualTo: actionButton.leadingAnchor, constant: -12),
            statusLabel.topAnchor.constraint(equalTo: content.topAnchor, constant: 18),

            actionButton.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -18),
            actionButton.centerYAnchor.constraint(equalTo: statusLabel.centerYAnchor),
            actionButton.widthAnchor.constraint(equalToConstant: 96),

            scrollView.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 18),
            scrollView.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -18),
            scrollView.topAnchor.constraint(equalTo: statusLabel.bottomAnchor, constant: 14),
            scrollView.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -18)
        ])

        window.makeKeyAndOrderFront(nil)
    }

    private func startSetup() {
        guard !didStart || process == nil else { return }
        didStart = true
        actionButton.title = "Stop"
        statusLabel.stringValue = "Running setup..."

        let setupURL = rootURL.appendingPathComponent("setup-mac.command")
        guard FileManager.default.fileExists(atPath: setupURL.path) else {
            fail("setup-mac.command was not found next to RAG Chat Setup.app. Keep the app inside the rag-app folder.")
            return
        }

        runQuietly("/usr/bin/xattr", ["-d", "-r", "com.apple.quarantine", rootURL.path])
        runQuietly("/bin/chmod", ["+x", setupURL.path, rootURL.appendingPathComponent("Start RAG (online).command").path, rootURL.appendingPathComponent("run.sh").path])

        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/bash")
        task.arguments = [setupURL.path]
        task.currentDirectoryURL = rootURL
        task.environment = ProcessInfo.processInfo.environment.merging([
            "TERM": "xterm-256color"
        ]) { _, new in new }

        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = pipe

        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            let text = String(data: data, encoding: .utf8) ?? String(decoding: data, as: UTF8.self)
            DispatchQueue.main.async {
                self?.append(text)
            }
        }

        task.terminationHandler = { [weak self] task in
            DispatchQueue.main.async {
                self?.process = nil
                self?.actionButton.title = "Run Again"
                if task.terminationStatus == 0 {
                    self?.statusLabel.stringValue = "RAG Chat closed."
                    self?.append("\nRAG Chat closed.\n")
                } else {
                    self?.statusLabel.stringValue = "Setup failed with exit code \(task.terminationStatus)."
                    self?.append("\nSetup failed with exit code \(task.terminationStatus).\n")
                }
            }
        }

        do {
            try task.run()
            process = task
        } catch {
            fail("Could not start setup: \(error.localizedDescription)")
        }
    }

    @objc private func stopOrRunAgain() {
        if let process, process.isRunning {
            append("\nStopping setup...\n")
            process.terminate()
            actionButton.isEnabled = false
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
                self?.actionButton.isEnabled = true
            }
            return
        }

        textView.string = ""
        didStart = false
        startSetup()
    }

    private func append(_ text: String) {
        let storage = textView.textStorage!
        storage.append(NSAttributedString(string: text))
        let range = NSRange(location: storage.length, length: 0)
        textView.scrollRangeToVisible(range)
    }

    private func fail(_ message: String) {
        statusLabel.stringValue = "Setup cannot start."
        actionButton.title = "Run Again"
        append("ERROR: \(message)\n")
        let alert = NSAlert()
        alert.messageText = "RAG Chat Setup"
        alert.informativeText = message
        alert.alertStyle = .critical
        alert.runModal()
    }

    private func runQuietly(_ executable: String, _ arguments: [String]) {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: executable)
        task.arguments = arguments
        task.standardOutput = Pipe()
        task.standardError = Pipe()
        try? task.run()
        task.waitUntilExit()
    }
}

let app = NSApplication.shared
let delegate = InstallerApp()
app.delegate = delegate
app.run()
