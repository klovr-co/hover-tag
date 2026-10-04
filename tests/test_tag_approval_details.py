import unittest

from scripts.tag_approval_details import approval_details


class ApprovalDetailsTests(unittest.TestCase):
    def test_command_exposes_safe_identity_and_reason(self) -> None:
        details = approval_details("item/commandExecution/requestApproval", {
            "command": "curl -H 'Authorization: Bearer secret-token' https://example.com",
            "cwd": "/shared/Finance",
            "reason": "Needs network access",
            "networkApprovalContext": {"host": "example.com", "protocol": "https"},
        })
        self.assertEqual("curl (arguments withheld)", details["command"])
        self.assertEqual("/shared/Finance", details["working_directory"])
        self.assertEqual("example.com", details["network_host"])
        self.assertNotIn("secret-token", str(details))

    def test_file_change_uses_paths_never_contents(self) -> None:
        details = approval_details("applyPatchApproval", {
            "fileChanges": {"/shared/Finance/budget.xlsx": {"content": "secret cells"}},
            "reason": "The folder is outside permitted write locations",
        })
        self.assertEqual("/shared/Finance/budget.xlsx", details["files"])
        self.assertNotIn("secret cells", str(details))

    def test_permission_and_missing_metadata(self) -> None:
        details = approval_details("item/permissions/requestApproval", {
            "permissions": {"network": {"enabled": True},
                            "fileSystem": {"write": ["/shared/Finance"]}},
        })
        self.assertEqual("/shared/Finance", details["filesystem_write"])
        self.assertEqual("Network access requested", details["network"])
        self.assertEqual({"action": "Change files"},
                         approval_details("item/fileChange/requestApproval", {}))

    def test_long_and_secret_values_are_bounded(self) -> None:
        details = approval_details("item/fileChange/requestApproval", {
            "grantRoot": "/shared/" + "a" * 1000,
            "reason": "Authorization: Bearer secret-token",
        })
        self.assertLessEqual(len(details["requested_write_root"]), 181)
        self.assertNotIn("secret-token", str(details))


if __name__ == "__main__":
    unittest.main()
