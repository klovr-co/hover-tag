from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from scripts import tag_chatgpt as auth
from scripts import codex_app_server as transport


class ChatGPTTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))
        cls.jwk.update(kid="test-key", use="sig", alg="RS256")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"TAG_HOME": str(self.root), "TAG_INSTANCE_HOME": str(self.root / "instance")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = auth.Store()

    def tokens(self, **overrides):
        result = {"access_token": "access-fixture", "refresh_token": "refresh-fixture",
                  "token_type": "Bearer", "expires_in": 3600, "scope": auth.SCOPES}
        result.update(overrides)
        return result

    def claims(self, **overrides):
        claims = {"iss": auth.ISSUER, "aud": "oaiapp_one", "sub": "person-one",
                  "iat": int(time.time()), "exp": int(time.time()) + 300, "nonce": "nonce"}
        claims.update(overrides)
        return claims

    def encoded(self, **overrides):
        return jwt.encode(self.claims(**overrides), self.key, algorithm="RS256", headers={"kid": "test-key"})

    def seed(self, *, expired=False, scope=auth.SCOPES):
        record = self.store.read()
        account = auth.token_record(self.tokens(scope=scope), {"subject": "person-one", "client_id": "oaiapp_one", "email": "one@example.test", "ext_agent_host_id": auth.host_id()})
        if expired:
            account["expires_at"] = 0
        record.update(mode="chatgpt", active="oaiapp_one", accounts={"oaiapp_one": account})
        auth.atomic_write(self.store.path, record)
        return record

    def validate(self, encoded, **kwargs):
        with patch.object(auth, "request", side_effect=[{"issuer": auth.ISSUER, "jwks_uri": auth.ISSUER + "/keys"}, {"keys": [self.jwk]}]):
            return auth.validate_id_token(encoded, "oaiapp_one", **kwargs)

    def test_old_installation_remains_codex_without_writes(self):
        self.assertFalse(self.store.enabled())
        self.assertEqual(self.store.status()["accounts"], [])
        self.assertFalse(self.store.path.exists())

    def test_host_identity_is_stable_across_signouts_and_concurrent_runs(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda _: auth.host_id(), range(8)))
        self.assertEqual(len(set(ids)), 1)
        self.seed()
        with patch.object(auth, "request", side_effect=auth.ChatGPTError("offline")):
            self.assertFalse(self.store.logout())
        self.assertEqual(auth.host_id(), ids[0])
        self.assertTrue(self.store.enabled())
        self.assertEqual(self.store.read()["active"], "oaiapp_one")
        self.assertNotIn("refresh_token", self.store.read()["accounts"]["oaiapp_one"])
        with self.assertRaisesRegex(auth.ChatGPTError, "needs sign-in"):
            self.store.access()

    def test_signed_id_token_requires_signature_issuer_audience_nonce_and_expiry(self):
        self.assertEqual(self.validate(self.encoded(), nonce="nonce")["sub"], "person-one")
        for changes in ({"iss": "https://wrong.example"}, {"aud": "wrong"}, {"nonce": "wrong"},
                        {"exp": int(time.time()) - 10}, {"sub": ""}, {"azp": "other"},
                        {"aud": ["oaiapp_one", "other"]}):
            with self.subTest(changes=changes), self.assertRaises(auth.ChatGPTError):
                self.validate(self.encoded(**changes), nonce="nonce")
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        forged = jwt.encode(self.claims(), other_key, algorithm="RS256", headers={"kid": "test-key"})
        with self.assertRaises(auth.ChatGPTError):
            self.validate(forged, nonce="nonce")

    def test_refresh_is_serialized_and_rotating_tokens_saved_together(self):
        self.seed(expired=True)
        def refresh(*args, **kwargs):
            time.sleep(.08)
            self.assertEqual(kwargs["form"]["client_id"], "oaiapp_one")
            self.assertNotIn("scope", kwargs["form"])
            return self.tokens(access_token="new-access", refresh_token="new-refresh")
        with patch.object(auth, "request", side_effect=refresh) as request:
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: self.store.access(), range(4)))
        self.assertEqual(results, ["new-access"] * 4)
        self.assertEqual(request.call_count, 1)
        account = self.store.read()["accounts"]["oaiapp_one"]
        self.assertEqual(account["refresh_token"], "new-refresh")
        if os.name != "nt":
            self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.store.path.parent.stat().st_mode & 0o777, 0o700)

    def test_transient_refresh_failure_preserves_tokens_and_retries(self):
        original = self.seed(expired=True)
        with patch.object(auth, "request", side_effect=auth.RequestError(503)):
            with self.assertRaises(auth.ChatGPTError):
                self.store.access()
        self.assertEqual(self.store.read(), original)
        with patch.object(auth, "request", return_value=self.tokens()):
            self.assertEqual(self.store.access(), "access-fixture")

    def test_terminal_refresh_failure_clears_tokens_but_retains_registration(self):
        self.seed(expired=True)
        with patch.object(auth, "request", side_effect=auth.RequestError(400, "refresh_token_reused")):
            with self.assertRaisesRegex(auth.ChatGPTError, "revoked"):
                self.store.access()
        account = self.store.read()["accounts"]["oaiapp_one"]
        self.assertEqual(account["subject"], "person-one")
        self.assertNotIn("access_token", account)
        self.assertTrue(self.store.enabled())

    def test_permission_removed_on_refresh_is_saved_and_blocks_inference(self):
        self.seed(expired=True)
        with patch.object(auth, "request", return_value=self.tokens(scope="openid email offline_access")):
            with self.assertRaisesRegex(auth.ChatGPTError, "permission was removed"):
                self.store.access()
        with self.assertRaisesRegex(auth.ChatGPTError, "disabled"):
            self.store.access()

    def test_invalid_replacement_never_overwrites_previous_record(self):
        original = self.seed(expired=True)
        with patch.object(auth, "request", return_value=self.tokens(refresh_token="")):
            with self.assertRaises(auth.ChatGPTError):
                self.store.access()
        self.assertEqual(self.store.read(), original)

    def test_logout_revokes_then_clears_without_changing_billing(self):
        self.seed()
        with patch.object(auth, "request", side_effect=[{"revocation_endpoint": auth.ISSUER + "/revoke"}, {}]) as request:
            self.assertTrue(self.store.logout())
        self.assertEqual(request.call_args.kwargs["form"], {"token": "refresh-fixture", "token_type_hint": "refresh_token", "client_id": "oaiapp_one"})
        self.assertTrue(self.store.enabled())
        self.store.use_codex()
        self.assertFalse(self.store.enabled())

    def test_account_switch_does_not_mix_registrations_with_same_email(self):
        record = self.seed()
        record["accounts"]["oaiapp_two"] = auth.token_record(self.tokens(access_token="two-access"), {"subject": "person-two", "email": "one@example.test", "client_id": "oaiapp_two", "ext_agent_host_id": auth.host_id()})
        auth.atomic_write(self.store.path, record)
        self.assertEqual(self.store.access("oaiapp_two", activate=True), "two-access")
        self.assertEqual(self.store.read()["accounts"]["oaiapp_one"], record["accounts"]["oaiapp_one"])
        self.assertEqual(self.store.status()["active"], "oaiapp_two")

    def browser_login(self, *, account=None, callback_overrides=None, scope=auth.SCOPES, subject="person-one", consent=False):
        visited = {}
        workers = []
        def browser(url):
            query = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}
            visited.update(query)
            callback = {"state": query["state"], "client_id": "oaiapp_one", "code": "single-use-code"}
            callback.update(callback_overrides or {})
            def send():
                try:
                    with urllib.request.urlopen(query["redirect_uri"] + "?" + urllib.parse.urlencode(callback), timeout=2) as response:
                        response.read()
                except Exception:
                    pass
            worker = threading.Thread(target=send)
            workers.append(worker)
            worker.start()
            return True
        def api(url, **kwargs):
            if url == auth.TOKEN:
                form = kwargs["form"]
                self.assertEqual(form["redirect_uri"], visited["redirect_uri"])
                self.assertEqual(form["client_id"], "oaiapp_one")
                challenge = auth.base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).decode().rstrip("=")
                self.assertEqual(challenge, visited["code_challenge"])
                return self.tokens(scope=scope, id_token=self.encoded(nonce=visited["nonce"], sub=subject))
            if url.endswith("openid-configuration"):
                return {"issuer": auth.ISSUER, "jwks_uri": auth.ISSUER + "/keys"}
            return {"keys": [self.jwk]}
        try:
            with patch.object(auth.webbrowser, "open", side_effect=browser), patch.object(auth, "request", side_effect=api):
                self.store.login(account, consent=consent, timeout=.15)
        finally:
            for worker in workers:
                worker.join(3)
        return visited

    def test_browser_registration_then_reauthorization_reuses_host_and_client(self):
        first = self.browser_login()
        self.assertEqual(first["client_id"], "dynamic_agent_client")
        self.assertEqual(first["agent_name_hint"], auth.APP_NAME)
        second = self.browser_login(account="oaiapp_one")
        self.assertEqual(second["client_id"], "oaiapp_one")
        self.assertEqual(second["ext_agent_host_id"], first["ext_agent_host_id"])
        self.assertNotIn("agent_name_hint", second)
        self.assertIn("id_token_hint", second)
        for field in ("state", "nonce", "code_challenge"):
            self.assertNotEqual(first[field], second[field])

    def test_invalid_callbacks_preserve_active_account(self):
        original = self.seed()
        for callback in ({"state": "forged"}, {"client_id": "dynamic_agent_client"}, {"error": "access_denied"}, {"code": ""}):
            with self.subTest(callback=callback), self.assertRaises(auth.ChatGPTError):
                self.browser_login(callback_overrides=callback)
            self.assertEqual(self.store.read(), original)

    def test_returning_identity_or_client_mismatch_is_rejected(self):
        original = self.seed()
        with self.assertRaisesRegex(auth.ChatGPTError, "does not match"):
            self.browser_login(account="oaiapp_one", subject="different-person")
        with self.assertRaisesRegex(auth.ChatGPTError, "mismatched"):
            self.browser_login(account="oaiapp_one", callback_overrides={"client_id": "oaiapp_other"})
        self.assertEqual(self.store.read(), original)

    def test_identity_without_plan_grant_is_retained_but_cannot_run(self):
        self.browser_login(scope="openid email offline_access")
        self.assertTrue(self.store.status()["accounts"][0]["signed_in"])
        self.assertFalse(self.store.status()["accounts"][0]["plan_enabled"])
        with self.assertRaises(auth.ChatGPTError):
            self.store.access()
        query = self.browser_login(account="oaiapp_one", consent=True)
        self.assertEqual(query["prompt"], "consent")
        self.assertEqual(self.store.access(), "access-fixture")

    def test_status_and_dry_run_never_expose_secrets_or_launch_browser(self):
        self.seed()
        output = io.StringIO()
        with redirect_stdout(output), patch.object(auth.webbrowser, "open") as browser:
            auth.cli(["status"], json_output=True)
            auth.cli(["logout"], json_output=True, dry_run=True)
        browser.assert_not_called()
        for value in ("access-fixture", "refresh-fixture"):
            self.assertNotIn(value, output.getvalue())
        self.assertEqual(self.store.access(), "access-fixture")
        with patch.object(auth.sys.stdin, "isatty", return_value=False), self.assertRaisesRegex(auth.ChatGPTError, "interactive"):
            auth.cli(["login"])

    def test_model_catalog_uses_selected_token_and_filters_visibility(self):
        self.seed()
        with patch.object(auth, "request", return_value={"models": [
            {"slug": "first", "display_name": "First", "visibility": "list"},
            {"slug": "hidden", "visibility": "hide"}]}) as request:
            result = transport.CodexAppServer(["codex", "app-server"], cwd=self.root, timeout=10).model_catalog()
        self.assertEqual([m["model"] for m in result], ["first"])
        request.assert_called_once_with(auth.RESOURCE + "/models", token="access-fixture")
        self.assertFalse(result[0]["isDefault"])

    def test_provider_uses_child_environment_only_and_real_attribution(self):
        self.seed()
        server = transport.CodexAppServer(["codex", "app-server", "--stdio"], cwd=self.root, timeout=10)
        with patch.object(transport.subprocess, "Popen") as popen, patch.object(transport.threading.Thread, "start"):
            server._start()
        command = popen.call_args.args[0]
        self.assertNotIn("access-fixture", " ".join(command))
        self.assertNotIn("--stdio", command)
        self.assertIn("stdio://", command)
        self.assertEqual(popen.call_args.kwargs["env"][auth.TOKEN_ENV], "access-fixture")
        self.assertNotIn(auth.TOKEN_ENV, os.environ)
        self.assertEqual(server._client_info()["name"], auth.APP_NAME)
        self.assertIn('model_providers.openai_chatgpt_plan.supports_websockets=false', command)

    def test_renewal_resumes_same_thread_and_preserves_absolute_deadline(self):
        server = transport.CodexAppServer(["codex", "app-server"], cwd=self.root, timeout=10, max_timeout=100)
        server.chatgpt_token = "access-fixture"
        events = []
        replies = [{}, {"thread": {"id": "thread-one"}}, {"turn": {"id": "turn-one"}},
                   {}, {"thread": {"id": "thread-one"}}, {"turn": {"id": "turn-two"}}]
        with patch.object(server, "_start") as start, patch.object(server, "close"), patch.object(server, "_notify"), patch.object(
            server, "_request", side_effect=replies
        ) as request, patch.object(server, "_consume_turn", side_effect=[("renew_token", ""), ("completed", "")]) as consume:
            self.assertEqual(server.run("original task", model="model-one", reasoning_effort=None, emit=events.append), ("completed", ""))
        self.assertEqual(start.call_count, 2)
        calls = request.call_args_list
        self.assertEqual([call.args[0] for call in calls], ["initialize", "thread/start", "turn/start", "initialize", "thread/resume", "turn/start"])
        for call in (calls[0], calls[3]):
            self.assertTrue(call.args[1]["capabilities"]["experimentalApi"])
            self.assertEqual(call.args[1]["clientInfo"]["name"], auth.APP_NAME)
        self.assertFalse(calls[1].args[1]["ephemeral"])
        self.assertEqual(calls[4].args[1]["threadId"], "thread-one")
        self.assertEqual(consume.call_args_list[0].kwargs["max_deadline"], consume.call_args_list[1].kwargs["max_deadline"])
        self.assertNotIn("original task", calls[-1].args[1]["input"][0]["text"])

    def test_renewal_waits_for_interruption_and_hides_intermediate_terminal_event(self):
        server = transport.CodexAppServer(["codex", "app-server"], cwd=self.root, timeout=10)
        server.chatgpt_token = "access-fixture"
        server.token_renewal_deadline = time.monotonic() - 1
        events = []
        with patch.object(server, "_next_message", side_effect=[transport.CodexAppServerError("timeout"),
             {"method": "turn/completed", "params": {"turn": {"status": "interrupted"}}}]), patch.object(server, "interrupt") as interrupt:
            result = server._consume_turn(transport.CodexEventMapper(), events.append,
                                         time.monotonic() + 10, time.monotonic() + 100)
        self.assertEqual(result, ("renew_token", ""))
        interrupt.assert_called_once()
        self.assertEqual(events, [])

    def test_stop_during_renewal_never_restarts_the_task(self):
        control = self.root / "stop"
        control.write_text("request-one")
        server = transport.CodexAppServer(["codex", "app-server"], cwd=self.root, timeout=10,
                                         control_file=control, run_id="request-one")
        with patch.object(server, "_start") as start, patch.object(server, "close"), patch.object(server, "_notify"), patch.object(
            server, "_request", side_effect=[{}, {"thread": {"id": "t"}}, {"turn": {"id": "u"}}]
        ), patch.object(server, "_consume_turn", return_value=("renew_token", "")):
            status, _ = server.run("task", model=None, reasoning_effort=None, emit=lambda e: None)
        self.assertEqual(status, "interrupted")
        self.assertEqual(start.call_count, 1)

    def test_account_changes_are_rejected_while_bridge_is_running(self):
        with patch("scripts.tag_cli.process_for", return_value=object()):
            with self.assertRaisesRegex(auth.ChatGPTError, "Stop this Tag"):
                auth.cli(["use-codex"])
        self.assertFalse(self.store.path.exists())

    def test_redaction_and_plan_error_classification(self):
        from scripts.tag_error_reporting import classify_failure, redact_sensitive_text
        from scripts.opentag_agent import retryable_backend_failure
        encoded = self.encoded()
        self.assertNotIn(encoded, redact_sensitive_text("ID " + encoded, limit=5000))
        self.assertNotIn("secret-value", redact_sensitive_text('id_token_hint=secret-value&state=ok'))
        result = classify_failure("subscription_sharing_usage_limit_exceeded HTTP 429")
        self.assertIn("https://chatgpt.com/settings/usage", result.explanation)
        self.assertFalse(retryable_backend_failure("subscription_sharing_usage_limit_exceeded HTTP 429"))

    def test_empty_or_malformed_store_never_falls_back_to_codex(self):
        for record in ({}, {"schema_version": 2}, {"schema_version": 1, "mode": "chatgpt", "active": "missing", "accounts": {}}):
            auth.atomic_write(self.store.path, record)
            with self.assertRaises(auth.ChatGPTError):
                self.store.enabled()

    def test_copied_registration_requires_authorization_for_new_host(self):
        record = self.seed()
        record["accounts"]["oaiapp_one"]["ext_agent_host_id"] = "urn:uuid:another-host"
        auth.atomic_write(self.store.path, record)
        with patch.object(auth, "request") as request, self.assertRaisesRegex(auth.ChatGPTError, "another host"):
            self.store.access()
        request.assert_not_called()
        self.assertEqual(self.store.read(), record)

    def test_failed_atomic_publish_keeps_previous_record_and_is_retryable(self):
        original = self.seed(expired=True)
        with patch.object(auth, "request", return_value=self.tokens()), patch.object(auth.os, "replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.store.access()
        self.assertEqual(self.store.read(), original)
        self.assertEqual(list(self.store.path.parent.glob(".chatgpt-*")), [])
        with patch.object(auth, "request", return_value=self.tokens()):
            self.assertEqual(self.store.access(), "access-fixture")

    def test_expired_code_retains_pending_registration_without_selecting_it(self):
        # Exercise the real listener and replace only the provider token response.
        original = auth.request
        def expired(url, **kwargs):
            if url == auth.TOKEN:
                raise auth.RequestError(400, "invalid_grant")
            return original(url, **kwargs)
        def browser(url):
            params = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}
            target = params["redirect_uri"] + "?" + urllib.parse.urlencode({
                "state": params["state"], "client_id": "oaiapp_one", "code": "expired"})
            def callback():
                with urllib.request.urlopen(target, timeout=2) as response:
                    response.read()
            worker = threading.Thread(target=callback)
            worker.start()
            self.addCleanup(worker.join, 3)
            return True
        with patch.object(auth.webbrowser, "open", side_effect=browser), patch.object(auth, "request", side_effect=expired):
            with self.assertRaisesRegex(auth.ChatGPTError, "login oaiapp_one"):
                self.store.login(timeout=.2)
        record = self.store.read()
        self.assertEqual(record["mode"], "codex")
        self.assertIsNone(record["active"])
        self.assertEqual(record["accounts"], {})
        self.assertEqual(record["pending_registration"], "oaiapp_one")
        self.browser_login(account="oaiapp_one")
        self.assertNotIn("pending_registration", self.store.read())
        self.assertTrue(self.store.enabled())

    def test_identity_only_token_without_refresh_is_retained(self):
        record = auth.token_record(self.tokens(refresh_token=None, scope="openid email"), {"id_token": "identity-fixture"})
        self.assertNotIn("refresh_token", record)
        self.assertEqual(record["id_token"], "identity-fixture")

    def test_cli_status_and_dry_run_are_read_only_before_setup(self):
        import subprocess
        import sys
        root = self.root / "empty-installation"
        environment = dict(os.environ, TAG_HOME=str(root), TAG_TELEMETRY="off")
        script = Path(__file__).resolve().parents[1] / "scripts/tag_cli.py"
        for arguments in (["status"], ["login", "--dry-run"]):
            result = subprocess.run([sys.executable, str(script), "chatgpt", *arguments, "--json"],
                                    env=environment, text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["schema_version"], 1)
            self.assertFalse(root.exists())

    def test_setup_uses_chatgpt_without_checking_codex_login(self):
        import subprocess
        from scripts import opentag_setup
        self.seed()
        with patch.object(opentag_setup, "selected_backend_available", return_value=True), patch.object(
            opentag_setup.lifecycle, "mfs_client_executable", return_value="mfs"
        ), patch.object(opentag_setup.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run, redirect_stdout(io.StringIO()) as output:
            result = opentag_setup.finish_setup(Path("settings.json"), {"OPENTAG_BACKEND": "codex"}, [])
        self.assertEqual(result, 0)
        self.assertEqual(len(run.call_args_list), 1)
        self.assertIn("app-server", run.call_args.args[0])
        self.assertIn("ChatGPT plan connected", output.getvalue())

    def test_chatgpt_catalog_does_not_reintroduce_other_accounts_models(self):
        from scripts import slack_socket_agent as slack
        self.seed()
        with patch.object(auth, "models", return_value=[{"model": "allowed", "displayName": "Allowed"}]), patch.object(
            slack, "configured_codex_defaults", return_value=("unavailable", "high", True)
        ), patch.dict(os.environ, {"OPENTAG_CODEX_MODELS": "unavailable,allowed"}):
            models = slack.discover_codex_models()
        self.assertEqual([model.model_id for model in models], ["allowed"])
        self.assertTrue(models[0].is_default)
        self.assertFalse(models[0].default_fast_mode)

    def test_usage_limit_pauses_requests_until_explicit_resume(self):
        self.seed()
        self.store.pause_usage()
        with patch.object(auth, "request") as request, self.assertRaisesRegex(auth.ChatGPTError, "reviewing limits"):
            self.store.access()
        request.assert_not_called()
        self.assertTrue(self.store.status()["active_account"]["usage_paused"])
        self.assertEqual(self.store.access("oaiapp_one", activate=True), "access-fixture")
        self.assertFalse(self.store.status()["active_account"]["usage_paused"])

    def test_structured_plan_error_code_survives_transport_mapping(self):
        events = transport.CodexEventMapper().map({"method": "turn/completed", "params": {"turn": {
            "status": "failed", "error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "Limit reached"}}}})
        self.assertIn("subscription_sharing_usage_limit_exceeded", events[-1]["text"])
        mapper = transport.CodexEventMapper()
        mapper.map({"method": "error", "params": {"error": {
            "code": "subscription_sharing_usage_limit_exceeded", "message": "Limit reached"}}})
        events = mapper.map({"method": "turn/completed", "params": {"turn": {"status": "failed"}}})
        self.assertIn("subscription_sharing_usage_limit_exceeded", events[-1]["text"])


if __name__ == "__main__":
    unittest.main()
