// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Prototype: once Tag is installed, the same app lists every Tag on this Mac,
// starts and stops them, and adds new ones by connecting Slack in-window.
import AppKit
import SwiftUI

let tagHomeBin = "\(NSHomeDirectory())/.local/bin"
let toolPath = "\(tagHomeBin):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
let demoMode = ProcessInfo.processInfo.environment["TAG_INSTALLER_DEMO"] != nil

/// The `tag` command the app drives. TAG_CLI points it at another build,
/// such as a source checkout during development (see run.sh).
let tagCLI = ProcessInfo.processInfo.environment["TAG_CLI"] ?? "tag"

var tagInstalled: Bool { FileManager.default.isExecutableFile(atPath: "\(tagHomeBin)/tag") }

/// Runs a command off the main thread and returns its exit status and output.
func run(_ arguments: [String]) async -> (Int32, String) {
    await Task.detached {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = arguments.first == "tag" ? [tagCLI] + arguments.dropFirst() : arguments
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = toolPath
        env["NO_COLOR"] = "1"
        p.environment = env
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        do { try p.run() } catch { return (127, error.localizedDescription) }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return (p.terminationStatus, String(decoding: data, as: UTF8.self))
    }.value
}

struct TagRow: Identifiable, Decodable {
    let id: String
    let valid: Bool
    let state: String?
    let slackWorkspace: String?
    var slackName: String? = nil
    var workspaceName: String? = nil
    var main: Bool? = nil
    var nickname: String? = nil
    var avatar: String? = nil

    enum CodingKeys: String, CodingKey {
        case id, valid, state, main, nickname, avatar
        case slackWorkspace = "slack_workspace", slackName = "slack_name", workspaceName = "workspace_name"
    }

    /// People see the Tag's Slack name and workspace; the ID is only for commands.
    var title: String { slackName ?? "New Tag" }
    var subtitle: String { workspaceName ?? slackWorkspace ?? "Slack not connected yet" }

    var label: (String, Color) {
        switch state {
        case "running": ("Online", .green)
        case "stopped", "configured": ("Offline", .secondary)
        case "not_configured", "setup_incomplete": ("Needs setup", .orange)
        default: ("Needs attention", .red)
        }
    }

    /// Always name the Tag explicitly; IDs are unambiguous.
    func command(_ action: String) -> [String] { ["tag", id, action] }
}

@MainActor
final class Tags: ObservableObject {
    @Published var rows: [TagRow] = []
    @Published var busy: Set<String> = []
    @Published var error = ""
    var finishSetup: ((TagRow) -> Void)?
    @Published var renaming: String?
    @Published var newName = ""

    /// Tags grouped by Slack workspace, in list order. Several Tags can share one.
    var groups: [(key: String, label: String, rows: [TagRow])] {
        var order: [String] = []
        var byKey: [String: [TagRow]] = [:]
        for row in rows {
            let key = row.slackWorkspace ?? ""
            if byKey[key] == nil { order.append(key) }
            byKey[key, default: []].append(row)
        }
        return order.map { key in
            let members = byKey[key]!
            return (key, members.first?.workspaceName ?? (key.isEmpty ? "Not connected yet" : key), members)
        }
    }

    func workspace(_ team: String, _ action: String) async {
        busy.insert(team)
        defer { busy.remove(team) }
        let (status, _) = await run(["tag", action, "--workspace", team, "--json"])
        error = status == 0 ? "" : "Some Tags in this workspace need attention."
        await refresh()
    }

    func beginRename(_ row: TagRow) { newName = row.slackName ?? ""; renaming = row.id }

    func rename(_ row: TagRow) async {
        let name = newName.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }
        busy.insert(row.id)
        defer { busy.remove(row.id) }
        let (status, output) = await run(["tag", row.id, "rename", name, "--json"])
        if status == 0 { renaming = nil; error = "" } else {
            error = output.split(separator: "\n").last.map(String.init) ?? "Rename failed."
        }
        await refresh()
    }

    func refresh() async {
        if demoMode { return }
        let (status, output) = await run(["tag", "list", "--json"])
        struct Listing: Decodable { let tags: [TagRow] }
        guard status == 0, let start = output.firstIndex(of: "{"),
              let listing = try? JSONDecoder().decode(Listing.self, from: Data(output[start...].utf8)) else {
            error = "Couldn't read your Tags."
            return
        }
        rows = listing.tags
    }

    func toggle(_ row: TagRow) async {
        busy.insert(row.id)
        defer { busy.remove(row.id) }
        let action = row.state == "running" ? "stop" : (row.label.0 == "Needs setup" ? "setup" : "start")
        if action == "setup" { finishSetup?(row); return }
        let (status, output) = await run(row.command(action))
        error = status == 0 ? "" : output.split(separator: "\n").last.map(String.init) ?? "Tag \(action) failed."
        if status == 0 {
            // Remembered so "Restore Running Tags at Login" brings back the same set.
            var wanted = LoginSettings.shared.wantedRunning
            if action == "start" { wanted.insert(row.id) } else { wanted.remove(row.id) }
            LoginSettings.shared.wantedRunning = wanted
        }
        await refresh()
    }
}

struct SlackPerson: Decodable, Identifiable {
    let id: String
    let name: String
    let username: String
    let imageURL: String

    enum CodingKeys: String, CodingKey { case id, name, username, imageURL = "image_url" }

    func matches(_ query: String) -> Bool {
        let query = query.trimmingCharacters(in: .whitespacesAndNewlines)
        return query.isEmpty || [name, username, id].contains { $0.localizedCaseInsensitiveContains(query) }
    }
}

/// One question from `tag setup --json` / `tag add --json`.
struct SetupQuestion: Decodable {
    let kind: String
    let prompt: String
    var options: [String]?
    var defaultIndex: Int?
    var defaultText: String?
    var selected: [Int]?
    var signInLine: String?
    var people: [SlackPerson]?

    /// Set by Tag when it can return to the previous question (never after Slack-side changes).
    var canGoBack = false

    enum CodingKeys: String, CodingKey {
        case kind, prompt, options, selected, people, signInLine = "sign_in_line", value = "default", canGoBack = "can_go_back"
    }

    init(kind: String, prompt: String, options: [String]? = nil, defaultIndex: Int? = nil,
         selected: [Int]? = nil, signInLine: String? = nil, people: [SlackPerson]? = nil) {
        self.kind = kind; self.prompt = prompt; self.options = options
        self.defaultIndex = defaultIndex; self.selected = selected; self.signInLine = signInLine; self.people = people
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kind = try c.decode(String.self, forKey: .kind)
        prompt = try c.decode(String.self, forKey: .prompt)
        options = try c.decodeIfPresent([String].self, forKey: .options)
        selected = try c.decodeIfPresent([Int].self, forKey: .selected)
        people = try c.decodeIfPresent([SlackPerson].self, forKey: .people)
        signInLine = try c.decodeIfPresent(String.self, forKey: .signInLine)
        canGoBack = (try? c.decodeIfPresent(Bool.self, forKey: .canGoBack)) ?? false
        defaultIndex = try? c.decodeIfPresent(Int.self, forKey: .value)
        defaultText = try? c.decodeIfPresent(String.self, forKey: .value)
    }
}

/// Drives Tag's own setup flow over its JSON-lines protocol, so the app
/// holds no setup logic of its own: it only draws the questions Tag asks.
@MainActor
final class SetupSession: ObservableObject {
    enum SignInStep: Int { case copy, slack, code }
    enum Outcome { case complete, paused, failed }

    @Published var question: SetupQuestion?
    @Published var signInStep: SignInStep = .copy
    @Published var copied = false
    @Published var code = ""
    @Published var text = ""
    @Published var checked: Set<Int> = []
    @Published var status = "Starting…"
    @Published var outcome: Outcome?
    @Published var error = ""
    @Published var tagID = ""
    private var process: Process?
    private var input: FileHandle?
    private var buffer = Data()
    private var demoStep = 0
    private var stderrTail = ""

    /// Turn a setup process that ended without a result into something actionable.
    private func explainExit() -> String {
        let last = stderrTail.split(separator: "\n").last.map(String.init) ?? ""
        if last.contains("--json supports") {
            return "This version of Tag can't be set up from the app yet. Update Tag (tag upgrade), then try again."
        }
        if last.isEmpty { return "Setup stopped unexpectedly. Run tag setup in Terminal to see why." }
        return last.replacingOccurrences(of: "tag_cli.py: error: ", with: "")
    }

    /// The first Tag finishes setup in place; later ones go through `tag add`.
    func start(firstTag: Bool) { start(arguments: firstTag ? ["setup"] : ["add"]) }

    func start(arguments: [String]) {
        if demoMode || renderingPreview { return demoAdvance() }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = [tagCLI] + arguments + ["--json"]
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = toolPath
        env["NO_COLOR"] = "1"
        p.environment = env
        let out = Pipe(), inp = Pipe()
        p.standardOutput = out
        p.standardInput = inp
        // Keep stderr: if setup can't start, its last line says why.
        let err = Pipe()
        p.standardError = err
        err.fileHandleForReading.readabilityHandler = { handle in
            let text = String(decoding: handle.availableData, as: UTF8.self)
            Task { @MainActor in self.stderrTail = (self.stderrTail + text).suffix(2000).description }
        }
        out.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            Task { @MainActor in self.receive(data) }
        }
        p.terminationHandler = { _ in
            Task { @MainActor in if self.outcome == nil { self.outcome = .failed; self.error = self.explainExit() } }
        }
        do { try p.run(); process = p; input = inp.fileHandleForWriting } catch { outcome = .failed; self.error = error.localizedDescription }
    }

    private func receive(_ data: Data) {
        buffer.append(data)
        while let newline = buffer.firstIndex(of: 0x0A) {
            let line = buffer[buffer.startIndex..<newline]
            buffer.removeSubrange(buffer.startIndex...newline)
            // Anything that is not a JSON object is ordinary output; ignore it.
            guard let object = try? JSONSerialization.jsonObject(with: line) as? [String: Any],
                  let type = object["type"] as? String else { continue }
            switch type {
            case "message": status = object["text"] as? String ?? status
            case "question":
                guard let q = try? JSONDecoder().decode(SetupQuestion.self, from: line) else { continue }
                present(q)
            case "result":
                tagID = object["tag"] as? String ?? ""
                error = object["error"] as? String ?? ""
                outcome = ["complete": .complete, "paused": .paused][object["status"] as? String ?? ""] ?? .failed
                question = nil
            default: break
            }
        }
    }

    private func present(_ q: SetupQuestion) {
        if q.kind == "slack_login", question?.kind == "slack_login" {
            error = "Slack didn't accept that code. Check it and try again."
            signInStep = .code
        } else if q.kind == "slack_login" {
            signInStep = .copy; copied = false
        }
        code = ""; text = q.defaultText ?? ""; checked = Set(q.selected ?? [])
        question = q
    }

    func send(_ answer: Any) {
        if demoMode || renderingPreview { return demoAnswer(answer) }
        guard let data = try? JSONSerialization.data(withJSONObject: ["answer": answer]) else { return }
        if question?.kind != "slack_login" {
            error = ""
            status = "Loading…"
            question = nil
        }
        input?.write(data + Data([0x0A]))
    }

    /// Ask Tag to return to the previous question; it comes back with the earlier answer selected.
    func back() {
        if demoMode || renderingPreview { demoStep = max(0, demoStep - 2); return demoAdvance() }
        guard let data = try? JSONSerialization.data(withJSONObject: ["back": true]) else { return }
        error = ""
        question = nil
        input?.write(data + Data([0x0A]))
    }

    func copySignInLine() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(question?.signInLine ?? "", forType: .string)
        copied = true
        signInStep = .slack
    }

    func pasteCode() {
        code = NSPasteboard.general.string(forType: .string)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    func cancel() {
        if let data = try? JSONSerialization.data(withJSONObject: ["answer": NSNull(), "pause": true]) {
            input?.write(data + Data([0x0A]))
        }
        process?.terminate()
    }

    func startTag() async {
        _ = await run(tagID.isEmpty ? ["tag", "start"] : ["tag", tagID, "start"])
    }

    private func demoAnswer(_ answer: Any) {
        if question?.kind == "people" {
            if answer as? String == "manual" {
                present(SetupQuestion(kind: "text", prompt: "Your Slack member ID (profile > More > Copy member ID)"))
                return
            }
            guard let id = answer as? String, question?.people?.contains(where: { $0.id == id }) == true else { return }
        }
        demoAdvance()
    }

    /// A scripted walk through the questions Tag asks, for demos and previews.
    private func demoAdvance() {
        let script: [SetupQuestion] = [
            SetupQuestion(kind: "slack_login", prompt: "Sign in to Slack", signInLine: "/slackauthticket MjA1demo"),
            SetupQuestion(kind: "choose", prompt: "Choose a workspace",
                          options: ["Acme Inc", "Connect another workspace", "Exit · finish setup later"]),
            SetupQuestion(kind: "multi", prompt: "Choose channels", options: ["#general", "#launch", "#random"], selected: [0]),
        ]
        error = ""
        if demoStep < script.count {
            var next = script[demoStep]
            next.canGoBack = demoStep > 0
            present(next)
            demoStep += 1
        }
        else { question = nil; tagID = "acme"; outcome = .complete }
    }
}

// MARK: - Shared styling

/// ImageRenderer can't draw AppKit-backed controls, so previews swap them out.
@MainActor var renderingPreview = false

struct PrimaryButton: View {
    let title: String
    var icon: String?
    var disabled = false
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if let icon { Image(systemName: icon) }
                Text(title)
            }
            .font(.body.weight(.semibold))
            .foregroundStyle(.white)
            .padding(.horizontal, 16).padding(.vertical, 8)
            .background(Color.accentColor.opacity(disabled ? 0.4 : 1), in: RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
        .disabled(disabled)
    }
}

struct SecondaryButton: View {
    let title: String
    var icon: String?
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if let icon { Image(systemName: icon) }
                Text(title)
            }
            .padding(.horizontal, 14).padding(.vertical, 8)
            .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
    }
}

struct BackButton: View {
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Label("Back", systemImage: "chevron.left").font(.callout).foregroundStyle(.secondary)
        }
        .buttonStyle(.plain)
    }
}

struct TextButton: View {
    let title: String
    let action: () -> Void
    var body: some View {
        Button(action: action) { Text(title).font(.callout).foregroundStyle(.secondary) }.buttonStyle(.plain)
    }
}

struct Field: View {
    let placeholder: String
    @Binding var text: String
    var monospaced = false
    var body: some View {
        Group {
            if renderingPreview {
                Text(text.isEmpty ? placeholder : text).foregroundStyle(text.isEmpty ? .tertiary : .primary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                TextField(placeholder, text: $text).textFieldStyle(.plain)
            }
        }
        .font(monospaced ? .title3.monospaced() : .body)
        .padding(.horizontal, 12).padding(.vertical, 9)
        .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Color.primary.opacity(0.15)))
    }
}

// MARK: - Your Tags

struct StatusDot: View {
    let text: String
    let color: Color
    var body: some View {
        HStack(spacing: 6) {
            Circle().fill(color).frame(width: 8, height: 8)
                .overlay(Circle().stroke(color.opacity(0.3), lineWidth: 3))
            Text(text).font(.callout).foregroundStyle(.secondary)
        }
    }
}

struct TagsView: View {
    @ObservedObject var tags: Tags
    let add: () -> Void

    var online: Int { tags.rows.filter { $0.state == "running" }.count }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(alignment: .center, spacing: 10) {
                if let icon = ContentView.icon {
                    Image(nsImage: icon).resizable().frame(width: 30, height: 30).clipShape(RoundedRectangle(cornerRadius: 7))
                }
                VStack(alignment: .leading, spacing: 0) {
                    Text("Tag").font(.title3.weight(.bold))
                    Text(tags.rows.isEmpty ? "No Tags yet" : "\(online) of \(tags.rows.count) online")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                IconButton(symbol: "arrow.clockwise", help: "Refresh") { Task { await tags.refresh() } }
                PrimaryButton(title: "Add Tag", icon: "plus", action: add)
            }
            if tags.rows.isEmpty {
                VStack(spacing: 10) {
                    WaterdropAvatar(row: nil, size: 56)
                    Text("Bring your first Tag to Slack").font(.headline)
                    Text("Connect a workspace and Tag sets up its own Slack app.")
                        .foregroundStyle(.secondary).multilineTextAlignment(.center)
                    PrimaryButton(title: "Add Tag", icon: "plus", action: add).padding(.top, 4)
                }
                .frame(maxWidth: .infinity).padding(.vertical, 36)
                .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 14))
            }
            ForEach(tags.groups, id: \.key) { group in
                VStack(alignment: .leading, spacing: 8) {
                    WorkspaceHeader(group: group, tags: tags)
                    VStack(spacing: 0) {
                        ForEach(Array(group.rows.enumerated()), id: \.element.id) { index, row in
                            if index > 0 { Divider().padding(.leading, 62) }
                            TagRowView(row: row, tags: tags) { Task { await tags.toggle(row) } }
                        }
                    }
                    .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 14))
                    .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(Color.primary.opacity(0.07)))
                }
            }
            if !tags.error.isEmpty {
                Label(tags.error, systemImage: "exclamationmark.triangle.fill").font(.callout).foregroundStyle(.red)
            }
        }
    }
}

struct WorkspaceHeader: View {
    let group: (key: String, label: String, rows: [TagRow])
    @ObservedObject var tags: Tags

    var body: some View {
        let running = group.rows.filter { $0.state == "running" }.count
        let startable = group.rows.filter { $0.label.0 != "Needs setup" }.count
        HStack(spacing: 8) {
            Text(group.label).font(.subheadline.weight(.semibold))
            Text("\(running)/\(group.rows.count)").font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
                .padding(.horizontal, 6).padding(.vertical, 1)
                .background(Color.primary.opacity(0.06), in: Capsule())
            Spacer()
            if !group.key.isEmpty && startable > 1 {
                let all = running >= startable
                Button { Task { await tags.workspace(group.key, all ? "stop" : "start") } } label: {
                    Label(all ? "Stop all" : "Start all", systemImage: all ? "stop.fill" : "play.fill")
                        .font(.caption.weight(.medium)).foregroundStyle(Color.accentColor)
                }
                .buttonStyle(.plain).disabled(tags.busy.contains(group.key))
            }
        }
        .padding(.horizontal, 4)
    }
}

/// Each Tag's own Slack profile picture; otherwise the Tag waterdrop, tinted per Tag.
struct WaterdropAvatar: View {
    let row: TagRow?
    var size: CGFloat = 38

    var body: some View {
        Group {
            if let path = row?.avatar, let image = NSImage(contentsOfFile: path) {
                Image(nsImage: image).resizable().interpolation(.none)
            } else if let icon = ContentView.icon {
                Image(nsImage: icon).resizable().interpolation(.none).hueRotation(.degrees(hue))
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: size * 0.26))
        .saturation(row?.label.0 == "Needs setup" ? 0.2 : 1)
    }

    var hue: Double {
        guard let id = row?.id else { return 0 }
        return Double(id.unicodeScalars.reduce(0) { $0 &+ Int($1.value) } % 6) * 60
    }
}

/// A compact on/off switch drawn in SwiftUI (renders in previews too).
struct PowerSwitch: View {
    let on: Bool
    let busy: Bool
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            ZStack(alignment: on ? .trailing : .leading) {
                Capsule().fill(on ? Color.green : Color.primary.opacity(0.15))
                Circle().fill(.white).frame(width: 20, height: 20).padding(2)
                    .shadow(color: .black.opacity(0.2), radius: 1, y: 1)
                    .overlay { if busy { Spinner().frame(width: 12, height: 12) } }
            }
            .frame(width: 40, height: 24)
            .animation(.easeInOut(duration: 0.15), value: on)
        }
        .buttonStyle(.plain).disabled(busy)
        .help(on ? "Stop this Tag" : "Start this Tag")
        .accessibilityLabel(on ? "Stop" : "Start")
    }
}

struct IconButton: View {
    let symbol: String
    let help: String
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 13, weight: .medium)).foregroundStyle(.secondary)
                .frame(width: 28, height: 28).background(Color.primary.opacity(0.05), in: Circle())
        }
        .buttonStyle(.plain).help(help)
    }
}

struct TagRowView: View {
    let row: TagRow
    @ObservedObject var tags: Tags
    let action: () -> Void
    var busy: Bool { tags.busy.contains(row.id) }

    var body: some View {
        HStack(spacing: 12) {
            WaterdropAvatar(row: row)
            if tags.renaming == row.id {
                Field(placeholder: "Name in Slack", text: $tags.newName)
                SecondaryButton(title: "Cancel") { tags.renaming = nil }
                PrimaryButton(title: "Save", disabled: busy || tags.newName.isEmpty) { Task { await tags.rename(row) } }
            } else {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(row.title).font(.body.weight(.semibold)).lineLimit(1)
                        if row.main == true {
                            Text("Main").font(.caption2.weight(.semibold)).foregroundStyle(Color.accentColor)
                                .padding(.horizontal, 5).padding(.vertical, 1)
                                .background(Color.accentColor.opacity(0.12), in: Capsule())
                                .help("tag start without a name uses this Tag")
                        }
                    }
                    HStack(spacing: 5) {
                        Circle().fill(row.label.1).frame(width: 7, height: 7)
                        Text(row.label.0)
                        if let nickname = row.nickname {
                            Text("·")
                            Text("tag \(nickname)").font(.caption.monospaced())
                        }
                    }
                    .font(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 8)
                // One fixed trailing column so every row's control lines up.
                Group {
                if row.label.0 == "Needs setup" {
                    Button(action: action) {
                        Text("Finish setup").font(.callout.weight(.medium)).foregroundStyle(.orange)
                            .padding(.horizontal, 10).padding(.vertical, 5)
                            .background(Color.orange.opacity(0.12), in: Capsule())
                    }
                    .buttonStyle(.plain).disabled(busy)
                } else {
                    PowerSwitch(on: row.state == "running", busy: busy, action: action)
                }
                }
                .frame(width: 112, alignment: .trailing)
                if renderingPreview {
                    Image(systemName: "ellipsis").foregroundStyle(.secondary).frame(width: 22, height: 22)
                } else {
                Menu {
                    if row.slackName != nil { Button("Rename…") { tags.beginRename(row) } }
                    Button("Copy command") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString("tag \(row.nickname ?? row.id) start", forType: .string)
                    }
                    Button("Show working folder") {
                        NSWorkspace.shared.open(URL(fileURLWithPath: "\(NSHomeDirectory())/Tag/\(row.id)"))
                    }
                } label: {
                    Image(systemName: "ellipsis").foregroundStyle(.secondary).frame(width: 22, height: 22)
                }
                .menuStyle(.borderlessButton).menuIndicator(.hidden)
                .frame(width: 22, height: 22)
                }
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 10)
        .help("tag \(row.nickname ?? row.id)")
    }
}

// MARK: - Add a Tag

struct ConnectView: View {
    @ObservedObject var session: SetupSession
    let cancel: () -> Void
    let done: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                if let q = session.question, q.kind == "slack_login" {
                    Text("Step \(session.signInStep.rawValue + 1) of 3")
                        .font(.caption.weight(.medium)).foregroundStyle(.secondary)
                }
                Spacer()
                if session.outcome == nil { TextButton(title: "Cancel") { session.cancel(); cancel() } }
            }
            .padding(.bottom, -10)
            if let outcome = session.outcome {
                result(outcome)
            } else if let q = session.question {
                switch q.kind {
                case "slack_login": signIn(q)
                case "choose": choose(q)
                case "multi": multi(q)
                case "people": PeoplePicker(people: q.people ?? [], select: session.send, search: $session.text)
                case "confirm": confirm(q)
                default: textQuestion(q)
                }
            } else {
                HStack(spacing: 10) { Spinner().frame(width: 16, height: 16); Text(session.status).foregroundStyle(.secondary) }
                    .padding(.vertical, 30).frame(maxWidth: .infinity)
            }
            if !session.error.isEmpty && session.outcome == nil {
                Label(session.error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red).font(.callout)
            }
        }
    }

    // MARK: Slack sign-in

    @ViewBuilder func signIn(_ q: SetupQuestion) -> some View {
        HStack(spacing: 6) {
            ForEach(0..<3, id: \.self) { n in
                Capsule().fill(n <= session.signInStep.rawValue ? Color.accentColor : Color.primary.opacity(0.12)).frame(height: 4)
            }
        }
        switch session.signInStep {
        case .copy:
            step(title: "Copy your sign-in line", body: "Tag creates a one-time line that tells Slack this Mac is yours.",
                 art: SlackComposer(pasted: false)) {
                PrimaryButton(title: "Copy sign-in line", icon: "doc.on.doc", action: session.copySignInLine)
            }
        case .slack:
            step(title: "Paste it in Slack",
                 body: "Open the workspace for this Tag, paste into any message box and send. Then click Confirm.",
                 art: SlackSequence()) {
                BackButton { session.signInStep = .copy }
                Spacer()
                SecondaryButton(title: "Open Slack", icon: "arrow.up.right") { NSWorkspace.shared.open(URL(string: "slack://open")!) }
                PrimaryButton(title: "I clicked Confirm") { session.signInStep = .code }
            }
        case .code:
            step(title: "Paste the code", body: "Slack now shows a short code. Copy it and paste it here.",
                 art: SlackCodeModal()) { EmptyView() }
                .padding(.bottom, -14)
            HStack(spacing: 8) {
                Field(placeholder: "Code from Slack", text: $session.code, monospaced: true)
                SecondaryButton(title: "Paste", icon: "doc.on.clipboard", action: session.pasteCode)
            }
            HStack {
                BackButton { session.signInStep = .slack }
                Spacer()
                TextButton(title: "Copy the line again") { session.signInStep = .copy }
                PrimaryButton(title: "Connect", disabled: session.code.isEmpty) { session.send(session.code) }
            }
        }
    }

    func step<Art: View, Actions: View>(title: String, body: String, art: Art, @ViewBuilder actions: () -> Actions) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            art.frame(maxWidth: .infinity).frame(height: 150)
                .background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 12))
            heading(title, body)
            HStack(spacing: 8) { actions() }.frame(maxWidth: .infinity, alignment: .trailing)
        }
    }

    func heading(_ title: String, _ body: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.title3.weight(.semibold))
            if !body.isEmpty { Text(body).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
        }
    }

    // MARK: Generic questions from Tag's setup

    /// Friendlier titles for questions the app knows; anything else shows Tag's own wording.
    func title(_ prompt: String) -> (String, String) {
        switch prompt {
        case "Choose a workspace": ("Which workspace?", "Pick the Slack workspace this Tag will work in.")
        case "Choose channels": ("Where should Tag respond?", "Tag answers and remembers conversations in these channels.")
        case "Create this app?": ("Create the Slack app", "Slack creates and installs Tag's app in this workspace.")
        default: (prompt, "")
        }
    }

    /// Back to Tag's previous question, when Tag says that's still possible.
    @ViewBuilder func backButton(_ q: SetupQuestion) -> some View {
        if q.canGoBack { BackButton(action: session.back) }
    }

    func choose(_ q: SetupQuestion) -> some View {
        let (heading, detail) = title(q.prompt)
        let options = q.options ?? []
        return VStack(alignment: .leading, spacing: 14) {
            self.heading(heading, detail)
            VStack(spacing: 0) {
                ForEach(Array(options.enumerated()).filter { $0.element != "Exit · finish setup later" }, id: \.offset) { index, label in
                    if index > 0 { Divider() }
                    Button { session.send(index) } label: {
                        HStack {
                            Text(label).foregroundStyle(.primary)
                            Spacer()
                            Image(systemName: "chevron.right").font(.caption).foregroundStyle(.tertiary)
                        }
                        .padding(.horizontal, 14).padding(.vertical, 11).contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Color.primary.opacity(0.08)))
            backButton(q)
        }
    }

    func multi(_ q: SetupQuestion) -> some View {
        let (heading, detail) = title(q.prompt)
        let options = q.options ?? []
        return VStack(alignment: .leading, spacing: 14) {
            self.heading(heading, detail)
            VStack(spacing: 0) {
                ForEach(Array(options.enumerated()), id: \.offset) { index, label in
                    if index > 0 { Divider() }
                    Button {
                        if session.checked.contains(index) { session.checked.remove(index) } else { session.checked.insert(index) }
                    } label: {
                        HStack(spacing: 10) {
                            Image(systemName: session.checked.contains(index) ? "checkmark.square.fill" : "square")
                                .foregroundStyle(session.checked.contains(index) ? Color.accentColor : .secondary)
                            Text(label)
                            Spacer()
                        }
                        .padding(.horizontal, 14).padding(.vertical, 10).contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Color.primary.opacity(0.08)))
            HStack {
                backButton(q)
                Spacer()
                PrimaryButton(title: "Continue", disabled: session.checked.isEmpty) { session.send(session.checked.sorted()) }
            }
        }
    }

    func confirm(_ q: SetupQuestion) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            heading(q.prompt, "")
            HStack {
                backButton(q)
                Spacer()
                SecondaryButton(title: "No") { session.send(false) }
                PrimaryButton(title: "Yes") { session.send(true) }
            }
        }
    }

    func textQuestion(_ q: SetupQuestion) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            heading(q.prompt, q.kind == "secret" ? "Stays on this Mac." : "")
            if q.kind == "secret" && !renderingPreview {
                SecureField(q.prompt, text: $session.text).textFieldStyle(.roundedBorder)
            } else {
                Field(placeholder: q.prompt, text: $session.text)
            }
            HStack {
                backButton(q)
                Spacer()
                PrimaryButton(title: "Continue", disabled: session.text.isEmpty) { session.send(session.text) }
            }
        }
    }

    // MARK: Outcome

    @ViewBuilder func result(_ outcome: SetupSession.Outcome) -> some View {
        switch outcome {
        case .complete:
            VStack(alignment: .leading, spacing: 14) {
                Label("Your Tag is ready", systemImage: "checkmark.circle.fill")
                    .font(.title3.weight(.semibold)).foregroundStyle(.green)
                Text("Start it, then mention it in one of the channels you picked.").foregroundStyle(.secondary)
                Divider()
                CommunityLinks()
                HStack {
                    Spacer()
                    SecondaryButton(title: "Later", action: done)
                    PrimaryButton(title: "Start Tag") { Task { await session.startTag(); done() } }
                }
            }
        case .paused:
            VStack(alignment: .leading, spacing: 14) {
                heading("Progress saved", "You can finish setting up this Tag from Your Tags at any time.")
                HStack { Spacer(); PrimaryButton(title: "Done", action: done) }
            }
        case .failed:
            VStack(alignment: .leading, spacing: 14) {
                Label("Setup stopped", systemImage: "exclamationmark.triangle.fill")
                    .font(.title3.weight(.semibold)).foregroundStyle(.red)
                Text(session.error.isEmpty ? "Something went wrong. Your progress is saved." : session.error)
                    .foregroundStyle(.secondary).textSelection(.enabled)
                HStack { Spacer(); PrimaryButton(title: "Done", action: done) }
            }
        }
    }
}

/// Search stays local; selection sends only the stable Slack member ID.
struct PeoplePicker: View {
    let people: [SlackPerson]
    let select: (Any) -> Void
    @Binding var search: String

    var matches: [SlackPerson] { people.filter { $0.matches(search) } }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Which one is you?").font(.title3.weight(.semibold))
            Text("You'll own this Tag. Only you can use it until you turn on access control in settings.")
                .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Field(placeholder: "Search by name or username", text: $search)
            Group {
                if renderingPreview {
                    VStack(spacing: 0) { rows }.frame(maxHeight: .infinity, alignment: .top).clipped()
                } else {
                    ScrollView { LazyVStack(spacing: 0) { rows } }
                }
            }
            .frame(height: 240)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
            TextButton(title: "Enter a member ID instead") { select("manual") }
        }
    }

    @ViewBuilder var rows: some View {
        if matches.isEmpty {
            Text("No people found. Try another name or enter a member ID.")
                .foregroundStyle(.secondary).padding(20)
        }
        ForEach(matches) { person in
            Button { select(person.id) } label: {
                HStack(spacing: 12) {
                    Group {
                        if (demoMode || renderingPreview), person.imageURL.hasPrefix("demo-avatar:"),
                           let path = Bundle.main.path(forResource: String(person.imageURL.dropFirst(12)), ofType: "jpg"),
                           let photo = NSImage(contentsOfFile: path) {
                            Image(nsImage: photo).resizable().scaledToFill()
                        } else {
                            AsyncImage(url: URL(string: person.imageURL)) { image in
                                image.resizable().scaledToFill()
                            } placeholder: {
                                Text(String(person.name.prefix(1)).uppercased())
                                    .font(.headline).frame(maxWidth: .infinity, maxHeight: .infinity)
                                    .background(Color.accentColor.opacity(0.15))
                            }
                        }
                    }
                    .frame(width: 36, height: 36).clipShape(RoundedRectangle(cornerRadius: 8))
                    VStack(alignment: .leading, spacing: 3) {
                        Text(person.name).fontWeight(.medium)
                        if !person.username.isEmpty {
                            Text("@\(person.username)").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                    Image(systemName: "chevron.right").font(.caption).foregroundStyle(.tertiary)
                }
                .padding(12).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            Divider().padding(.leading, 60)
        }
    }

}

// MARK: - Slack illustrations

private let slackAubergine = Color(red: 0.25, green: 0.08, blue: 0.25)
private let slackGreen = Color(red: 0, green: 0.48, blue: 0.35)

/// A miniature Slack window frame shared by every illustration.
struct SlackWindow<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        HStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 7) {
                RoundedRectangle(cornerRadius: 5).fill(.white.opacity(0.9)).frame(width: 22, height: 22)
                ForEach(0..<3, id: \.self) { i in
                    Capsule().fill(.white.opacity(i == 0 ? 0.7 : 0.3)).frame(width: i == 0 ? 52 : 44, height: 5)
                }
            }
            .padding(10)
            .frame(width: 76, alignment: .topLeading)
            .frame(maxHeight: .infinity, alignment: .top)
            .background(slackAubergine)
            content.frame(maxWidth: .infinity, maxHeight: .infinity).background(.white)
        }
        .frame(width: 330, height: 124)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .shadow(color: .black.opacity(0.12), radius: 8, y: 3)
        .environment(\.colorScheme, .light)
    }
}

struct SlackComposer: View {
    let pasted: Bool
    var body: some View {
        SlackWindow {
            VStack(alignment: .leading, spacing: 8) {
                Text("# general").font(.system(size: 11, weight: .bold))
                Spacer()
                HStack(spacing: 6) {
                    Text(pasted ? "/slackauthticket MjA1…" : "Message #general")
                        .font(.system(size: 11, design: pasted ? .monospaced : .default))
                        .foregroundStyle(pasted ? .black : .gray)
                    Spacer()
                    Image(systemName: "paperplane.fill").font(.system(size: 9)).foregroundStyle(.white)
                        .padding(5).background(pasted ? slackGreen : .gray.opacity(0.4), in: RoundedRectangle(cornerRadius: 4))
                }
                .padding(7)
                .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(.gray.opacity(0.4)))
            }
            .padding(10)
        }
    }
}

struct SlackConfirmModal: View {
    var body: some View {
        SlackWindow {
            ZStack {
                Color.black.opacity(0.35)
                VStack(alignment: .leading, spacing: 5) {
                    Text("Slack CLI Authentication").font(.system(size: 10, weight: .bold)).foregroundStyle(.black)
                    ForEach([0.9, 0.7, 0.8], id: \.self) { w in
                        Capsule().fill(.gray.opacity(0.25)).frame(width: 150 * w, height: 4)
                    }
                    HStack {
                        Spacer()
                        Text("Confirm").font(.system(size: 9, weight: .bold)).foregroundStyle(.white)
                            .padding(.horizontal, 9).padding(.vertical, 4)
                            .background(slackGreen, in: RoundedRectangle(cornerRadius: 4))
                            .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Color.accentColor, lineWidth: 2).padding(-3))
                    }
                }
                .padding(10).frame(width: 180)
                .background(.white, in: RoundedRectangle(cornerRadius: 6))
            }
        }
    }
}

struct SlackCodeModal: View {
    var body: some View {
        SlackWindow {
            ZStack {
                Color.black.opacity(0.35)
                VStack(alignment: .leading, spacing: 6) {
                    Text("Submit Challenge Code").font(.system(size: 10, weight: .bold)).foregroundStyle(.black)
                    Capsule().fill(.gray.opacity(0.25)).frame(width: 140, height: 4)
                    Text("NwrFLzAy").font(.system(size: 15, weight: .bold, design: .monospaced)).foregroundStyle(.black)
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .background(Color.accentColor.opacity(0.2), in: RoundedRectangle(cornerRadius: 4))
                }
                .padding(10).frame(width: 180, alignment: .leading)
                .background(.white, in: RoundedRectangle(cornerRadius: 6))
            }
        }
    }
}

/// Paste-and-send, then Confirm, on a loop. Holds on Confirm when the
/// operator has asked macOS to reduce motion.
struct SlackSequence: View {
    @Environment(\.accessibilityReduceMotion) var reduceMotion
    var frameOverride: Int?
    var body: some View {
        TimelineView(.periodic(from: .now, by: 1.6)) { context in
            let frame = frameOverride ?? (reduceMotion ? 2 : Int(context.date.timeIntervalSinceReferenceDate / 1.6) % 3)
            ZStack {
                SlackComposer(pasted: false).opacity(frame == 0 ? 1 : 0)
                SlackComposer(pasted: true).opacity(frame == 1 ? 1 : 0)
                SlackConfirmModal().opacity(frame == 2 ? 1 : 0)
            }
            .animation(.easeInOut(duration: 0.3), value: frame)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Paste the line into a Slack message, send it, then click Confirm.")
    }
}
