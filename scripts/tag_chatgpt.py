"""Tag-owned ChatGPT plan authorization; never reads or writes Codex credentials.

A host belongs to the installation; registrations and the selected billing mode
belong to one Tag. All credential mutations use the same OS-backed lock. Browser
consent happens outside that lock and only a validated result can become active.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser

try:
    from .tag_paths import instance_home, tag_home, restrict_windows_acl
    from .tag_locks import LifecycleLock
except ImportError:
    from tag_paths import instance_home, tag_home, restrict_windows_acl
    from tag_locks import LifecycleLock

ISSUER = "https://auth.openai.com"
AUTHORIZE = ISSUER + "/api/accounts/authorize"
TOKEN = ISSUER + "/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
APP_NAME = "tag"
PLAN_ERRORS = {
    "subscription_sharing_user_not_eligible": "ChatGPT plan usage is unavailable for this account or workspace. Check its access policy.",
    "subscription_sharing_usage_limit_exceeded": "Tag's ChatGPT plan usage limit was reached. Review https://chatgpt.com/settings/usage before retrying.",
    "subscription_sharing_usage_unavailable": "ChatGPT usage availability could not be checked. Retry later.",
    "subscription_sharing_unsupported_capability": "This ChatGPT plan route does not support a requested model, tool, or option. Review Tag's ChatGPT connection documentation.",
    "subscription_sharing_route_not_supported": "The ChatGPT plan request used an unsupported endpoint. Check the Tag provider configuration.",
    "subscription_sharing_invalid_user": "ChatGPT could not validate the account. Check tag chatgpt status; sign in again if the session was revoked.",
    "chatpass_v2_scope_not_authorized": "ChatGPT plan permission does not authorize this operation. Check the account's grant in ChatGPT Settings.",
    "chatpass_v2_invalid_authorization_context": "ChatGPT plan authorization is invalid. Check the account's grant in ChatGPT Settings.",
    "subscription_sharing_user_unavailable": "The ChatGPT account or workspace is temporarily unavailable. Retry later.",
}
TOKEN_ENV = "TAG_CHATGPT_ACCESS_TOKEN"
TERMINAL_REFRESH_ERRORS = {
    "invalid_grant", "invalid_refresh_token", "token_expired",
    "refresh_token_expired", "refresh_token_invalidated", "refresh_token_reused",
}


class ChatGPTError(RuntimeError):
    """Safe public message; never includes an OAuth response or authorization URL."""


class RequestError(ChatGPTError):
    def __init__(self, status: int, code: str = "", request_id: str = ""):
        self.status = status
        self.code = code if re.fullmatch(r"[a-z_]{1,100}", code) else ""
        self.request_id = request_id if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", request_id) else ""
        detail = PLAN_ERRORS.get(self.code, {
            401: "Check the selected ChatGPT account and plan permission with tag chatgpt status.",
            403: "A policy or permission prevented access. Check the account's plan permission and serving region.",
        }.get(status, "Retry later; credentials were preserved."))
        reference = f" Request ID: {self.request_id}." if self.request_id else ""
        super().__init__(f"ChatGPT request failed (HTTP {status}, {self.code or 'unavailable'}). {detail}{reference}")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url: str, *, form: dict | None = None, token: str | None = None) -> dict:
    """Bounded HTTPS requests; credentials cannot be forwarded by redirects."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc not in {"auth.openai.com", "api.openai.com"}:
        raise ChatGPTError("Unexpected OpenAI endpoint; check the integration configuration.")
    headers = {"Accept": "application/json", "User-Agent": "Tag"}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with urllib.request.build_opener(NoRedirect).open(
            urllib.request.Request(url, data=data, headers=headers), timeout=20
        ) as response:
            body = response.read(1024 * 1024 + 1)
            if len(body) > 1024 * 1024:
                raise ChatGPTError("OpenAI response is too large; retry later.")
            result = json.loads(body) if body else {}
            if not isinstance(result, dict):
                raise ValueError()
            return result
    except urllib.error.HTTPError as exc:
        code = ""
        try:
            result = json.loads(exc.read(65536))
            error = result.get("error", "") if isinstance(result, dict) else ""
            code = error.get("code", "") if isinstance(error, dict) else error
        except (ValueError, OSError):
            pass
        raise RequestError(exc.code, code if isinstance(code, str) else "",
                           exc.headers.get("x-request-id", "")) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ChatGPTError("Cannot reach OpenAI. Retry later; credentials were preserved.") from None
    except (ValueError, UnicodeError):
        raise ChatGPTError("OpenAI returned an invalid response; retry later.") from None


def atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or path.parent.is_symlink():
        raise ChatGPTError("ChatGPT credential storage must not be a symlink.")
    path.parent.chmod(0o700)
    restrict_windows_acl(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".chatgpt-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def locked(path: Path):
    lock = LifecycleLock(path)
    deadline = time.monotonic() + 30
    while True:
        try:
            lock.acquire()
            break
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise ChatGPTError("Another ChatGPT account operation is running; retry shortly.") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.release()


def read_object(path: Path) -> dict:
    try:
        if path.is_symlink():
            raise ValueError()
        obj = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            raise ValueError()
        return obj
    except FileNotFoundError:
        return {}
    except (ValueError, UnicodeError):
        raise ChatGPTError("Cannot read ChatGPT credentials. Restore the protected file before retrying.") from None


def host_id(root: Path | None = None) -> str:
    path = (root or tag_home()) / "state/chatgpt-host.json"
    with locked(path.with_suffix(".lock")):
        record = read_object(path)
        if not record and not path.exists():
            record = {"schema_version": 1, "ext_agent_host_id": "urn:uuid:" + str(uuid.uuid4())}
            atomic_write(path, record)
        value = record.get("ext_agent_host_id", "")
        try:
            if record.get("schema_version") != 1 or not value.startswith("urn:uuid:") or uuid.UUID(value[9:]).version != 4:
                raise ValueError()
        except (ValueError, AttributeError, TypeError):
            raise ChatGPTError("Invalid ChatGPT host identity; restore its saved record.") from None
        return value


def validate_id_token(encoded: str, client_id: str, nonce: str | None = None) -> dict:
    try:
        import jwt
    except ImportError:
        raise ChatGPTError("ChatGPT sign-in requires PyJWT. Upgrade Tag or run ./install.sh --dependencies-only.") from None
    discovery = request(ISSUER + "/.well-known/openid-configuration")
    if discovery.get("issuer") != ISSUER:
        raise ChatGPTError("OpenAI discovery returned an unexpected issuer.")
    jwks_url = discovery.get("jwks_uri")
    if not isinstance(jwks_url, str) or not jwks_url.startswith(ISSUER + "/"):
        raise ChatGPTError("OpenAI discovery returned an unexpected signing-key endpoint.")
    try:
        header = jwt.get_unverified_header(encoded)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise ValueError()
        keys = request(jwks_url).get("keys", [])
        matches = [key for key in keys if isinstance(key, dict) and key.get("kid") == header["kid"]
                   and key.get("kty") == "RSA" and key.get("use", "sig") == "sig"
                   and key.get("alg", "RS256") == "RS256"]
        if len(matches) != 1:
            raise ValueError()
        claims = jwt.decode(encoded, jwt.PyJWK(matches[0]).key, algorithms=["RS256"],
                            audience=client_id, issuer=ISSUER,
                            options={"require": ["exp", "iat", "sub", "aud", "iss"]})
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise ValueError()
        if nonce is not None and (not isinstance(claims.get("nonce"), str)
                                  or not secrets.compare_digest(claims["nonce"], nonce)):
            raise ValueError()
        audiences = claims.get("aud")
        if ((isinstance(audiences, list) and len(audiences) > 1 and claims.get("azp") != client_id)
                or claims.get("azp", client_id) != client_id):
            raise ValueError()
        return claims
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise ChatGPTError("OpenAI identity validation failed. Run tag chatgpt login again.") from None


def token_record(response: dict, previous: dict) -> dict:
    """Validate a complete replacement before committing any rotating credential."""
    result = dict(previous)
    scope = response.get("scope")
    if not isinstance(scope, str):
        raise ChatGPTError("OpenAI did not return granted scopes; sign in again.")
    for key in ("access_token", "refresh_token"):
        value = response.get(key)
        if key == "refresh_token" and not value and PLAN_SCOPE not in scope.split():
            result.pop(key, None)
            continue
        if not isinstance(value, str) or not value or any(c.isspace() for c in value):
            raise ChatGPTError("OpenAI returned an incomplete token set. Sign in again if retry fails.")
        result[key] = value
    expiry = response.get("expires_in")
    if (isinstance(expiry, bool) or not isinstance(expiry, (float, int))
            or not math.isfinite(expiry) or expiry <= 0 or expiry > 86400):
        raise ChatGPTError("OpenAI returned an invalid token expiry.")
    if str(response.get("token_type", "")).lower() != "bearer":
        raise ChatGPTError("OpenAI returned an unsupported token type.")
    result.update(scopes=scope.split(), token_type="Bearer", expires_at=time.time() + expiry,
                  saved_at=time.time(), earliest_refresh_at=response.get("earliest_refresh_at"))
    return result


class Store:
    def __init__(self, home: Path | None = None):
        self.home = home or instance_home()
        self.path = self.home / "config/chatgpt/accounts.json"
        self.lock_path = self.path.with_suffix(".lock")

    def read(self) -> dict:
        record = read_object(self.path)
        if not record and not self.path.exists():
            # v1 is additive: old installations keep their existing Codex login.
            return {"schema_version": 1, "mode": "codex", "active": None, "accounts": {}}
        if (record.get("schema_version") != 1 or record.get("mode") not in {"codex", "chatgpt"}
                or not isinstance(record.get("accounts"), dict)):
            raise ChatGPTError("Unsupported ChatGPT account store; upgrade Tag or restore its saved file.")
        for key, account in record["accounts"].items():
            if (not isinstance(key, str) or not re.fullmatch(r"oaiapp_[A-Za-z0-9_-]+", key)
                    or not isinstance(account, dict) or account.get("client_id") != key
                    or not isinstance(account.get("subject"), str)
                    or not isinstance(account.get("scopes"), list)):
                raise ChatGPTError("Invalid ChatGPT registration record; restore the protected account file.")
        if record.get("active") is not None and record["active"] not in record["accounts"]:
            raise ChatGPTError("The selected ChatGPT registration is missing; restore the account file.")
        return record

    def enabled(self) -> bool:
        return self.read()["mode"] == "chatgpt"

    def status(self, limit: int = 10) -> dict:
        record = self.read()
        rows = []
        for key, account in record["accounts"].items():
            rows.append({"id": key, "email": account.get("email", ""),
                         "active": key == record.get("active"),
                         "signed_in": bool(account.get("refresh_token") or account.get("id_token")),
                         "plan_enabled": PLAN_SCOPE in account.get("scopes", []),
                         "usage_paused": bool(account.get("usage_paused"))})
        return {"schema_version": 1, "mode": record["mode"], "active": record.get("active"),
                "accounts": rows[:limit], "total": len(rows),
                "pending_registration": record.get("pending_registration"),
                "active_account": next((row for row in rows if row["active"]), None),
                "usage_url": "https://chatgpt.com/settings/usage"}

    def access(self, account_id: str | None = None, *, activate: bool = False) -> str:
        return self.lease(account_id, activate=activate)[0]

    def lease(self, account_id: str | None = None, *, activate: bool = False) -> tuple[str, float]:
        with locked(self.lock_path):
            record = self.read()
            selected = account_id or record.get("active")
            account = record["accounts"].get(selected)
            if not account or not account.get("refresh_token"):
                raise ChatGPTError("ChatGPT needs sign-in. Run tag chatgpt login ACCOUNT (see tag chatgpt status).")
            if account.get("ext_agent_host_id") != host_id():
                raise ChatGPTError("This ChatGPT registration belongs to another host. Run tag chatgpt login ACCOUNT on this host.")
            if account.get("usage_paused") and not activate:
                raise ChatGPTError(PLAN_ERRORS["subscription_sharing_usage_limit_exceeded"] +
                                   f" After reviewing limits, stop Tag and run tag chatgpt use {selected}, then start Tag.")
            if PLAN_SCOPE not in account.get("scopes", []):
                raise ChatGPTError("ChatGPT plan usage is disabled. Run tag chatgpt login ACCOUNT --consent.")
            if account.get("expires_at", 0) <= time.time() + 120:
                try:
                    response = request(TOKEN, form={"grant_type": "refresh_token", "client_id": selected,
                                                   "refresh_token": account["refresh_token"], "resource": RESOURCE})
                except RequestError as exc:
                    if exc.code in TERMINAL_REFRESH_ERRORS:
                        self._clear(account)
                        atomic_write(self.path, record)
                        raise ChatGPTError("ChatGPT session expired or was revoked. Run tag chatgpt login ACCOUNT.") from None
                    raise
                replacement = token_record(response, account)
                if response.get("id_token"):
                    claims = validate_id_token(response["id_token"], selected)
                    if claims["sub"] != account["subject"]:
                        raise ChatGPTError("Refreshed ChatGPT identity changed; sign in again.")
                    replacement["id_token"] = response["id_token"]
                record["accounts"][selected] = account = replacement
                atomic_write(self.path, record)
                if PLAN_SCOPE not in account["scopes"]:
                    raise ChatGPTError("ChatGPT plan permission was removed. Run tag chatgpt login ACCOUNT --consent.")
            if activate:
                account.pop("usage_paused", None)
                record.update(active=selected, mode="chatgpt")
                atomic_write(self.path, record)
            return account["access_token"], account["expires_at"]

    def pause_usage(self) -> None:
        with locked(self.lock_path):
            record = self.read()
            account = record["accounts"].get(record.get("active"))
            if account:
                account["usage_paused"] = True
                atomic_write(self.path, record)

    @staticmethod
    def _clear(account: dict) -> None:
        for key in ("access_token", "refresh_token", "id_token", "expires_at", "earliest_refresh_at"):
            account.pop(key, None)

    def logout(self, account_id: str | None = None) -> bool:
        with locked(self.lock_path):
            record = self.read()
            selected = account_id or record.get("active")
            account = record["accounts"].get(selected)
            if account is None:
                raise ChatGPTError("Select an account from tag chatgpt status.")
            revoked = not account.get("refresh_token")
            if not revoked:
                try:
                    endpoint = request(ISSUER + "/.well-known/openid-configuration").get("revocation_endpoint")
                    if not isinstance(endpoint, str) or not endpoint.startswith(ISSUER + "/"):
                        raise ChatGPTError("Invalid revocation endpoint.")
                    request(endpoint, form={"token": account["refresh_token"],
                                           "token_type_hint": "refresh_token", "client_id": selected})
                    revoked = True
                except ChatGPTError:
                    pass
            self._clear(account)
            # Keep active + chatgpt mode: logout must never change the billing path.
            atomic_write(self.path, record)
            return revoked

    def use_codex(self) -> None:
        with locked(self.lock_path):
            record = self.read()
            record["mode"] = "codex"
            atomic_write(self.path, record)

    def login(self, account_id: str | None = None, *, consent: bool = False, timeout: float = 180) -> str:
        before = self.read()
        pending = before.get("pending_registration")
        if account_id and account_id not in before["accounts"] and account_id != pending:
            raise ChatGPTError("Unknown account. Use tag chatgpt status, or tag chatgpt login to add one.")
        previous = dict(before["accounts"].get(account_id, {}))
        host = host_id()
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        received: dict[str, str] = {}

        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Callback URLs contain single-use authorization codes.

            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
                valid = (parsed.path == "/auth/callback" and all(len(v) == 1 for v in query.values())
                         and secrets.compare_digest(query.get("state", [""])[0].encode(), state.encode()))
                if valid:
                    received.update({k: v[0] for k, v in query.items()})
                body = b"Return to Tag to finish sign-in." if valid else b"Invalid sign-in callback. Return to Tag."
                self.send_response(200 if valid else 400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class Listener(HTTPServer):
            def get_request(self):
                connection, address = super().get_request()
                connection.settimeout(2)
                return connection, address

        with Listener(("127.0.0.1", 0), Callback) as server:
            server.timeout = 0.5
            redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
            params = {"client_id": account_id or "dynamic_agent_client", "ext_agent_host_id": host,
                      "response_type": "code", "redirect_uri": redirect, "scope": SCOPES,
                      "resource": RESOURCE, "state": state, "nonce": nonce,
                      "code_challenge_method": "S256",
                      "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")}
            if not account_id:
                params["agent_name_hint"] = APP_NAME
            elif previous.get("id_token"):
                params["id_token_hint"] = previous["id_token"]
            if consent:
                params["prompt"] = "consent"
            # Never print this URL: returning sign-in can contain an ID token.
            if not webbrowser.open(AUTHORIZE + "?" + urllib.parse.urlencode(params)):
                raise ChatGPTError("Could not open a browser. Run tag chatgpt login in a local desktop terminal.")
            deadline = time.monotonic() + timeout
            while not received and time.monotonic() < deadline:
                server.handle_request()
        if not received:
            raise ChatGPTError("ChatGPT sign-in timed out. Run tag chatgpt login again.")
        if received.get("error"):
            raise ChatGPTError("ChatGPT authorization was declined or failed. Your previous account is unchanged.")
        issued = received.get("client_id", account_id)
        if (not isinstance(issued, str) or not re.fullmatch(r"oaiapp_[A-Za-z0-9_-]+", issued)
                or (account_id and issued != account_id) or not received.get("code")):
            raise ChatGPTError("Incomplete or mismatched ChatGPT registration; run tag chatgpt login again.")
        try:
            response = request(TOKEN, form={"grant_type": "authorization_code", "client_id": issued,
                                           "code": received["code"], "code_verifier": verifier,
                                           "redirect_uri": redirect, "resource": RESOURCE})
        except RequestError as exc:
            if exc.code == "invalid_grant" and not previous:
                # Keep only the issued registration ID, never the rejected code.
                # This record is not an authenticated account and cannot run tasks.
                with locked(self.lock_path):
                    record = self.read()
                    record["pending_registration"] = issued
                    atomic_write(self.path, record)
                raise ChatGPTError(f"The sign-in code expired. Run tag chatgpt login {issued} to retry the same registration.") from None
            raise
        claims = validate_id_token(response.get("id_token", ""), issued, nonce)
        if previous and claims["sub"] != previous["subject"]:
            raise ChatGPTError("ChatGPT account does not match the selected registration. Previous account unchanged.")
        replacement = token_record(response, {"client_id": issued, "subject": claims["sub"],
                                              "issuer": ISSUER, "email": claims.get("email", ""),
                                              "ext_agent_host_id": host, "id_token": response["id_token"]})
        with locked(self.lock_path):
            record = self.read()
            if record != before:
                raise ChatGPTError("ChatGPT accounts changed during sign-in. Retry with the intended account.")
            record["accounts"][issued] = replacement
            if record.get("pending_registration") == issued:
                record.pop("pending_registration")
            record.update(active=issued, mode="chatgpt")
            atomic_write(self.path, record)
        return issued


def enabled() -> bool:
    return Store().enabled()


def models() -> list[dict]:
    store = Store()
    try:
        payload = request(RESOURCE + "/models", token=store.access())
    except RequestError as exc:
        if exc.code == "subscription_sharing_usage_limit_exceeded":
            store.pause_usage()
        raise
    if not isinstance(payload.get("models"), list):
        raise ChatGPTError("ChatGPT returned an invalid model catalog; retry later.")
    return [{"model": item["slug"], "displayName": item.get("display_name", item["slug"]),
             "isDefault": False, "additionalSpeedTiers": []}
            for item in payload["models"] if isinstance(item, dict) and item.get("visibility") == "list"
            and isinstance(item.get("slug"), str) and item["slug"]]


def provider_options() -> list[str]:
    values = {
        "model_provider": "openai_chatgpt_plan",
        "model_providers.openai_chatgpt_plan.name": "ChatGPT plan",
        "model_providers.openai_chatgpt_plan.base_url": RESOURCE,
        "model_providers.openai_chatgpt_plan.env_key": TOKEN_ENV,
        "model_providers.openai_chatgpt_plan.wire_api": "responses",
        "model_providers.openai_chatgpt_plan.requires_openai_auth": False,
        "model_providers.openai_chatgpt_plan.supports_websockets": False,
        "features.fast_mode": False,
        "features.tool_search": False,
        "features.apps": False,
        "features.image_generation": False,
        "features.computer_use": False,
        "shell_environment_policy.exclude": [TOKEN_ENV],
        "service_tier": "default",
    }
    return [part for key, value in values.items() for part in ("-c", key + "=" + json.dumps(value))]


def cli(arguments: list[str], *, json_output: bool = False, dry_run: bool = False,
        consent: bool = False, limit: int = 10) -> int:
    action = arguments[0] if arguments else "status"
    account_id = arguments[1] if len(arguments) == 2 else None
    if (action not in {"status", "login", "use", "logout", "use-codex"} or len(arguments) > 2
            or (account_id and action in {"status", "use-codex"}) or (action == "use" and not account_id)):
        raise ChatGPTError("Use tag chatgpt status | login [ACCOUNT] | use ACCOUNT | logout [ACCOUNT] | use-codex.")
    if consent and action != "login":
        raise ChatGPTError("--consent is only for tag chatgpt login.")
    store = Store()
    if action != "status" and not dry_run:
        # Switching accounts changes the catalog and the tokens held by running
        # children. Require a stopped bridge so no in-flight task keeps old access.
        try:
            from .tag_cli import process_for
        except ImportError:
            from tag_cli import process_for
        if process_for(store.home / "state/slack.json"):
            prefix = "tag " + (os.getenv("TAG_ID", "default") + " " if os.getenv("TAG_ID", "default") != "default" else "")
            raise ChatGPTError(f"Stop this Tag before changing its ChatGPT account: {prefix}stop. Then repeat the account command and run {prefix}start.")
    message = ""
    if dry_run:
        result = {"schema_version": 1, "dry_run": True, "action": action, "account": account_id}
    else:
        if action == "login":
            if json_output or not sys.stdin.isatty():
                raise ChatGPTError("Browser consent is required. Run tag chatgpt login in an interactive local terminal.")
            print("Continue with ChatGPT · approve Tag's use of your plan in your browser.", file=sys.stderr)
            selected = store.login(account_id, consent=consent)
            message = "ChatGPT connected. First task remains unverified."
            if not next(row for row in store.status(10000)["accounts"] if row["id"] == selected)["plan_enabled"]:
                message = f"Signed in; plan usage is disabled. Run tag chatgpt login {selected} --consent."
        elif action == "use":
            store.access(account_id, activate=True)
        elif action == "logout":
            if not store.logout(account_id):
                message = "Signed out locally. Remote revocation was not confirmed; disconnect Tag in ChatGPT Settings."
            else:
                message = "Signed out. The registration is retained for future sign-in."
        elif action == "use-codex":
            store.use_codex()
            message = "Using the existing Codex CLI sign-in for new requests."
        result = store.status(limit)
        if message:
            result["message"] = message
    if json_output:
        print(json.dumps(result, indent=2))
    else:
        if dry_run:
            print(f"Would perform ChatGPT action: {action}.")
        else:
            print(message or f"Authentication: {result['mode']}")
            for row in result["accounts"]:
                label = "active" if row["active"] else "saved"
                state = "signed in" if row["signed_in"] else "sign-in required"
                # Strip control characters from identity display metadata.
                email = "".join(c for c in str(row["email"]) if c.isprintable())[:200]
                print(f"{row['id']}  {email}  {label} · {state}")
            if result.get("pending_registration"):
                print(f"Finish pending registration: tag chatgpt login {result['pending_registration']}")
            print("Usage and app limits: https://chatgpt.com/settings/usage")
    return 0
