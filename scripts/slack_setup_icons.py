"""Best-effort setup pictures using an existing Tag in the selected workspace.

Slack CLI sign-ins cannot authorize team.info/users.info before an app exists.
Never read the CLI's private credentials or install an app to obtain pictures.
Older installations populate this versioned cosmetic cache on their next recap.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

try:
    from . import slack_channels, slack_identity, slack_profile_icon, slack_workspace_icon, tag_config, tag_instances
    from .tag_locks import LifecycleLock
except ImportError:
    import slack_channels, slack_identity, slack_profile_icon, slack_workspace_icon, tag_config, tag_instances
    from tag_locks import LifecycleLock


VERSION = 1
TTL = 3600


def pictures(root: Path, home: Path, values: dict[str, str], *, api=None, fetch=None) -> dict[str, str | None]:
    """Return local paths only. Missing grants, network and disk failures are cosmetic."""
    result = {"workspace": None, "owner": None}
    team = values.get("SLACK_TEAM_ID", "")
    owner = values.get("SLACK_ALLOWED_USER_IDS", "").split(",")[0]
    if not re.fullmatch(r"T[A-Z0-9]+", team) or not re.fullmatch(r"[UW][A-Z0-9]+", owner):
        return result
    api = api or slack_channels.slack_api
    fetch = fetch or slack_workspace_icon.download
    try:
        with LifecycleLock(home / "state/setup-icons.lock"):
            return _pictures(root, home, values, team, owner, api, fetch, result)
    except (OSError, ValueError, RuntimeError):
        return result


def _pictures(root, home, values, team, owner, api, fetch, result):
    # Identity-specific records cannot display another workspace or owner's photo.
    record_path = home / "state" / f"setup-icons-v{VERSION}-{team}-{owner}.json"
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = {}
    if not isinstance(record, dict) or record.get("version") != VERSION:
        record = {"version": VERSION}
    pending = []
    for kind in result:
        entry = record.get(kind)
        entry = entry if isinstance(entry, dict) else {}
        name = entry.get("file")
        verified = name is None and entry.get("default") is True
        if isinstance(name, str) and re.fullmatch(r"setup-icon-[a-f0-9]{64}\.(png|jpg|gif|webp)", name):
            path = home / "state" / name
            try:
                if path.is_symlink():
                    raise ValueError("Unexpected image link")
                data = path.read_bytes()
                slack_profile_icon._validate(data, path.suffix)
                verified = name == f"setup-icon-{hashlib.sha256(data).hexdigest()}{path.suffix}"
                if verified:
                    result[kind] = str(path)
            except (OSError, ValueError, RuntimeError):
                pass
        checked = entry.get("checked")
        if not (verified and isinstance(checked, (int, float)) and 0 <= time.time() - checked < TTL):
            pending.append(kind)
    if not pending:
        return result

    def candidates():
        yield values
        for item in tag_instances.discover(root):
            if item["valid"]:
                try:
                    context = tag_instances.resolve(root, str(item["id"]))
                    if context.home != home:
                        yield tag_config.read_config(context.home / "config/settings.json")
                except (OSError, ValueError, RuntimeError):
                    continue

    seen = set()
    for candidate in candidates():
        token = candidate.get("SLACK_BOT_TOKEN", "")
        if not token or token in seen or candidate.get("SLACK_TEAM_ID") != team:
            continue
        seen.add(token)
        try:
            slack_identity.validate(token, team_id=team, app_id=candidate.get("SLACK_APP_ID", ""),
                                    enterprise_id=candidate.get("SLACK_ENTERPRISE_ID", ""),
                                    label="Setup pictures", api=api)
        except (OSError, ValueError, RuntimeError):
            continue
        for kind in pending[:]:
            try:
                if kind == "workspace":
                    entity = api(token, "team.info", {"team": team}).get("team")
                    if not isinstance(entity, dict) or entity.get("id") != team:
                        continue
                    profile, sizes = entity.get("icon"), slack_workspace_icon.SIZES
                else:
                    entity = api(token, "users.info", {"user": owner, "team_id": team}).get("user")
                    if not isinstance(entity, dict) or entity.get("id") != owner:
                        continue
                    profile, sizes = entity.get("profile"), slack_profile_icon.SIZES
                if not isinstance(profile, dict):
                    continue
                default = kind == "workspace" and profile.get("image_default") is True
                url = next((profile[size] for size in sizes if isinstance(profile.get(size), str) and profile[size]), "")
                if not default and not url:
                    continue
                name = None
                if not default:
                    data, suffix = fetch(url)
                    slack_profile_icon._validate(data, suffix)
                    name = f"setup-icon-{hashlib.sha256(data).hexdigest()}{suffix}"
                    path = home / "state" / name
                    slack_workspace_icon._write(path, data)
                    if path.read_bytes() != data:
                        raise RuntimeError("Saved picture could not be verified")
                updated = {**record, kind: {"file": name, "default": default, "checked": time.time()}}
                slack_workspace_icon._write(record_path, (json.dumps(updated) + "\n").encode())
                record = updated
                result[kind] = str(home / "state" / name) if name else None
                pending.remove(kind)
            except (OSError, ValueError, RuntimeError):
                continue
        if not pending:
            break
    return result
