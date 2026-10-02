// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
import Foundation

@main
struct SetupTests {
    @MainActor static func main() throws {
        let data = Data(#"{"kind":"people","prompt":"Choose someone else","people":[{"id":"U123","name":"Jamie Chen","username":"jchen","image_url":"https://example.com/j.png"}]}"#.utf8)
        let question = try JSONDecoder().decode(SetupQuestion.self, from: data)
        let person = question.people![0]
        precondition(person.imageURL == "https://example.com/j.png")
        precondition(person.matches("  JAMIE "))
        precondition(person.matches("jchen"))
        precondition(person.matches("u123"))
        precondition(person.matches(""))
        precondition(!person.matches("missing"))

        // Setup no longer asks who can use Tag: sign-in, workspace, then channels.
        renderingPreview = true
        let session = SetupSession()
        session.start(firstTag: true)
        session.send("demo-code")
        session.send(0)
        precondition(session.question?.kind == "multi")
        print("Mac setup tests passed")
    }
}
