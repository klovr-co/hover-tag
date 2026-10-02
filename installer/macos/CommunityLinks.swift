import AppKit
import SwiftUI

/// Opens the public pages where people can support Tag. Nothing is posted or
/// starred on the user's behalf; each button just opens the page in a browser.
struct CommunityLinks: View {
    static let repo = "https://github.com/klovr-co/hover-tag"
    static let slack = "https://join.slack.com/t/hover-community/shared_invite/zt-4aghkshid-n7fRukS7_J5sR2jDLBXK9A"
    static let share: String = {
        // Encode strictly: URLComponents leaves characters like ' : / unescaped in query values.
        func encode(_ s: String) -> String {
            s.addingPercentEncoding(withAllowedCharacters: .alphanumerics.union(CharacterSet(charactersIn: "-._~"))) ?? s
        }
        let text = "I'm using Tag, a personal assistant that lives in Slack."
        return "https://twitter.com/intent/tweet?text=\(encode(text))&url=\(encode(repo))"
    }()

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Enjoying Tag?").font(.callout.weight(.semibold))
            HStack(spacing: 8) {
                link("Star on GitHub", "star", Self.repo)
                link("Join Slack", "bubble.left.and.bubble.right", Self.slack)
                link("Share on X", "square.and.arrow.up", Self.share)
            }
        }
    }

    private func link(_ title: String, _ icon: String, _ url: String) -> some View {
        SecondaryButton(title: title, icon: icon) { NSWorkspace.shared.open(URL(string: url)!) }
    }
}
