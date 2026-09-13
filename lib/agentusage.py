"""Shared helpers for `omarchy-agent-usage-<agent>` collectors.

Every collector prints exactly one JSON record on stdout. The Omarchy agents
panel (and any renderer that watches ~/.local/state/omarchy/agents/usage/)
reads only that record: see /usr/share/omarchy/shell/plugins/agents/README.md.

Record contract (the fields consumers actually read):
    schemaVersion, id, name, updatedAt, ready, tierLabel,
    usageStatusText, authHelpText,
    limits[] = {title, percent (0.0-1.0), resetsAt (ISO8601), used, allowance}
    balance  = {remaining, funded, spent, currency, estimated}  # prepaid plans
A failed scan still prints a record: ready=false plus a human-readable
usageStatusText/authHelpText, so the panel can say why instead of vanishing.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_TIMEOUT = 15


class CollectorError(Exception):
    """A scan failure with a message fit for the panel."""


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def epoch_ms_iso(value: Any) -> str:
    """Millisecond epoch -> ISO8601. Returns "" for anything unusable."""
    try:
        ms = float(value)
    except (TypeError, ValueError):
        return ""
    if ms <= 0:
        return ""
    return (
        dt.datetime.fromtimestamp(ms / 1000.0, dt.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def iso(value: Any) -> str:
    """Normalize an ISO timestamp to UTC Z form. Returns "" when unparseable."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def fraction(used: Any, allowance: Any) -> float:
    """used/allowance clamped to 0.0-1.0. The panel wants a fraction, not %."""
    try:
        used_value = float(used)
        allowance_value = float(allowance)
    except (TypeError, ValueError):
        return 0.0
    if allowance_value <= 0:
        return 0.0
    return max(0.0, min(1.0, used_value / allowance_value))


def percent_fraction(percent: Any) -> float:
    """A 0-100 percentage from a provider -> 0.0-1.0 fraction."""
    try:
        value = float(percent)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, value / 100.0))


def limit(
    title: str,
    percent: float,
    resets_at: str = "",
    used: Any = None,
    allowance: Any = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {"title": title, "percent": round(percent, 6)}
    if resets_at:
        entry["resetsAt"] = resets_at
    if used is not None:
        entry["used"] = used
    if allowance is not None:
        entry["allowance"] = allowance
    return entry


def record(
    agent_id: str,
    name: str,
    ready: bool = False,
    limits: list[dict[str, Any]] | None = None,
    tier_label: str = "",
    status_text: str = "",
    auth_help: str = "",
    balance: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build one panel record. Account-scoped: never summed across devices."""
    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "id": agent_id,
        "name": name,
        "updatedAt": now_iso(),
        "ready": ready,
        "scope": "account",
        "hasLocalStats": False,
        "hasPromptStats": False,
        "tierLabel": tier_label,
        "usageStatusText": status_text,
        "authHelpText": auth_help,
        "limits": limits or [],
    }
    if balance is not None:
        payload["balance"] = balance
    payload.update(extra)
    return payload


def emit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, separators=(",", ":")))
    return 0


def run(agent_id: str, name: str, scan, auth_help: str = "") -> int:
    """Call `scan()`, print its record, and turn any failure into a record.

    A collector that raised would leave the panel with a stale file and no
    explanation, so every exit path here prints valid JSON.
    """
    try:
        return emit(scan())
    except CollectorError as error:
        return emit(
            record(
                agent_id,
                name,
                status_text=f"{name} unavailable",
                auth_help=str(error) or auth_help,
            )
        )
    except Exception:
        return emit(
            record(
                agent_id,
                name,
                status_text=f"{name} unavailable",
                auth_help=auth_help or f"{name} usage scan failed",
            )
        )


# --- credentials ------------------------------------------------------------


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))


def agent_config(agent_id: str) -> dict[str, Any]:
    """~/.config/omarchy/agents/<id>.json — the per-agent override file."""
    path = _config_home() / "omarchy" / "agents" / f"{agent_id}.json"
    try:
        parsed = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def codexbar_key(provider_id: str) -> str:
    """Reuse a key already stored in CodexBar's config, when present.

    CodexBar keeps one entry per provider in ~/.config/codexbar/config.json.
    Reading it means a machine that already tracks a provider there needs no
    second copy of the same secret.
    """
    path = _config_home() / "codexbar" / "config.json"
    try:
        parsed = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return ""
    providers = parsed.get("providers") if isinstance(parsed, dict) else None
    if not isinstance(providers, list):
        return ""
    for entry in providers:
        if isinstance(entry, dict) and entry.get("id") == provider_id:
            return str(entry.get("apiKey") or "").strip()
    return ""


def read_file_key(path: str | Path) -> str:
    try:
        return Path(path).expanduser().read_text().strip()
    except OSError:
        return ""


def resolve_key(
    agent_id: str,
    env_vars: tuple[str, ...] = (),
    codexbar_id: str = "",
    files: tuple[str, ...] = (),
) -> str:
    """First hit wins: env var, agent config file, CodexBar config, key file."""
    for name in env_vars:
        value = str(os.environ.get(name, "")).strip()
        if value:
            return value
    configured = str(agent_config(agent_id).get("apiKey") or "").strip()
    if configured:
        return configured
    if codexbar_id:
        shared = codexbar_key(codexbar_id)
        if shared:
            return shared
    for candidate in files:
        value = read_file_key(candidate)
        if value:
            return value
    return ""


# --- http -------------------------------------------------------------------


def get_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    label: str = "provider",
) -> Any:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise CollectorError(f"{label} rejected the credential") from error
        if error.code == 429:
            raise CollectorError(f"{label} rate-limited the usage request") from error
        raise CollectorError(f"{label} returned HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise CollectorError(f"Could not reach {label}") from error
    except TimeoutError as error:
        raise CollectorError(f"{label} timed out") from error
    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise CollectorError(f"{label} returned invalid JSON") from error
