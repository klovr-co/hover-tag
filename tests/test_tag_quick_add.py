from __future__ import annotations

import json
import sys
import unittest

from scripts import tag_quick_add

WORKSPACES = [
    {"id": "T0BND7V5J2W", "name": "Klovr", "kind": "workspace"},
    {"id": "E0C6K31U308", "name": "Hover Sandbox", "kind": "organization"},
]


class AnswerTests(unittest.TestCase):
    def test_workspace_matches_id_or_name_without_regard_to_case(self):
        event = {"id": "workspace", "workspaces": WORKSPACES}
        for target in ("T0BND7V5J2W", "t0bnd7v5j2w", "klovr"):
            with self.subTest(target=target):
                self.assertEqual(tag_quick_add.answer(event, workspace=target), "T0BND7V5J2W")

    def test_unknown_workspace_lists_the_signed_in_ones(self):
        with self.assertRaisesRegex(tag_quick_add.Unanswered, r"Klovr \(T0BND7V5J2W\)"):
            tag_quick_add.answer({"id": "workspace", "workspaces": WORKSPACES}, workspace="T0OTHER")

    def test_organization_needs_the_full_flow(self):
        with self.assertRaisesRegex(tag_quick_add.Unanswered, "organization"):
            tag_quick_add.answer({"id": "workspace", "workspaces": WORKSPACES}, workspace="Hover Sandbox")

    def test_a_workspace_in_an_organization_goes_through_its_organization(self):
        options = dict(workspace="T0LAB", team="T0LAB", enterprise="E0C6K31U308")
        self.assertEqual(tag_quick_add.answer({"id": "workspace", "workspaces": WORKSPACES}, **options), "E0C6K31U308")
        listed = {"id": "org_workspace", "option_ids": ["T0LAB", "manual"]}
        self.assertEqual(tag_quick_add.answer(listed, **options), "T0LAB")
        unlisted = {"id": "org_workspace", "option_ids": ["manual"]}
        self.assertEqual(tag_quick_add.answer(unlisted, **options), "manual")
        self.assertEqual(tag_quick_add.answer({"id": "org_workspace_id"}, **options), "T0LAB")

    def test_an_organization_is_never_guessed_without_a_tag_in_it(self):
        with self.assertRaises(tag_quick_add.Unanswered):
            tag_quick_add.answer({"id": "workspace", "workspaces": WORKSPACES}, workspace="T0LAB")
        with self.assertRaises(tag_quick_add.Unanswered):
            tag_quick_add.answer({"id": "workspace", "workspaces": WORKSPACES}, workspace="T0LAB",
                                 team="T0LAB", enterprise="E0SOMEWHEREELSE")

    def test_profile_uses_the_suggested_name_unless_one_is_given(self):
        event = {"id": "profile", "name": "Maya's Tag", "description": ""}
        self.assertEqual(tag_quick_add.answer(event, workspace="x"), {"name": "Maya's Tag", "description": ""})
        self.assertEqual(tag_quick_add.answer(event, workspace="x", name="Docs Tag")["name"], "Docs Tag")

    def test_defaults_for_the_model_approval_and_channels(self):
        self.assertEqual(tag_quick_add.answer({"id": "default_model", "default": 1}, workspace="x"), 1)
        self.assertEqual(tag_quick_add.answer({"id": "approve_setup"}, workspace="x"), "create")
        self.assertEqual(tag_quick_add.answer({"id": "channels", "allow_empty": True}, workspace="x"), [])

    def test_questions_without_an_obvious_answer_stop_quick_add(self):
        for event in ({"id": "ai_connection", "prompt": "Connect an AI in Settings"},
                      {"id": "channels", "allow_empty": False}, {"id": "app_token"}):
            with self.subTest(event=event), self.assertRaises(tag_quick_add.Unanswered):
                tag_quick_add.answer(event, workspace="x")


class DriveTests(unittest.TestCase):
    def run_script(self, script: str, **options):
        seen: list[dict] = []
        code, reason = tag_quick_add.drive(
            [sys.executable, "-c", script], {}, workspace="klovr", forward=lambda event, _: seen.append(event), **options)
        return code, reason, seen

    def test_answers_questions_and_forwards_the_rest(self):
        script = (
            "import json,sys\n"
            "print(json.dumps({'type':'message','text':'hello'}),flush=True)\n"
            "print(json.dumps({'type':'question','id':'approve_setup'}),flush=True)\n"
            "reply=json.loads(sys.stdin.readline())\n"
            "print(json.dumps({'type':'progress','text':reply['answer']}),flush=True)\n"
        )
        code, reason, seen = self.run_script(script)
        self.assertEqual((code, reason), (0, None))
        self.assertEqual([event["text"] for event in seen], ["hello", "create"])

    def test_pauses_setup_on_an_unanswerable_question(self):
        script = (
            "import json,sys\n"
            "print(json.dumps({'type':'question','id':'ai_connection','prompt':'Connect an AI'}),flush=True)\n"
            "reply=json.loads(sys.stdin.readline())\n"
            "sys.exit(0 if reply.get('pause') else 1)\n"
        )
        code, reason, _ = self.run_script(script)
        self.assertEqual(code, 0)
        self.assertIn("Connect an AI", reason)


if __name__ == "__main__":
    unittest.main()
