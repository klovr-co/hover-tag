// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Tag in the menu bar, and opening at login: Tag stays available after its
// window closes, and can bring back the Tags you had running when you log in.
import AppKit
import ServiceManagement
import SwiftUI

/// Login behaviour. "Open at Login" uses the system Login Items list, and then
/// starts the Tags last switched on in this app; there's no separate setting.
@MainActor
final class LoginSettings: ObservableObject {
    static let shared = LoginSettings()
    private let defaults = UserDefaults.standard

    @Published var openAtLogin = SMAppService.mainApp.status == .enabled
    @Published var error = ""

    /// Tags the person last switched on; restored when Tag opens at login.
    var wantedRunning: Set<String> {
        get { Set(defaults.stringArray(forKey: "wantedRunningTags") ?? []) }
        set { defaults.set(Array(newValue).sorted(), forKey: "wantedRunningTags") }
    }

    func setOpenAtLogin(_ enabled: Bool) {
        do {
            if enabled { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            error = ""
        } catch {
            self.error = enabled
                ? "macOS didn't allow opening at login. Move Tag to Applications and try again."
                : "macOS didn't remove Tag from Login Items. Remove it in System Settings › General › Login Items."
        }
        openAtLogin = SMAppService.mainApp.status == .enabled
        if SMAppService.mainApp.status == .requiresApproval {
            error = "Approve Tag in System Settings › General › Login Items."
            SMAppService.openSystemSettingsLoginItems()
        }
    }

    /// Whether this launch came from the login item rather than a person opening Tag.
    static var launchedAtLogin: Bool {
        guard let event = NSAppleEventManager.shared().currentAppleEvent else { return false }
        return event.eventID == kAEOpenApplication
            && event.paramDescriptor(forKeyword: keyAEPropData)?.enumCodeValue == keyAELaunchedAsLogInItem
    }

    /// Opening at login always brings back the Tags the person had switched on.
    func restore(_ tags: Tags) async {
        guard tagInstalled, !demoMode else { return }
        await tags.refresh()
        for row in tags.rows where wantedRunning.contains(row.id) && row.state != "running" {
            _ = await run(row.command("start"))
        }
        await tags.refresh()
    }
}

/// The menu bar icon: a waterdrop, filled while any Tag is online.
struct MenuBarLabel: View {
    @ObservedObject var tags: Tags
    var body: some View {
        Image(systemName: tags.rows.contains { $0.state == "running" } ? "drop.fill" : "drop")
            .accessibilityLabel("Tag")
    }
}

struct MenuBarContent: View {
    @ObservedObject var tags: Tags
    @ObservedObject var login = LoginSettings.shared
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        let online = tags.rows.filter { $0.state == "running" }.count
        Text(tags.rows.isEmpty ? "No Tags yet" : "\(online) of \(tags.rows.count) Tags online")
        Divider()
        ForEach(tags.groups, id: \.key) { group in
            if tags.groups.count > 1 { Section(group.label) { rows(group.rows) } } else { rows(group.rows) }
        }
        if tags.rows.contains(where: { $0.label.0 != "Needs setup" }) {
            Divider()
            Button("Start All Tags") { Task { await tags.all("start") } }
            Button("Stop All Tags") { Task { await tags.all("stop") } }
        }
        Divider()
        Button("Open Tag…") { showWindow() }.keyboardShortcut("o")
        Button("Add Tag…") { showWindow() }
        Divider()
        Toggle("Open at Login", isOn: Binding(get: { login.openAtLogin }, set: { login.setOpenAtLogin($0) }))
        if !login.error.isEmpty { Text(login.error) }
        Divider()
        Button("Quit Tag") { NSApp.terminate(nil) }.keyboardShortcut("q")
    }

    @ViewBuilder func rows(_ rows: [TagRow]) -> some View {
        ForEach(rows) { row in
            if row.label.0 == "Needs setup" {
                Button("\(row.title) — finish setup…") { showWindow() }
            } else {
                Toggle(row.title, isOn: Binding(
                    get: { row.state == "running" },
                    set: { _ in Task { await tags.toggle(row) } }
                ))
                .disabled(tags.busy.contains(row.id))
            }
        }
    }

    func showWindow() {
        NSApp.setActivationPolicy(.regular)
        openWindow(id: "main")
        NSApp.activate(ignoringOtherApps: true)
    }
}

extension Tags {
    /// Start or stop every Tag that has finished setup, one at a time.
    func all(_ action: String) async {
        for row in rows where row.label.0 != "Needs setup" && (row.state == "running") == (action == "stop") {
            await toggle(row)
        }
    }
}
