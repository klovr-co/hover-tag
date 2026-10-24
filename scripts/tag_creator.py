"""Create an approved Tag by driving ``tag add --json``, as Tag.app does.

Only Chief-of-Tag's bridge calls this, after a person approves the proposal
in Slack. The driver answers setup's questions from the proposal. When setup
needs something only a person can do, such as an admin approving the install,
the driver saves progress and returns that one step. ``resume`` continues the
same Tag later, so a Slack app is never created twice.

Slack apps are created by the Slack CLI, signed in as the service account in
its own config folder (see ``slack_cli_config``). The CLI creates the app,
installs it and hands over its tokens. If the service account isn't signed in
but an app configuration token is saved, the fallback creates the app with
``apps.manifest.create`` and asks a person to install it and finish setup in
Tag.app or the CLI. Tokens are never asked for in Slack.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

try:
    import slack_cli_config
    import tag_chief
    from tag_memory import write_document
except ImportError:
    from scripts import slack_cli_config, tag_chief
    from scripts.tag_memory import write_document

ROOT = Path(__file__).resolve().parents[1]
CREATION_TIMEOUT_SECONDS = 20 * 60
TOKEN_RE = re.compile(r"\b(?:xox[a-z]-|xapp-|xoxe[.-])[A-Za-z0-9.-]+")
APP_LIMIT_RE = re.compile(r"limit of 10 apps|10 apps limit", re.IGNORECASE)
# Setup questions that only a person can answer. Each maps to the one step to post.
HUMAN_STEPS = {
    "app_install": ("Slack needs a workspace admin to approve installing *{name}*. Ask an admin to approve "
                    "the request in Slack (Manage apps → Requests), then press *Check again*."),
    "slack_login": ("The Tag service account isn't signed in to Slack on this computer. Run "
                    "`tag chief-of-tag sign-in` once, then press *Check again*."),
    "service_account": ("The Tag service account isn't signed in to Slack on this computer. Run "
                        "`tag chief-of-tag sign-in` once, then press *Check again*."),
    "workspace": ("The Tag service account isn't signed in to this workspace. Run "
                  "`tag chief-of-tag sign-in` and choose this workspace, then press *Check again*."),
    "org_workspace": ("The service account signed in to a Slack organization. Finish choosing the workspace in "
                      "Tag.app or with `tag {tag} setup`, then press *Check again*."),
    "ai_connection": ("No AI connection is ready for *{name}*. Connect one in Tag.app Settings or with "
                      "`tag settings ai sign-in`, then press *Check again*."),
    "default_model": ("The model chosen for *{name}* isn't available on this computer's AI connection. "
                      "Choose one in Tag.app or with `tag {tag} setup`, then press *Check again*."),
    "credentials_next": ("Slack didn't hand over *{name}*'s tokens. Finish in Tag.app or with `tag {tag} setup` "
                         "and choose *Enter tokens manually*. Never paste tokens in Slack. Then press *Check again*."),
    "app_limit": ("This Slack workspace has reached the free plan's limit of 10 apps. Remove an unused app in "
                  "Slack (Manage apps), or upgrade the plan, then press *Check again*. Tag never removes apps."),
}
OTHER_STEP = ("Setup for *{name}* needs a decision Tag won't make for you: {prompt}. Finish it in Tag.app or "
              "with `tag {tag} setup`, then press *Check again*.")


@dataclass
class Outcome:
    state: str  # created, waiting_on_slack, failed
    tag: str | None
    message: str
    step: str | None = None
    notes: list[str] = field(default_factory=list)


def safe(text: str) -> str:
    return TOKEN_RE.sub("[redacted Slack token]", text)


def tag_cli(*arguments: str) -> list[str]:
    return [sys.executable, str(ROOT / "scripts/tag_cli.py"), *arguments]


def setup_environment(root: Path, folder: Path) -> dict[str, str]:
    """The bridge's environment without any Tag's settings, using the service account's sign-in."""
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("SLACK_", "OPENTAG_", "MFS_"))}
    environment.update(TAG_HOME=str(root), TAG_SETUP_PROTOCOL="jsonl", NO_COLOR="1",
                       **{slack_cli_config.CONFIG_DIR_ENV: str(folder)})
    return environment


class Answers:
    """Answer setup's questions from an approved proposal; pause for anything else."""

    def __init__(self, proposal: dict[str, Any], *, sign_in: str, model: str, resume: bool):
        self.proposal, self.sign_in, self.model = proposal, sign_in, model
        # On resume the person says the blocking step is done, so check the install once.
        self.install_checks = 1 if resume else 0
        self.step: str | None = None
        self.prompt = ""

    def __call__(self, question: dict[str, Any]) -> Any:
        qid = str(question.get("id") or "")
        ids = [str(item) for item in question.get("option_ids") or []]
        options = [str(item) for item in question.get("options") or []]
        if qid == "profile":
            return {"name": self.proposal["name"], "description": self.proposal["description"]}
        if qid == "default_model" and self.model in ids:
            return {"value": self.model, "effort": "default"}
        if qid == "workspace" and self.sign_in in ids:
            return self.sign_in
        if qid == "approve_setup" and "create" in ids:
            return "create"
        if qid == "channels":
            wanted = set(self.proposal.get("channels") or [])
            details = question.get("channels") or []
            return [index for index, item in enumerate(details) if isinstance(item, dict) and item.get("id") in wanted]
        if qid == "app_install" and self.install_checks and "Check approval again" in options:
            self.install_checks -= 1
            return options.index("Check approval again")
        self.step = qid if qid in HUMAN_STEPS else "other"
        self.prompt = str(question.get("prompt") or qid)
        return None  # Pause: save progress and stop.


def drive(command: list[str], environment: dict[str, str], answers: Answers, *,
          on_tag: Callable[[str], None], timeout: float = CREATION_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Run one JSON-lines setup session to its result event."""
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                                   text=True, encoding="utf-8", env=environment, cwd=str(ROOT))
        timer = threading.Timer(timeout, process.kill)
        timer.start()
        result: dict[str, Any] = {}
        messages: list[str] = []
        try:
            assert process.stdin is not None and process.stdout is not None
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue  # Clients ignore lines that aren't JSON objects.
                if not isinstance(event, dict):
                    continue
                kind = event.get("type")
                if kind == "tag" and isinstance(event.get("tag"), str):
                    on_tag(event["tag"])
                elif kind == "message":
                    messages.append(safe(str(event.get("text") or "")))
                    if APP_LIMIT_RE.search(messages[-1]):
                        answers.step = "app_limit"
                elif kind == "question":
                    answer = answers(event)
                    reply = {"answer": None, "pause": True} if answer is None else {"answer": answer}
                    try:
                        process.stdin.write(json.dumps(reply) + "\n")
                        process.stdin.flush()
                    except (BrokenPipeError, OSError):
                        break
                elif kind == "result":
                    result = event
            process.stdin.close()
            process.wait(timeout=60)
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            for stream in (process.stdin, process.stdout):
                if stream is not None and not stream.closed:
                    stream.close()
        errors.seek(0)
        tail = [safe(line.strip()) for line in errors.read().splitlines() if line.strip()][-3:]
    if not result:
        result = {"status": "failed", "error": "Setup stopped without a result" + (f": {tail[-1]}" if tail else "")}
    result["messages"] = messages[-5:]
    return result


def step_message(step: str, proposal: dict[str, Any], tag: str | None, prompt: str = "") -> str:
    template = HUMAN_STEPS.get(step, OTHER_STEP)
    return template.format(name=proposal["name"], tag=tag or "NAME", prompt=prompt)


def create(root: Path, record: dict[str, Any], *, chief_values: dict[str, str],
           on_tag: Callable[[str], None], run: Callable[..., dict[str, Any]] = drive,
           finish: Callable[[Path, str, dict[str, Any]], list[str]] | None = None,
           fallback: "ConfigTokenFallback | None" = None) -> Outcome:
    """Create the Tag for an approved proposal, or continue one that waited for a person."""
    proposal = record["proposal"]
    if tag_chief.chief_id(root) != record.get("chief"):
        raise tag_chief.ChiefRefused(f"Only {tag_chief.ROLE_NAME} can create Tags. Nothing was created.")
    tag = record.get("tag")
    if tag and setup_complete(root, tag):
        # A person finished setup in Tag.app or the CLI after the fallback; just start it.
        notes = (finish or finish_tag)(root, tag, {**proposal, "requester": record.get("requester")})
        return Outcome("created", tag, f"Created *{proposal['name']}*. Mention it directly, or ask me and I'll route to it.",
                       notes=notes)
    sign_in = chief_values.get("OPENTAG_CHIEF_SLACK_SIGN_IN", "")
    folder = slack_cli_config.service_account_dir(root, chief_values)
    if not sign_in:
        fallback = fallback or ConfigTokenFallback(folder)
        if not tag and fallback.available():
            return fallback.create(root, proposal, chief_values, on_tag=on_tag)
        return Outcome("waiting_on_slack", tag, step_message("service_account", proposal, tag), "service_account")
    model = proposal.get("model") or chief_values.get("OPENTAG_DEFAULT_MODEL", "")
    answers = Answers(proposal, sign_in=sign_in, model=model, resume=bool(tag))
    environment = setup_environment(root, folder)
    with tempfile.TemporaryDirectory(prefix="tag-create-") as directory:
        if tag:
            command = tag_cli(tag, "setup", "--json")
        else:
            preset = {"OPENTAG_BOT_NAME": proposal["name"], "OPENTAG_BOT_DESCRIPTION": proposal["description"],
                      "SLACK_ALLOWED_USER_IDS": chief_values.get("SLACK_ALLOWED_USER_IDS", ""),
                      slack_cli_config.TAG_SETTING: str(folder)}
            if model:
                preset["OPENTAG_DEFAULT_MODEL"] = model
            path = Path(directory) / "preset.json"
            path.write_text(json.dumps({key: value for key, value in preset.items() if value}), encoding="utf-8")
            command = tag_cli("add", "--json", "--preset", str(path))

        def saw_tag(value: str) -> None:
            nonlocal tag
            tag = value
            on_tag(value)
        result = run(command, environment, answers, on_tag=saw_tag)
    tag = str(result.get("tag") or tag or "") or None
    if tag and result.get("tag"):
        on_tag(tag)  # add renames the Tag after its Slack IDs; keep the final name.
    status = result.get("status")
    if status == "complete" and tag:
        notes = (finish or finish_tag)(root, tag, {**proposal, "requester": record.get("requester")})
        return Outcome("created", tag, f"Created *{proposal['name']}*. Mention it directly, or ask me and I'll route to it.",
                       notes=notes)
    if answers.step:
        return Outcome("waiting_on_slack", tag, step_message(answers.step, proposal, tag, answers.prompt), answers.step)
    if status == "paused":
        return Outcome("waiting_on_slack", tag, step_message("other", proposal, tag, "setup paused"), "other")
    error = safe(str(result.get("error") or "Setup failed"))
    return Outcome("failed", tag, f"Couldn't create *{proposal['name']}*: {error}")


def setup_complete(root: Path, tag: str) -> bool:
    try:
        import tag_config, tag_instances
    except ImportError:
        from scripts import tag_config, tag_instances
    try:
        home = tag_instances.resolve(root, tag).home
        return not tag_config.config_errors(tag_config.load_config(home / "config/settings.json"))
    except (OSError, ValueError, RuntimeError):
        return False


def finish_tag(root: Path, tag: str, proposal: dict[str, Any], *,
               call: Callable[[list[str]], subprocess.CompletedProcess] | None = None) -> list[str]:
    """Record, keep running and start a new Tag, and hand over its starting routines."""
    call = call or (lambda command: subprocess.run(command, capture_output=True, text=True, check=False,
                                                    env={**os.environ, "TAG_HOME": str(root)}, timeout=300))
    tag_chief.record_created(root, tag)
    notes = []
    call(tag_cli("autostart", "keep", tag, "--json"))
    started = call(tag_cli(tag, "start", "--json"))
    if started.returncode:
        notes.append(f"It isn't running yet. Start it in Tag.app or with `tag {tag} start`.")
    if proposal.get("routines"):
        notes.append(save_routines(root, tag, proposal))
    return notes


ROUTINES_FILE = "state/pending-routines.json"


def routine_definitions(proposal: dict[str, Any]) -> list[dict[str, str]]:
    """Starting routines as ``tag NAME routine add --from-json`` objects, one per routine."""
    channels = proposal.get("channels") or []
    definitions = []
    for index, item in enumerate(proposal.get("routines") or [], start=1):
        definition = {"name": f"{proposal['name']} routine {index}", "schedule": item["schedule"],
                      "prompt": item["prompt"]}
        if channels:
            definition["channel"] = channels[0]
        if proposal.get("requester"):
            definition["owner"] = proposal["requester"]
        definitions.append(definition)
    return definitions


def save_routines(root: Path, tag: str, proposal: dict[str, Any]) -> str:
    """Hand starting routines to the routines feature through a documented file.

    ``<Tag home>/state/pending-routines.json`` (schema 1) lists ``routines``,
    each the exact object ``tag NAME routine add --from-json`` takes (name,
    schedule, prompt, channel, owner), approved by the person who approved the
    Tag. Nothing here depends on the routines feature.

    TODO(#237): once routines ship, import this file when the Tag starts (or
    call ``tag NAME routine add --from-json -`` here when ``tag version``
    lists the ``routines`` capability), then remove the file.
    """
    try:
        import tag_instances
    except ImportError:
        from scripts import tag_instances
    home = tag_instances.resolve(root, tag).home
    routines = routine_definitions(proposal)
    write_document(home / ROUTINES_FILE, {"schema_version": 1, "source": "chief-of-tag",
                                          "approved_by": proposal.get("requester"), "routines": routines})
    return f"Its {len(routines)} starting routine(s) are saved and are set up when routines are available."


# Fallback: create the app with an app configuration token ------------------------------


def slack_post(method: str, token: str, parameters: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"https://slack.com/api/{method}", data=urllib.parse.urlencode(parameters).encode(),
        headers={"Authorization": f"Bearer {token}"} if token else {}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read())
    return payload if isinstance(payload, dict) else {"ok": False, "error": "invalid_response"}


class ConfigTokenFallback:
    """``apps.manifest.create`` with an app configuration token that is rotated before it expires.

    A person creates the first token pair at api.slack.com/apps and saves it
    with ``tag chief-of-tag config-token``. Access tokens last 12 hours;
    each refresh token works once, so the new pair is saved before it is used.
    """

    def __init__(self, folder: Path, api: Callable[[str, str, dict[str, str]], dict[str, Any]] = slack_post,
                 clock: Callable[[], float] = time.time):
        self.path = folder / "config-token.json"
        self.api, self.clock = api, clock

    def available(self) -> bool:
        return self._read() is not None

    def _read(self) -> dict[str, Any] | None:
        try:
            record = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if isinstance(record, dict) and str(record.get("refresh_token", "")).startswith("xoxe-"):
            return record
        return None

    def save(self, token: str, refresh_token: str, expires_at: float) -> None:
        if not token.startswith("xoxe.") or not refresh_token.startswith("xoxe-"):
            raise ValueError("Use the app configuration token (xoxe.…) and its refresh token (xoxe-…)")
        write_document(self.path, {"schema_version": 1, "token": token, "refresh_token": refresh_token,
                                   "expires_at": expires_at})

    def token(self) -> str:
        record = self._read()
        if record is None:
            raise RuntimeError("No app configuration token is saved.")
        if float(record.get("expires_at") or 0) - self.clock() > 300:
            return str(record["token"])
        rotated = self.api("tooling.tokens.rotate", "", {"refresh_token": str(record["refresh_token"])})
        if not rotated.get("ok") or not rotated.get("token") or not rotated.get("refresh_token"):
            raise RuntimeError(f"Slack didn't renew the app configuration token ({rotated.get('error', 'unknown')}). "
                               "Save a new one with tag chief-of-tag config-token.")
        self.save(str(rotated["token"]), str(rotated["refresh_token"]), float(rotated.get("exp") or self.clock() + 43200))
        return str(rotated["token"])

    def create(self, root: Path, proposal: dict[str, Any], chief_values: dict[str, str], *,
               on_tag: Callable[[str], None]) -> Outcome:
        try:
            import slack_app_create, tag_config, tag_instances
        except ImportError:
            from scripts import slack_app_create, tag_config, tag_instances
        manifest = slack_app_create.manifest_for(proposal["name"], description=proposal["description"],
                                                 enterprise=bool(chief_values.get("SLACK_ENTERPRISE_ID")))
        created = self.api("apps.manifest.create", self.token(), {"manifest": json.dumps(manifest)})
        app_id = created.get("app_id")
        if not created.get("ok") or not isinstance(app_id, str):
            return Outcome("failed", None, f"Slack didn't create *{proposal['name']}* ({created.get('error', 'unknown')}). "
                                           "Nothing else was changed.")
        context = tag_instances.create(root, tag_instances.suggest_name(root, "new tag"), provisional=True)
        on_tag(context.tag_id)
        try:
            from tag_paths import initialize_instance
        except ImportError:
            from scripts.tag_paths import initialize_instance
        initialize_instance(context.home)
        tag_config.update_config(context.home / "config/settings.json", {key: value for key, value in {
            "OPENTAG_BOT_NAME": proposal["name"], "OPENTAG_BOT_DESCRIPTION": proposal["description"],
            "OPENTAG_DEFAULT_MODEL": proposal.get("model") or chief_values.get("OPENTAG_DEFAULT_MODEL", ""),
            "SLACK_ALLOWED_USER_IDS": chief_values.get("SLACK_ALLOWED_USER_IDS", ""),
            "SLACK_TEAM_ID": chief_values.get("SLACK_TEAM_ID", ""), "SLACK_APP_ID": app_id,
        }.items() if value})
        link = created.get("oauth_authorize_url")
        install = f"<{link}|install it>" if isinstance(link, str) and link.startswith("https://") else (
            f"install it from <https://api.slack.com/apps/{app_id}/install-on-team|its app page>")
        return Outcome("waiting_on_slack", context.tag_id, (
            f"Slack created the app for *{proposal['name']}* ({app_id}). A person needs to {install}, then add its "
            f"app-level token in Tag.app or with `tag {context.tag_id} setup` (choose *Enter tokens manually*). "
            "Never paste tokens in Slack. Then press *Check again*."), "manual_install")
