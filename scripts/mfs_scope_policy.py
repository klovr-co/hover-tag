"""Shared scope enforcement for MFS helper commands."""

from __future__ import annotations

import posixpath
import urllib.parse
from dataclasses import dataclass


@dataclass(frozen=True)
class MfsUri:
    scheme: str
    authority: str
    path: str


def parse_scopes(raw: str) -> list[str]:
    return [scope.strip() for scope in raw.split(",") if scope.strip()]


def canonical_uri(value: str) -> MfsUri | None:
    """Return comparable URI components, rejecting ambiguous or unsafe paths."""
    parsed = urllib.parse.urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        not parsed.scheme
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
    ):
        return None

    decoded_path = parsed.path
    while True:
        next_path = urllib.parse.unquote(decoded_path)
        if next_path == decoded_path:
            break
        decoded_path = next_path

    if ".." in decoded_path.split("/"):
        return None

    normalized_path = posixpath.normpath(decoded_path or "/")
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"
    normalized_path = normalized_path.rstrip("/") or "/"
    return MfsUri(
        scheme=parsed.scheme.lower(),
        authority=parsed.netloc.lower(),
        path=normalized_path,
    )


def is_path_allowed(path: str, allowed_scopes: list[str]) -> bool:
    target = canonical_uri(path)
    if target is None:
        return False
    if "--all" in allowed_scopes:
        return True

    for allowed in allowed_scopes:
        scope = canonical_uri(allowed)
        if scope is None or (target.scheme, target.authority) != (
            scope.scheme,
            scope.authority,
        ):
            continue
        if target.path == scope.path or (
            scope.path == "/" or target.path.startswith(f"{scope.path}/")
        ):
            return True
    return False


def is_scope_allowed(scope: str, allowed_scopes: list[str]) -> bool:
    if scope == "--all":
        return "--all" in allowed_scopes
    return is_path_allowed(scope, allowed_scopes)
