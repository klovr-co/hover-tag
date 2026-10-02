// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Prototype: the Tag Mac app. On first open it installs Tag by wrapping
// install.sh; afterwards it lists, starts and adds Tags.
import AppKit
import SwiftUI

enum StepState { case pending, running, done, failed }

struct Step: Identifiable {
    let id: Int
    let title: String
    let detail: String
    let weight: Double  // share of the overall progress bar
    let marker: String  // installer output that means this step has started
    var state: StepState = .pending
}

let installCommand = "curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/klovr-co/hover-tag/main/install.sh | sh"

@MainActor
final class Installer: ObservableObject {
    @Published var steps: [Step] = [
        Step(id: 0, title: "Getting installation tools", detail: "A small, verified download", weight: 0.10, marker: ""),
        Step(id: 1, title: "Preparing Python", detail: "A private copy, so your system stays untouched", weight: 0.15, marker: "Preparing Tag Python"),
        Step(id: 2, title: "Downloading Tag", detail: "The latest stable release", weight: 0.10, marker: "PREPARING"),
        Step(id: 3, title: "Installing components", detail: "Slack connection and local search", weight: 0.30, marker: "Installing runtime dependencies"),
        Step(id: 4, title: "Preparing local memory", detail: "Downloads a search model — the longest step", weight: 0.30, marker: "Preparing the local memory model"),
        Step(id: 5, title: "Finishing up", detail: "Adding the tag command", weight: 0.05, marker: "Command"),
    ]
    @Published var phase: Phase = tagInstalled && !demoMode ? .home : .welcome
    @Published var log = ""
    @Published var progress = 0.0
    @Published var version = ""
    @Published var failure = ""
    @Published var showLog = false
    private var stepStart = Date()
    private var timer: Timer?
    private var process: Process?

    enum Phase { case welcome, installing, finished, failed, home, connect }
    let tags = Tags()

    init() {
        tags.finishSetup = { [weak self] row in self?.finishSetup(row) }
    }
    @Published var session = SetupSession()

    func showHome() { phase = .home; Task { await tags.refresh() } }

    func finishSetup(_ row: TagRow) {
        session = SetupSession()
        session.start(arguments: Array(row.command("setup").dropFirst()))
        phase = .connect
    }
    func addTag() {
        session = SetupSession()
        session.start(firstTag: tags.rows.isEmpty)
        phase = .connect
    }

    var current: Int? { steps.firstIndex { $0.state == .running } }

    func start() {
        phase = .installing
        advance(to: 0)
        // Creep within the running step so long downloads never look frozen.
        timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
        if ProcessInfo.processInfo.environment["TAG_INSTALLER_DEMO"] != nil { return demo() }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/sh")
        let script = ProcessInfo.processInfo.environment["TAG_INSTALLER_SCRIPT"].map { "sh \($0)" } ?? installCommand
        p.arguments = ["-c", script + " 2>&1"]
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "\(NSHomeDirectory())/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        env["NO_COLOR"] = "1"
        env["TERM"] = "dumb"
        p.environment = env
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { h in
            let data = h.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            Task { @MainActor in self.consume(text) }
        }
        p.terminationHandler = { p in
            Task { @MainActor in self.finish(status: p.terminationStatus) }
        }
        do { try p.run(); process = p } catch { fail(error.localizedDescription) }
    }

    func cancel() { process?.terminate(); NSApp.terminate(nil) }

    private func consume(_ chunk: String) {
        // Strip ANSI escapes and carriage-return progress redraws.
        let clean = chunk.replacingOccurrences(of: "\u{1B}\\[[0-9;?]*[A-Za-z]", with: "", options: .regularExpression)
        log += clean.replacingOccurrences(of: "\r", with: "\n")
        for line in clean.split(whereSeparator: \.isNewline) {
            let text = line.trimmingCharacters(in: .whitespaces)
            if let r = text.range(of: "Tag v"), text.contains("Release") { version = String(text[r.upperBound...]) }
            if text.hasPrefix("Installation failed:") { failure = String(text.dropFirst(21)) }
            for step in steps.reversed() where !step.marker.isEmpty && text.contains(step.marker) {
                if step.id > (current ?? -1) { advance(to: step.id) }
                break
            }
        }
    }

    private func advance(to index: Int) {
        for i in steps.indices { steps[i].state = i < index ? .done : (i == index ? .running : .pending) }
        progress = steps.prefix(index).reduce(0) { $0 + $1.weight }
        stepStart = Date()
    }

    private func tick() {
        guard let i = current else { return }
        let base = steps.prefix(i).reduce(0) { $0 + $1.weight }
        // Approach 90% of the step asymptotically; real markers jump ahead.
        let fraction = 0.9 * (1 - exp(-Date().timeIntervalSince(stepStart) / 25))
        withAnimation(.linear(duration: 0.25)) { progress = base + steps[i].weight * fraction }
    }

    private func finish(status: Int32) {
        timer?.invalidate()
        if status == 0 {
            for i in steps.indices { steps[i].state = .done }
            withAnimation { progress = 1; phase = .finished }
        } else {
            fail(failure.isEmpty ? "The installer stopped (exit \(status))." : failure)
        }
    }

    private func fail(_ message: String) {
        timer?.invalidate()
        if let i = current { steps[i].state = .failed }
        failure = message
        phase = .failed
    }

    private func demo() {
        let script: [(Double, String)] = [
            (1.5, "Downloading Tag installation tools…\n"), (3, "Preparing Tag Python 3.12.14…\n"),
            (5, "  PREPARING\n    ✓  Release   Tag v0.2.0\n"), (7, "    Installing runtime dependencies…\n"),
            (11, "    Preparing the local memory model…\n"), (15, "    ✓  Command   ~/.local/bin/tag\n"),
            (16, "  ✓  Tag is installed\n"),
        ]
        for (delay, text) in script {
            DispatchQueue.main.asyncAfter(deadline: .now() + delay) { self.consume(text) }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 17) { self.finish(status: 0) }
    }

}

struct Spinner: View {
    var body: some View {
        TimelineView(.animation) { context in
            Circle().trim(from: 0, to: 0.7)
                .stroke(Color.accentColor, style: StrokeStyle(lineWidth: 2, lineCap: .round))
                .rotationEffect(.degrees(context.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 0.9) / 0.9 * 360))
                .padding(2)
        }
    }
}

struct StepRow: View {
    let step: Step
    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            ZStack {
                switch step.state {
                case .pending: Circle().strokeBorder(.tertiary, lineWidth: 1.5)
                case .running: Spinner()
                case .done: Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                case .failed: Image(systemName: "exclamationmark.circle.fill").foregroundStyle(.red)
                }
            }
            .frame(width: 18, height: 18)
            VStack(alignment: .leading, spacing: 2) {
                Text(step.title).fontWeight(step.state == .running ? .semibold : .regular)
                    .foregroundStyle(step.state == .pending ? .secondary : .primary)
                if step.state == .running {
                    Text(step.detail).font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer()
        }
    }
}

struct ContentView: View {
    // Loaded once: the header re-renders several times a second during
    // install, and a fresh NSImage each time made the icon flicker.
    static let icon = Bundle.main.url(forResource: "tag-icon", withExtension: "png").flatMap(NSImage.init(contentsOf:))

    @ObservedObject var installer: Installer


    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            if installer.phase == .home {
                TagsView(tags: installer.tags, add: installer.addTag)
            } else {
                header
            }
            switch installer.phase {
            case .welcome: welcome
            case .home: EmptyView()
            case .connect: ConnectView(session: installer.session, cancel: installer.showHome, done: installer.showHome)
            default: progress
            }
        }
        .padding(28)
        .frame(width: 460)
    }

    var header: some View {
            HStack(spacing: 14) {
                if let image = ContentView.icon {
                    Image(nsImage: image).resizable().frame(width: 52, height: 52).clipShape(RoundedRectangle(cornerRadius: 12))
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.title2.bold())
                    Text(subtitle).foregroundStyle(.secondary)
                }
            }
    }

    var title: String {
        switch installer.phase {
        case .welcome: "Install Tag"
        case .installing: "Installing Tag…"
        case .finished: "Tag is installed"
        case .failed: "Installation stopped"
        case .home: "Your Tags"
        case .connect: "Add a Tag"
        }
    }

    var subtitle: String {
        switch installer.phase {
        case .welcome: "Your personal assistant, in Slack."
        case .installing: "This usually takes a few minutes."
        case .finished: "Ready to set up your first Tag."
        case .failed: "You can safely try again."
        case .home: "Each Tag is its own assistant in Slack."
        case .connect: "Connect a Slack workspace"
        }
    }

    var welcome: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Tag installs into your user folder and does not need an administrator password. Your existing configuration is kept.")
                .fixedSize(horizontal: false, vertical: true)
            Label("About 650 MB of disk space", systemImage: "arrow.down.circle")
            Label("Keep this Mac online until it finishes", systemImage: "wifi")
            HStack {
                Spacer()
                SecondaryButton(title: "Quit") { NSApp.terminate(nil) }
                PrimaryButton(title: "Install") { installer.start() }.keyboardShortcut(.defaultAction)
            }
        }
    }

    var progress: some View {
        VStack(alignment: .leading, spacing: 14) {
            GeometryReader { g in
                ZStack(alignment: .leading) {
                    Capsule().fill(.quaternary)
                    Capsule().fill(installer.phase == .failed ? Color.red : Color.accentColor)
                        .frame(width: g.size.width * installer.progress)
                }
            }
            .frame(height: 6)
            VStack(alignment: .leading, spacing: 10) {
                ForEach(installer.steps) { StepRow(step: $0) }
            }
            .padding(14)
            .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 10))
            if installer.phase == .failed {
                Text(installer.failure).foregroundStyle(.red).textSelection(.enabled)
            }
            DisclosureGroup("Details", isExpanded: $installer.showLog) {
                ScrollView {
                    Text(installer.log).font(.system(.caption, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
                }
                .frame(height: 140)
            }
            HStack {
                // The version matters for support, not as a headline: keep it quiet.
                if !installer.version.isEmpty && installer.phase != .installing {
                    Text("Version \(installer.version)").font(.caption).foregroundStyle(.tertiary)
                        .textSelection(.enabled)
                }
                Spacer()
                switch installer.phase {
                case .installing: SecondaryButton(title: "Cancel") { installer.cancel() }
                case .failed:
                    SecondaryButton(title: "Copy details", icon: "doc.on.doc") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(installer.log, forType: .string)
                    }
                    SecondaryButton(title: "Quit") { NSApp.terminate(nil) }
                default:
                    PrimaryButton(title: "Continue") { installer.showHome() }.keyboardShortcut(.defaultAction)
                }
            }
        }
    }
}

#if !TAG_SETUP_TEST
@main
struct TagApp: App {
    @NSApplicationDelegateAdaptor var delegate: Delegate
    var body: some Scene {
        WindowGroup(id: "main") { ContentView(installer: delegate.installer) }.windowResizability(.contentSize)
            .commands { CommandGroup(replacing: .appInfo) { AboutMenuItem() } }
        Window("About Tag", id: "about") { AboutView() }.windowResizability(.contentSize)
        MenuBarExtra { MenuBarContent(tags: delegate.installer.tags) } label: { MenuBarLabel(tags: delegate.installer.tags) }
    }
}

struct AboutMenuItem: View {
    @Environment(\.openWindow) private var openWindow
    var body: some View { Button("About Tag") { openWindow(id: "about") } }
}

struct AboutView: View {
    var body: some View {
        VStack(spacing: 14) {
            if let image = ContentView.icon {
                Image(nsImage: image).resizable().frame(width: 72, height: 72).clipShape(RoundedRectangle(cornerRadius: 16))
            }
            Text("Tag").font(.title2.bold())
            Text(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "")
                .foregroundStyle(.secondary)
            Text("Your personal assistant, in Slack.").foregroundStyle(.secondary)
            Divider()
            CommunityLinks()
        }
        .padding(28)
        .frame(width: 460)
    }
}

#endif

final class Delegate: NSObject, NSApplicationDelegate {
    @MainActor let installer = Installer()
    // Prototype aid: TAG_INSTALLER_RENDER=<dir> writes each screen to PNG and quits.
    @MainActor func applicationDidFinishLaunching(_: Notification) {
        guard let dir = ProcessInfo.processInfo.environment["TAG_INSTALLER_RENDER"] else { return startMenuBar() }
        renderingPreview = true
        let states: [(String, (Installer) -> Void)] = [
            ("welcome", { i in i.phase = .welcome }),
            ("progress", { i in i.phase = .installing; i.version = "0.2.0"
                for n in 0..<3 { i.steps[n].state = .done }; i.steps[3].state = .running; i.progress = 0.5 }),
            ("done", { i in i.phase = .finished; i.version = "0.2.0"; i.progress = 1
                for n in i.steps.indices { i.steps[n].state = .done } }),
            ("home", { i in i.phase = .home; i.tags.rows = [
                TagRow(id: "t0klovr1-a0maya01", valid: true, state: "running", slackWorkspace: "T0KLOVR1",
                       slackName: "Maya's Tag", workspaceName: "Klovr", main: true),
                TagRow(id: "t0klovr1-a0rese02", valid: true, state: "stopped", slackWorkspace: "T0KLOVR1",
                       slackName: "Research Tag", workspaceName: "Klovr", nickname: "research"),
                TagRow(id: "t0acme01-a0ops003", valid: true, state: "running", slackWorkspace: "T0ACME01",
                       slackName: "Ops Tag", workspaceName: "Acme Inc"),
                TagRow(id: "new-tag", valid: true, state: "setup_incomplete", slackWorkspace: "T0ACME01",
                       workspaceName: "Acme Inc"),
            ] }),
            ("anim-1", { _ in }), ("anim-2", { _ in }), ("anim-3", { _ in }),
            ("home-rename", { i in i.phase = .home; i.tags.rows = [
                TagRow(id: "t0klovr1-a0maya01", valid: true, state: "running", slackWorkspace: "T0KLOVR1",
                       slackName: "Maya's Tag", workspaceName: "Klovr", main: true),
                TagRow(id: "t0klovr1-a0rese02", valid: true, state: "stopped", slackWorkspace: "T0KLOVR1",
                       slackName: "Research Tag", workspaceName: "Klovr"),
            ]; i.tags.renaming = "t0klovr1-a0rese02"; i.tags.newName = "Market Research" }),
            ("connect-copy", { i in i.phase = .connect; i.session.start(firstTag: true) }),
            ("connect-slack", { i in i.phase = .connect; i.session.start(firstTag: true); i.session.signInStep = .slack }),
            ("connect-code", { i in i.phase = .connect; i.session.start(firstTag: true); i.session.signInStep = .code; i.session.code = "NwrFLzAy" }),
            ("connect-workspace", { i in i.phase = .connect; i.session.start(firstTag: true); i.session.send("x") }),
            ("connect-channels", { i in i.phase = .connect; i.session.start(firstTag: true); for _ in 0..<2 { i.session.send("x") } }),
            ("connect-done", { i in i.phase = .connect; i.session.start(firstTag: true); for _ in 0..<3 { i.session.send("x") } }),
            ("failed", { i in i.phase = .failed; i.progress = 0.4
                for n in 0..<3 { i.steps[n].state = .done }; i.steps[3].state = .failed
                i.failure = "Installing runtime dependencies failed; see the download or preparation error above" }),
        ]
        for (name, configure) in states {
            let installer = Installer(); configure(installer)
            let frame = ["anim-1": 0, "anim-2": 1, "anim-3": 2][name]
            let view: AnyView = frame.map { AnyView(SlackSequence(frameOverride: $0).padding(20).frame(width: 400, height: 170)) }
                ?? AnyView(ContentView(installer: installer))
            let renderer = ImageRenderer(content: view.background(Color(nsColor: .windowBackgroundColor)))
            renderer.scale = 2
            if let image = renderer.nsImage, let tiff = image.tiffRepresentation,
               let png = NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:]) {
                try? png.write(to: URL(fileURLWithPath: dir).appendingPathComponent("installer-\(name).png"))
            }
        }
        exit(0)
    }
    // Tag keeps running in the menu bar after its window closes.
    func applicationShouldTerminateAfterLastWindowClosed(_: NSApplication) -> Bool { false }

    @MainActor private func startMenuBar() {
        let tags = installer.tags
        if LoginSettings.launchedAtLogin && tagInstalled {
            // Opened by the login item: stay in the menu bar, no window or Dock icon.
            NSApp.setActivationPolicy(.accessory)
            DispatchQueue.main.async { NSApp.windows.filter { $0.isVisible && $0.canBecomeMain }.forEach { $0.close() } }
            Task { await LoginSettings.shared.restore(tags) }
        } else {
            Task { await tags.refresh() }
        }
        // Keep the menu bar status current without anyone opening the window.
        Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { _ in Task { @MainActor in await tags.refresh() } }
        // Hide the Dock icon once the last window closes; the menu bar remains.
        NotificationCenter.default.addObserver(forName: NSWindow.willCloseNotification, object: nil, queue: .main) { note in
            let closing = note.object as? NSWindow
            DispatchQueue.main.async {
                if !NSApp.windows.contains(where: { $0 !== closing && $0.isVisible && $0.canBecomeMain }) {
                    NSApp.setActivationPolicy(.accessory)
                }
            }
        }
    }
}
