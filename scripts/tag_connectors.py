"""One installation-level list of MCP connectors that Tags may use (ADR 0014).

The list lives in ``state/connectors.json`` under the installation root and is
shared by every Tag, like ChatGPT accounts. It holds connector definitions and
which Tags may use each one. It never holds tokens or secret values: sign-ins
stay in mcp-remote's files, and stdio servers get environment variable
*names* that the backend reads from Tag's environment. Nothing is written into
the backends' own config: each run is given the connectors its Tag may use, and
remote ones start through a pinned mcp-remote that keeps one sign-in for both
backends in Tag's folder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

try:
    from .tag_locks import LifecycleLock, LockBusy
    from .tag_paths import restrict_windows_acl, tag_home
    from . import tag_instances
except ImportError:
    from tag_locks import LifecycleLock, LockBusy
    from tag_paths import restrict_windows_acl, tag_home
    import tag_instances

SCHEMA_VERSION = 1
# Both backends accept these; "__" would break Claude's mcp__SERVER__TOOL names.
NAME_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]|_(?!_)){0,47}")
ENV_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
# Remote connectors run through this exact mcp-remote, installed in Tag's folder (ADR 0014).
MCP_REMOTE_VERSION = "0.15.2"
# mcp-remote keeps sign-ins in <MCP_REMOTE_CONFIG_DIR>/mcp-remote-v1/<md5(url)>_tokens.json.
MCP_REMOTE_STORE = "mcp-remote-v1"
# A sign-in that stops working fails the connector quickly instead of holding up the run.
RUN_AUTH_TIMEOUT_SECONDS = 10
SIGN_IN_TIMEOUT_SECONDS = 900
BRIDGE_STARTUP_SECONDS = 60
# mcp-remote discovers sign-in settings before connecting; slow servers take most of a minute.
CHECK_TIMEOUT_SECONDS = 90
NODE_MISSING = ("Remote connectors need Node.js with npm. Install it from https://nodejs.org "
                "(or with your package manager), then retry.")
USAGE = ("tag connector list | show NAME | add NAME (--url URL | --command \"CMD ARGS\") [--env NAME ...] | "
         "remove NAME | allow NAME TAG... | deny NAME TAG... | sign-in NAME [--device-code] | check NAME")


class ConnectorError(ValueError):
    """A connector request that can't be done; the message says what to do instead."""


def store_path(root: Path | None = None) -> Path:
    return (root or tag_home()) / "state/connectors.json"


def validate_name(name: str) -> str:
    if not NAME_PATTERN.fullmatch(name or ""):
        raise ConnectorError("Connector names use lowercase letters, digits, - and single _, "
                             "start with a letter or digit, and have at most 48 characters.")
    return name


def validate_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ConnectorError("The connector URL must be a full https:// address.")
    if parsed.scheme == "http" and parsed.hostname not in LOCAL_HOSTS:
        raise ConnectorError("Use https:// for connectors that aren't on this computer.")
    if parsed.username or parsed.password:
        raise ConnectorError("Don't put sign-in details in the URL; the backend signs in for you.")
    return url


def validate_env_names(names: list[str]) -> list[str]:
    for name in names:
        if not ENV_NAME_PATTERN.fullmatch(name):
            raise ConnectorError(f"--env takes an environment variable name such as API_KEY, not a value: {name[:40]!r}"
                                 if "=" not in name else
                                 "--env takes only the variable's name. Tag never stores its value; "
                                 "set it in the environment you start Tag from.")
    return sorted(set(names))


def atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or path.parent.is_symlink():
        raise ConnectorError("The connector list must not be a symlink.")
    restrict_windows_acl(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".connectors-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Store:
    """The connector list. Changes take the store lock and replace the file atomically."""

    def __init__(self, root: Path | None = None):
        self.root = (root or tag_home()).expanduser().absolute()
        self.path = store_path(self.root)

    def read(self) -> dict:
        try:
            if self.path.is_symlink():
                raise ValueError()
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("connectors", {}), dict):
                raise ValueError()
        except FileNotFoundError:
            return {"version": SCHEMA_VERSION, "connectors": {}}
        except (ValueError, UnicodeError):
            raise ConnectorError(f"Cannot read the connector list at {self.path}. "
                                 "Fix or move the file, then retry.") from None
        if data.get("version") != SCHEMA_VERSION:
            raise ConnectorError("The connector list was written by a newer Tag. Run tag upgrade.")
        data.setdefault("connectors", {})
        return data

    @contextmanager
    def change(self):
        lock = LifecycleLock(self.root / "state/connectors.lock")
        deadline = time.monotonic() + 30
        while True:
            try:
                lock.acquire()
                break
            except LockBusy:
                if time.monotonic() >= deadline:
                    raise ConnectorError("Another connector change is running; retry shortly.") from None
                time.sleep(0.05)
        try:
            data = self.read()
            yield data
            atomic_write(self.path, data)
        finally:
            lock.release()

    def list(self) -> list[dict]:
        return [view(name, entry, sign_in_state(self.root, name, entry))
                for name, entry in sorted(self.read()["connectors"].items())]

    def entry(self, name: str) -> dict:
        entry = self.read()["connectors"].get(name)
        if entry is None:
            raise ConnectorError(f"No connector named '{name}'. See tag connector list.")
        return entry

    def get(self, name: str) -> dict:
        entry = self.entry(name)
        return view(name, entry, sign_in_state(self.root, name, entry))

    def add(self, name: str, *, url: str | None = None, command: list[str] | None = None,
            env: list[str] | None = None) -> dict:
        validate_name(name)
        if bool(url) == bool(command):
            raise ConnectorError("Give exactly one of --url (a remote connector) or --command (a local one).")
        entry: dict = {"added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "allowed": []}
        if url:
            entry.update(transport="http", url=validate_url(url))
            if env:
                raise ConnectorError("--env is only for --command connectors.")
        else:
            entry.update(transport="stdio", command=command[0], args=command[1:],
                         env=validate_env_names(env or []))
        with self.change() as data:
            if name in data["connectors"]:
                raise ConnectorError(f"A connector named '{name}' already exists. "
                                     f"Remove it first: tag connector remove {name}.")
            data["connectors"][name] = entry
        return view(name, entry)

    def remove(self, name: str) -> dict:
        """Remove the connector and its sign-in on this computer."""
        with self.change() as data:
            entry = data["connectors"].pop(name, None)
            if entry is None:
                raise ConnectorError(f"No connector named '{name}'. See tag connector list.")
            shutil.rmtree(auth_dir(self.root, name), ignore_errors=True)
        return view(name, entry)

    def set_allowed(self, name: str, tags: list[str], allowed: bool) -> dict:
        with self.change() as data:
            entry = data["connectors"].get(name)
            if entry is None:
                raise ConnectorError(f"No connector named '{name}'. See tag connector list.")
            rows = [row for row in entry.get("allowed", []) if row.get("tag") not in tags]
            if allowed:
                rows.extend({"tag": tag} for tag in tags)
            entry["allowed"] = sorted(rows, key=lambda row: row["tag"])
        return view(name, entry)

    def allowed_for(self, tag_id: str) -> set[str]:
        """Names of the connectors this Tag may use."""
        return {name for name, entry in self.read()["connectors"].items()
                if any(row.get("tag") == tag_id for row in entry.get("allowed", []))}


    def rename_tag(self, old: str, new: str) -> None:
        """Carry a renamed Tag's access over to its new name."""
        if not self.path.exists():
            return
        with self.change() as data:
            for entry in data["connectors"].values():
                for row in entry.get("allowed", []):
                    if row.get("tag") == old:
                        row["tag"] = new

    def forget_tag(self, tag_id: str) -> None:
        """Drop a removed Tag from every allow list, so a later Tag of that name starts with none."""
        if not self.path.exists():
            return
        with self.change() as data:
            for entry in data["connectors"].values():
                entry["allowed"] = [row for row in entry.get("allowed", []) if row.get("tag") != tag_id]


def bridge_script(root: Path) -> Path:
    return root / "tools/mcp-remote/node_modules/mcp-remote/dist/proxy.js"


def auth_dir(root: Path, name: str) -> Path:
    """mcp-remote's folder for one connector. mcp-remote keys sign-ins by URL only,
    so a folder per connector keeps two accounts on one service apart."""
    return root / "state/connector-auth" / validate_name(name)


def sign_in_file(root: Path, name: str, url: str) -> Path:
    """Where mcp-remote keeps this connector's sign-in. Tag only checks it exists."""
    return auth_dir(root, name) / MCP_REMOTE_STORE / f"{hashlib.md5(url.encode()).hexdigest()}_tokens.json"


def run_plan() -> tuple[dict[str, dict], dict[str, str]]:
    """The server definitions for this Tag's run, and the allowed connectors left
    out with the reason. Nothing outside a Tag run."""
    tag_id = os.getenv("TAG_ID")
    if not tag_id or not os.getenv("TAG_HOME"):
        return {}, {}
    store = Store()
    definitions, skipped = {}, {}
    node = None
    for name, entry in sorted(store.read()["connectors"].items()):
        if not any(row.get("tag") == tag_id for row in entry.get("allowed", [])):
            continue
        if entry.get("transport") != "http":
            definitions[name] = {"command": entry["command"], "args": entry.get("args", []),
                                 "env_names": entry.get("env", [])}
            continue
        # Both backends start the same bridge, so they share its one sign-in.
        node = node or shutil.which("node")
        if node is None or not bridge_script(store.root).is_file():
            skipped[name] = "Node or mcp-remote isn't installed; see tag doctor."
        elif sign_in_state(store.root, name, entry) in {"not_signed_in", "expired"}:
            skipped[name] = f"Not signed in; run tag connector sign-in {name}."
        else:
            definitions[name] = {
                "command": node,
                "args": [str(bridge_script(store.root)), entry["url"],
                         "--auth-timeout", str(RUN_AUTH_TIMEOUT_SECONDS)],
                "env": {"MCP_REMOTE_CONFIG_DIR": str(auth_dir(store.root, name))}, "env_names": []}
    return definitions, skipped


def codex_run_args(workdir: Path) -> list[str]:
    """Codex -c definitions for the connectors this Tag may use. A server the
    Tag's own workspace defines is the Tag's choice and wins."""
    definitions, _skipped = run_plan()
    if not definitions:
        return []
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    try:
        from .tag_paths import toml_value
    except ImportError:
        from tag_paths import toml_value
    config = workdir / ".codex/config.toml"
    try:
        with config.open("rb") as handle:
            workspace = tomllib.load(handle).get("mcp_servers", {})
    except FileNotFoundError:
        workspace = {}
    except (OSError, ValueError):
        raise ConnectorError(f"Couldn't read {config} to add this Tag's connectors.") from None
    arguments = []
    for name, found in definitions.items():
        if name in workspace:
            continue
        definition = {"command": found["command"], "args": found["args"]}
        if found.get("env"):
            definition["env"] = found["env"]
            # mcp-remote connects before it answers; give slow servers time to start.
            definition["startup_timeout_sec"] = BRIDGE_STARTUP_SECONDS
        if found["env_names"]:
            # Names only: Codex reads the values from Tag's environment.
            definition["env_vars"] = found["env_names"]
        arguments += ["-c", f"mcp_servers.{name}={toml_value(definition)}"]
    return arguments


def claude_mcp_servers(workdir: Path) -> Path | dict | None:
    """The MCP servers for a Claude run: the workspace's .mcp.json plus the
    connectors this Tag may use. The workspace's own servers win on a name clash."""
    config = workdir / ".mcp.json"
    definitions, _skipped = run_plan()
    if not definitions:
        return config if config.is_file() else None
    try:
        workspace = json.loads(config.read_text(encoding="utf-8")).get("mcpServers", {})
        if not isinstance(workspace, dict):
            raise ValueError()
    except FileNotFoundError:
        workspace = {}
    except (OSError, ValueError, AttributeError):
        raise ConnectorError(f"Couldn't read {config} to add this Tag's connectors.") from None
    servers = {}
    for name, found in definitions.items():
        # Claude expands ${NAME} from Tag's environment when it starts the server.
        env = {**found.get("env", {}), **{env: "${" + env + "}" for env in found["env_names"]}}
        servers[name] = {"type": "stdio", "command": found["command"], "args": found["args"], "env": env}
    return {**servers, **workspace}


def bridge_dir(root: Path) -> Path:
    return root / "tools/mcp-remote"


def installed_bridge_version(root: Path) -> str | None:
    try:
        return json.loads((bridge_dir(root) / "node_modules/mcp-remote/package.json")
                          .read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def install_bridge(root: Path) -> None:
    """Install the pinned mcp-remote into Tag's folder from the shipped lockfile.
    npm ci checks every package against the lockfile's checksums, and no install
    scripts run."""
    npm = shutil.which("npm")
    if npm is None or shutil.which("node") is None:
        raise ConnectorError(NODE_MISSING)
    print(f"Installing mcp-remote {MCP_REMOTE_VERSION} for remote connectors...", file=sys.stderr)
    target = bridge_dir(root)
    target.mkdir(parents=True, exist_ok=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(Path(__file__).resolve().parent / "connectors" / name, target / name)
    try:
        done = subprocess.run([npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--omit=dev"],
                              cwd=target, capture_output=True, text=True, timeout=900, check=False)
    except subprocess.TimeoutExpired:
        raise ConnectorError("Installing mcp-remote took more than 15 minutes; check the network and retry.") from None
    if done.returncode or installed_bridge_version(root) != MCP_REMOTE_VERSION:
        detail = (done.stderr.strip().splitlines() or [""])[-1][:200]
        raise ConnectorError(f"Couldn't install mcp-remote {MCP_REMOTE_VERSION} with npm. {detail}".strip())


def ensure_bridge(root: Path) -> None:
    if installed_bridge_version(root) != MCP_REMOTE_VERSION:
        install_bridge(root)


def _bridge_env(root: Path, name: str) -> dict[str, str]:
    directory = auth_dir(root, name)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    restrict_windows_acl(directory)
    return {**os.environ, "MCP_REMOTE_CONFIG_DIR": str(directory)}


def sign_in_state(root: Path, name: str, entry: dict) -> str:
    """not_needed, not_signed_in, expired, signed_in or unknown, from mcp-remote's
    sign-in file alone. A sign-in revoked at the provider still reads signed_in;
    tag connector check finds those."""
    if entry.get("transport") != "http" or entry.get("sign_in") == "none":
        return "not_needed"
    try:
        tokens = json.loads(sign_in_file(root, name, entry["url"]).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "not_signed_in"
    except (OSError, ValueError, UnicodeError, KeyError):
        return "unknown"
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        return "unknown"
    expires_at = tokens.get("expires_at")  # milliseconds
    if isinstance(expires_at, (int, float)) and expires_at <= time.time() * 1000 and not tokens.get("refresh_token"):
        return "expired"
    return "signed_in"


def sign_in(root: Path, name: str, *, device_code: bool = False) -> str:
    """Run mcp-remote's sign-in once; both backends and every allowed Tag then use it."""
    entry = Store(root).entry(name)
    if entry.get("transport") != "http":
        raise ConnectorError(f"{name} runs on this computer and doesn't sign in.")
    ensure_bridge(root)
    command = [shutil.which("node") or "node", str(bridge_dir(root) / "node_modules/mcp-remote/dist/client.js"),
               entry["url"]]
    if device_code:
        command.append("--device-code")
    print(f"Signing in to {name}. Finish in the browser; if none opens, open the address shown below.",
          file=sys.stderr)
    try:
        # The sign-in's own output goes to stderr, so --json output stays clean.
        connected = subprocess.run(command, env=_bridge_env(root, name), stdin=subprocess.DEVNULL, stdout=2,
                                   timeout=SIGN_IN_TIMEOUT_SECONDS, check=False).returncode == 0
    except subprocess.TimeoutExpired:
        connected = False
    state = sign_in_state(root, name, entry)
    if connected and state == "not_signed_in":
        # It connected without asking anyone to sign in, so it needs none.
        with Store(root).change() as data:
            data["connectors"][name]["sign_in"] = "none"
        return "not_needed"
    if state != "signed_in":
        hint = "" if device_code else (" On a computer without a browser, try --device-code, or forward the "
                                       "sign-in port shown above over SSH.")
        raise ConnectorError(f"Signing in to {name} didn't finish.{hint}")
    return state


def check(root: Path, name: str) -> dict:
    """Connect for real and count the server's tools. Finds sign-ins the provider
    revoked, which the sign-in file can't show."""
    entry = Store(root).entry(name)
    state = sign_in_state(root, name, entry)
    if entry.get("transport") != "http" or state in {"not_signed_in", "expired"}:
        return {"state": state}
    node = shutil.which("node")
    if node is None or installed_bridge_version(root) != MCP_REMOTE_VERSION:
        return {"state": "bridge_missing"}
    command = [node, str(bridge_script(root)), entry["url"], "--auth-timeout", str(RUN_AUTH_TIMEOUT_SECONDS)]
    try:
        return {"state": "connected", "tools": _count_tools(command, _bridge_env(root, name))}
    except (OSError, ConnectionError, TimeoutError) as exc:
        return {"state": "failed", "detail": str(exc)[:200] or type(exc).__name__}


def _count_tools(command: list[str], env: dict[str, str]) -> int:
    """Speak just enough MCP over stdio to list the server's tools."""
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               env=env, text=True, start_new_session=True)
    lines: queue.Queue = queue.Queue()

    def read() -> None:
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=read, daemon=True).start()
    deadline = time.monotonic() + CHECK_TIMEOUT_SECONDS

    def send(message: dict) -> None:
        process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        process.stdin.flush()

    def reply(request_id: int) -> dict:
        while True:
            try:
                line = lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                raise TimeoutError(f"No answer within {CHECK_TIMEOUT_SECONDS} seconds.") from None
            if line is None:
                raise ConnectionError("The connector closed before answering.")
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict) and message.get("id") == request_id:
                if "error" in message:
                    raise ConnectionError(str((message["error"] or {}).get("message", "")))
                return message.get("result") or {}

    try:
        send({"id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "tag-connector-check", "version": "1"}}})
        reply(1)
        send({"method": "notifications/initialized"})
        send({"id": 2, "method": "tools/list", "params": {}})
        return len(reply(2).get("tools") or [])
    finally:
        try:
            os.killpg(process.pid, 15)
        except (OSError, AttributeError):
            process.kill()
        process.wait(timeout=10)


def view(name: str, entry: dict, sign_in: str | None = None) -> dict:
    """The public shape of one connector, for --json and Tag.app."""
    row = {"name": name, "transport": entry.get("transport"),
           "allowed_tags": [row["tag"] for row in entry.get("allowed", []) if isinstance(row.get("tag"), str)],
           "added_at": entry.get("added_at")}
    if entry.get("transport") == "http":
        row["url"] = entry.get("url")
    else:
        row.update(command=entry.get("command"), args=entry.get("args", []), env=entry.get("env", []))
    if sign_in is not None:
        row["sign_in"] = sign_in
    return row


def resolve_tags(root: Path, references: list[str]) -> list[str]:
    """Turn Tag IDs or nicknames into IDs, refusing Tags that don't exist."""
    tags = []
    for reference in references:
        tag_id = tag_instances.resolve_reference(root, reference)
        try:
            tag_instances.resolve(root, tag_id)
        except ValueError:
            raise ConnectorError(f"No Tag named '{reference}'. See tag list.") from None
        tags.append(tag_id)
    return sorted(set(tags))


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        # Report through ConnectorError so --json callers get a JSON error, not usage text.
        raise ConnectorError(f"{message}. Usage: {USAGE}")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="tag connector", description="Manage the MCP connectors Tags may use.",
                                     usage=USAGE)
    parser.add_argument("action", choices=("list", "show", "add", "remove", "allow", "deny", "sign-in", "check"),
                        nargs="?", default="list")
    parser.add_argument("name", nargs="?")
    parser.add_argument("tags", nargs="*", help="allow, deny: Tag names or nicknames")
    parser.add_argument("--url", help="add: the remote connector's address")
    parser.add_argument("--command", help="add: the program that runs a local connector, with its arguments")
    parser.add_argument("--env", action="append", default=[], metavar="NAME",
                        help="add --command: an environment variable the connector reads (name only; repeatable)")
    parser.add_argument("--device-code", action="store_true",
                        help="sign-in: show a code to enter on another device, for computers without a browser")
    parser.add_argument("--json", action="store_true", dest="json_output", help="structured output")
    return parser


SIGN_IN_TEXT = {"not_needed": "no sign-in needed", "not_signed_in": "not signed in",
                "expired": "sign-in expired", "signed_in": "signed in", "unknown": "sign-in unknown"}


def _print_row(row: dict) -> None:
    target = row.get("url") or shlex.join([row.get("command") or "", *row.get("args", [])])
    tags = ", ".join(row["allowed_tags"]) or "no Tags yet"
    state = SIGN_IN_TEXT.get(row.get("sign_in", ""), "")
    print(f"{row['name']}  {target}  · allowed for {tags}" + (f"  · {state}" if state else ""))


def cli(arguments: list[str], *, root: Path | None = None) -> int:
    args = _parser().parse_args(arguments)
    root = (root or tag_home()).expanduser().absolute()
    store = Store(root)
    if args.action == "list":
        if args.name or args.tags:
            raise ConnectorError("Use tag connector list without arguments.")
        rows = store.list()
        if args.json_output:
            print(json.dumps({"schema_version": SCHEMA_VERSION, "connectors": rows}, indent=2))
        elif not rows:
            print("No connectors yet. Add one: tag connector add NAME --url URL")
        else:
            for row in rows:
                _print_row(row)
        return 0
    if not args.name:
        raise ConnectorError(f"tag connector {args.action} needs a connector name.")
    if args.action in {"allow", "deny"}:
        if not args.tags:
            raise ConnectorError(f"Name the Tags: tag connector {args.action} {args.name} TAG...")
    elif args.tags:
        raise ConnectorError(f"Unexpected arguments: {' '.join(args.tags)}")
    if (args.url or args.command or args.env) and args.action != "add":
        raise ConnectorError("--url, --command and --env are only for tag connector add.")
    if args.device_code and args.action != "sign-in":
        raise ConnectorError("--device-code is only for tag connector sign-in.")
    message = ""
    if args.action == "show":
        row = store.get(args.name)
    elif args.action == "sign-in":
        sign_in(root, args.name, device_code=args.device_code)
        row = store.get(args.name)
        message = (f"Signed in to {args.name}. Claude and Codex runs of every allowed Tag use this sign-in."
                   if row["sign_in"] == "signed_in" else f"{args.name} connected without a sign-in; none is needed.")
    elif args.action == "check":
        found = check(root, args.name)
        row = {**store.get(args.name), "check": found}
        message = {"connected": f"{args.name} is connected ({found.get('tools')} tools).",
                   "failed": f"{args.name} didn't connect: {str(found.get('detail')).rstrip('.')}. "
                             f"If it keeps failing, sign in again: tag connector sign-in {args.name}.",
                   "bridge_missing": "Node or mcp-remote isn't installed; see tag doctor.",
                   "not_needed": f"{args.name} runs on this computer; it's started with each run.",
                   }.get(found["state"], f"{args.name}: {SIGN_IN_TEXT.get(found['state'], found['state'])}. "
                                         f"Sign in with tag connector sign-in {args.name}.")
    elif args.action == "add":
        command = shlex.split(args.command) if args.command else None
        if args.command is not None and not command:
            raise ConnectorError("--command needs a program to run.")
        row = store.add(args.name, url=args.url, command=command, env=args.env)
        message = (f"Added {args.name}. No Tag can use it yet; "
                   f"allow one with tag connector allow {args.name} TAG.")
        if args.url:
            message += f" Then sign in once: tag connector sign-in {args.name}."
    elif args.action == "remove":
        row = store.remove(args.name)
        message = f"Removed {args.name} and its sign-in on this computer."
    else:
        tags = resolve_tags(root, args.tags)
        row = store.set_allowed(args.name, tags, args.action == "allow")
        verb = "can now" if args.action == "allow" else "can no longer"
        message = f"{', '.join(tags)} {verb} use {args.name} in new requests."
    ok = args.action != "check" or row["check"]["state"] in {"connected", "not_needed"}
    if args.json_output:
        result = {"schema_version": SCHEMA_VERSION, "ok": ok, "connector": row}
        if message:
            result["message"] = message
        print(json.dumps(result, indent=2))
    else:
        if message:
            print(message)
        if args.action != "remove":
            _print_row(row)
    return 0 if ok else 1


def doctor_checks(root: Path | None = None) -> list[tuple[bool, str, str, str | None]]:
    """(ok, label, detail, next action) for tag doctor: Node and the pinned
    mcp-remote when a remote connector is in use, and each in-use connector's
    sign-in. Connectors no Tag may use aren't checked."""
    store = Store(root)
    entries = {name: entry for name, entry in store.read()["connectors"].items() if entry.get("allowed")}
    remote = {name: entry for name, entry in entries.items() if entry.get("transport") == "http"}
    if not remote:
        return []
    results = []
    node = shutil.which("node") is not None
    results.append((node, "Node.js for remote connectors", "found" if node else "missing",
                    None if node else NODE_MISSING))
    version = installed_bridge_version(store.root)
    results.append((version == MCP_REMOTE_VERSION, "mcp-remote",
                    f"{version or 'not installed'} (Tag uses {MCP_REMOTE_VERSION})",
                    f"Run tag connector sign-in {next(iter(sorted(remote)))} to install it."))
    for name, entry in sorted(remote.items()):
        state = sign_in_state(store.root, name, entry)
        results.append((state in {"signed_in", "not_needed"}, f"connector {name}", SIGN_IN_TEXT[state],
                        f"Run tag connector sign-in {name}."))
    return results


if __name__ == "__main__":
    try:
        raise SystemExit(cli(sys.argv[1:]))
    except ConnectorError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
