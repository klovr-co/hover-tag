"""Who a Tag works with: Tags and apps that can give it tasks.

A Tag on the list may also be asked for help (``OPENTAG_PEER_TAGS``). An app's
bot may only ask this Tag to run a skill (``SLACK_ALLOWED_BOT_IDS``). Both
settings stay the source of truth; this module reads and writes them as one
list, and offers candidates so nobody has to look up member IDs. Nothing is
ever added without the owner choosing it.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

try:
    import slack_channels
    import slack_profile_icon
    import tag_config as settings
    import tag_handoff
    import tag_instances
except ImportError:  # pragma: no cover - package import path
    from scripts import slack_channels, slack_profile_icon, tag_config as settings, tag_handoff, tag_instances

KINDS = ("tag", "app")
NAMES = "state/works-with-names.json"
SLACKBOT = "USLACKBOT"
MEMBER_ID = re.compile(r"[UW][A-Z0-9]+")
BOT_OR_MEMBER_ID = re.compile(r"[BUW][A-Z0-9]+")
NAME_LIMIT = 80
USERS_PAGES = 20  # 200 per page: enough for any workspace's bots, bounded for huge ones.

Api = Callable[[str, str, dict[str, str]], dict[str, Any]]


def _ids(raw: str) -> list[str]:
    seen: list[str] = []
    for item in raw.split(","):
        if item.strip() and item.strip() not in seen:
            seen.append(item.strip())
    return seen


def _clean_name(name: str) -> str:
    # A comma or "=" would break OPENTAG_PEER_TAGS's Name=MEMBERID list.
    return re.sub(r"\s+", " ", re.sub(r"[,=]", " ", name)).strip()[:NAME_LIMIT].strip()


def _names(home: Path) -> dict[str, str]:
    try:
        value = json.loads((home / NAMES).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    names = value.get("names") if isinstance(value, dict) else None
    return {k: v for k, v in names.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(names, dict) else {}


def _remember(home: Path, found: dict[str, str]) -> None:
    """Keep display names for listed apps so showing the list never needs Slack."""
    names = {**_names(home), **{k: v for k, v in found.items() if v}}
    target = home / NAMES
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps({"version": 1, "names": names}, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def current(home: Path, values: dict[str, str]) -> dict[str, Any]:
    """The saved list, Tags first. A malformed setting is reported, never repaired."""
    names, entries, problems = _names(home), [], []
    try:
        peers = tag_handoff.parse_peers(values.get("OPENTAG_PEER_TAGS", ""))
    except ValueError as exc:
        peers = []
        problems.append(f"OPENTAG_PEER_TAGS: {exc}")
    entries += [{"id": peer.user_id, "name": peer.name, "kind": "tag"} for peer in peers]
    bots = values.get("SLACK_ALLOWED_BOT_IDS", "")
    if error := settings.validation_error("SLACK_ALLOWED_BOT_IDS", bots):
        problems.append(f"SLACK_ALLOWED_BOT_IDS: {error}")
    else:
        tag_ids = {entry["id"] for entry in entries}
        entries += [{"id": item, "name": names.get(item), "kind": "app"} for item in _ids(bots) if item not in tag_ids]
    return {"works_with": entries, "error": "; ".join(problems) or None}


def local_tags(installation_root: Path, home: Path, values: dict[str, str]) -> list[dict[str, Any]]:
    """The owner's other Tags in the same Slack workspace, from files on this computer.

    A Tag's bot member ID comes from its saved Slack picture record, which every
    started Tag keeps; a Tag that never reached Slack has none and is skipped.
    """
    team = values.get("SLACK_TEAM_ID", "")
    found = []
    for row in tag_instances.discover(installation_root):
        other = Path(str(row.get("home", "")))
        if not row.get("valid") or not team or other.resolve() == home.resolve():
            continue
        try:
            other_values = settings.load_config(other / "config/settings.json")
        except (OSError, ValueError):
            continue
        record = slack_profile_icon._record(other)
        user_id = record.get("user_id")
        if other_values.get("SLACK_TEAM_ID") != team or not isinstance(user_id, str) or not MEMBER_ID.fullmatch(user_id):
            continue
        name = _clean_name(other_values.get("OPENTAG_BOT_NAME", "")) or str(row["id"])
        found.append({"id": user_id, "name": name, "kind": "tag", "tag": str(row["id"])})
    return sorted(found, key=lambda item: item["name"].casefold())


def workspace_bots(values: dict[str, str], *, api: Api | None = None) -> list[dict[str, Any]]:
    """Other apps' bots that Slack lists in the workspace (users:read, already granted)."""
    api = api or slack_channels.slack_api
    token, team = values.get("SLACK_BOT_TOKEN", ""), values.get("SLACK_TEAM_ID", "")
    if not token:
        raise RuntimeError("Connect the Tag to Slack first")
    own = api(token, "auth.test", {}).get("user_id")
    bots, cursor = [], ""
    for _ in range(USERS_PAGES):
        parameters = {"limit": "200", **({"team_id": team} if team else {}), **({"cursor": cursor} if cursor else {})}
        payload = api(token, "users.list", parameters)
        for user in payload.get("members") or []:
            if not isinstance(user, dict) or not user.get("is_bot") or user.get("deleted"):
                continue
            user_id = user.get("id")
            if user_id in {SLACKBOT, own} or not isinstance(user_id, str) or not MEMBER_ID.fullmatch(user_id):
                continue
            profile = user.get("profile") if isinstance(user.get("profile"), dict) else {}
            name = _clean_name(str(profile.get("real_name") or user.get("real_name") or user.get("name") or user_id))
            bots.append({"id": user_id, "name": name or user_id, "kind": "app"})
        cursor = str((payload.get("response_metadata") or {}).get("next_cursor") or "")
        if not cursor:
            break
    return sorted(bots, key=lambda item: item["name"].casefold())


def candidates(installation_root: Path, home: Path, values: dict[str, str], *, api: Api | None = None) -> dict[str, Any]:
    """Everyone the owner could pick. Slack being unreachable still leaves the local Tags."""
    tags = local_tags(installation_root, home, values)
    error = None
    try:
        apps = workspace_bots(values, api=api)
    except (RuntimeError, slack_channels.SlackChannelError, OSError) as exc:
        apps, error = [], f"Couldn't list the workspace's apps: {exc}. Paste a member ID instead."
    local = {item["id"] for item in tags}
    apps = [item for item in apps if item["id"] not in local]
    _remember(home, {item["id"]: item["name"] for item in tags + apps})
    return {"candidates": tags + apps, "slack_error": error}


def _kind_for(item_id: str, kind: str | None, entries: list[dict[str, Any]], tags: list[dict[str, Any]]) -> str:
    if kind:
        return kind
    if any(entry["id"] == item_id for entry in entries):
        return next(entry["kind"] for entry in entries if entry["id"] == item_id)
    return "tag" if any(tag["id"] == item_id for tag in tags) else "app"


def changes(entries: list[dict[str, Any]]) -> dict[str, str]:
    """The two settings for a list. Tag names are made unique, as handoffs require."""
    peers, bots, used = [], [], set()
    for entry in entries:
        if entry["kind"] == "tag":
            if not MEMBER_ID.fullmatch(entry["id"]):
                raise ValueError(f"{entry['id']}: a Tag needs its bot member ID (U… or W…)")
            base = _clean_name(entry.get("name") or "") or entry["id"]
            name, number = base, 2
            while name.casefold() in used:
                name, number = f"{base} {number}", number + 1
            used.add(name.casefold())
            peers.append(f"{name}={entry['id']}")
        else:
            if not BOT_OR_MEMBER_ID.fullmatch(entry["id"]):
                raise ValueError(f"{entry['id']}: use a bot ID (B…) or member ID (U… or W…)")
            bots.append(entry["id"])
    return {"OPENTAG_PEER_TAGS": ",".join(peers), "SLACK_ALLOWED_BOT_IDS": ",".join(bots)}


def edit(installation_root: Path, home: Path, action: str, ids: list[str], *, kind: str | None = None,
         name: str | None = None) -> dict[str, Any]:
    """Apply add, remove, or set (replace the list) and save both settings together."""
    path = settings.config_path(home)
    values = settings.load_config(path)
    saved = current(home, values)
    if saved["error"]:
        raise ValueError(f"{saved['error']}. Fix it with tag config set, or clear it, first")
    entries = [dict(entry) for entry in saved["works_with"]]
    ids = [item.strip().removeprefix("@") for item in ids if item.strip()]
    if any(not BOT_OR_MEMBER_ID.fullmatch(item) for item in ids):
        raise ValueError("Use Slack member IDs (U… or W…) or bot IDs (B…)")
    if action in {"add", "remove"} and len(ids) != 1:
        raise ValueError(f"works-with {action} needs one ID")
    if name is not None and action != "add":
        raise ValueError("--name is only for works-with add")
    if kind and action == "remove":
        raise ValueError("--as is only for works-with add and set")
    tags = local_tags(installation_root, home, values)
    known = {**_names(home), **{tag["id"]: tag["name"] for tag in tags}}
    if action == "remove":
        if not any(entry["id"] == ids[0] for entry in entries):
            raise ValueError(f"{ids[0]} isn't on the list")
        entries = [entry for entry in entries if entry["id"] != ids[0]]
    elif action == "add":
        entries = [entry for entry in entries if entry["id"] != ids[0]] + [{
            "id": ids[0], "kind": _kind_for(ids[0], kind, entries, tags),
            "name": _clean_name(name or "") or next((e["name"] for e in entries if e["id"] == ids[0] and e["name"]), None)
            or known.get(ids[0]),
        }]
    elif action == "set":
        previous = {entry["id"]: entry for entry in entries}
        entries = [{"id": item, "kind": _kind_for(item, kind, entries, tags),
                    "name": (previous.get(item) or {}).get("name") or known.get(item)}
                   for item in dict.fromkeys(ids)]
    else:
        raise ValueError("Use works-with, works-with candidates, add ID, remove ID, or set [ID ...]")
    settings.update_config(path, changes(entries))
    if name:
        _remember(home, {ids[0]: _clean_name(name)})
    return current(home, settings.load_config(path))
