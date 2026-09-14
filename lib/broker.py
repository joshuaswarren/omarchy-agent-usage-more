"""Read subscription usage from an omp auth-broker.

The broker (`omp auth-broker serve`) already holds every OAuth/API credential
omp uses and exposes `GET /v1/usage` — an aggregate `UsageReport[]` across all
of them. That makes it the one source that can report providers whose quota is
otherwise locked behind a browser session, because the broker refreshed the
credential server-side already. See `omp://auth-broker-gateway.md`.

Collectors here turn one broker report into one panel record. Nothing is
written back; the broker is read-only from this side.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import agentusage as au

USAGE_PATH = "/v1/usage"
SNAPSHOT_PATH = "/v1/snapshot"
CLI_TIMEOUT = 20


def _config_yml_value(key: str) -> str:
    """Pull `auth.broker.<key>` out of ~/.omp/agent/config.yml.

    A flat scan beats a YAML dependency: the file is machine-written, and the
    two keys we want are unique leaf names under a single `broker:` block.
    """
    path = Path(
        os.environ.get("OMP_AGENT_DIR") or (Path.home() / ".omp" / "agent")
    ) / "config.yml"
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return ""
    in_broker = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("broker:"):
            in_broker = True
            continue
        if in_broker:
            if stripped and not line.startswith((" ", "\t")):
                break
            if stripped.startswith(f"{key}:"):
                return _resolve(stripped.split(":", 1)[1].strip().strip("\"'"))
    return ""


def _resolve(value: str) -> str:
    """Expand omp's `!command` config indirection.

    omp lets a config value be `!some-command`, meaning "run this and use its
    output" — which is how the broker URL and token stay out of the file.
    """
    if not value.startswith("!"):
        return value
    command = value[1:].strip()
    if not command:
        return ""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _dict(value: Any) -> dict[str, Any]:
    """Defensive narrowing: broker JSON fields are only trusted as objects."""
    return value if isinstance(value, dict) else {}



def _cli(*args: str) -> str:
    try:
        result = subprocess.run(
            ["omp", "auth-broker", *args],
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def endpoint() -> tuple[str, str]:
    """(url, token) for the broker, or ("", "") when it is not configured."""
    url = os.environ.get("OMP_AUTH_BROKER_URL", "").strip() or _config_yml_value("url")
    token = os.environ.get("OMP_AUTH_BROKER_TOKEN", "").strip() or _config_yml_value("token")
    if not url:
        # `status --json` reports the configured broker and its health.
        raw = _cli("status", "--json")
        try:
            url = str(json.loads(raw).get("url") or "").strip()
        except (json.JSONDecodeError, AttributeError):
            url = ""
    if not token:
        token = _cli("token")
    return url.rstrip("/"), token


def _get(path: str) -> Any:
    url, token = endpoint()
    if not url or not token:
        raise au.CollectorError(
            "No omp auth-broker configured (set OMP_AUTH_BROKER_URL/TOKEN or auth.broker in ~/.omp/agent/config.yml)"
        )
    request = urllib.request.Request(
        url + path,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=au.DEFAULT_TIMEOUT) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise au.CollectorError("The auth broker rejected its bearer token") from error
        raise au.CollectorError(f"The auth broker returned HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise au.CollectorError(f"Could not reach the auth broker at {url}") from error
    except json.JSONDecodeError as error:
        raise au.CollectorError("The auth broker returned invalid JSON") from error


def fetch_reports() -> list[dict[str, Any]]:
    payload = _get(USAGE_PATH)
    reports = payload.get("reports") if isinstance(payload, dict) else None
    return [report for report in (reports or []) if isinstance(report, dict)]


def stored_credentials(provider: str) -> int:
    """How many credentials the broker holds for a provider.

    A provider can have a credential and still produce no usage report — the
    broker only publishes what a provider's usage probe can actually fetch.
    Counting reports alone would tell the user to log in again when the
    credential is fine and the provider simply exposes no quota API, so the
    snapshot is the authority for "is it connected".

    Only provider ids are read here; the snapshot's token material is ignored.
    """
    try:
        payload = _get(SNAPSHOT_PATH)
    except au.CollectorError:
        return 0
    rows = payload.get("credentials") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return 0
    return sum(
        1
        for row in rows
        if isinstance(row, dict)
        and row.get("provider") == provider
        and not row.get("disabled")
    )


def _fraction(amount: dict[str, Any]) -> float | None:
    """Broker amounts carry usedFraction; fall back to used/limit."""
    value = amount.get("usedFraction")
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    remaining = amount.get("remainingFraction")
    if isinstance(remaining, (int, float)):
        return max(0.0, min(1.0, 1.0 - float(remaining)))
    used, limit = amount.get("used"), amount.get("limit")
    if isinstance(used, (int, float)) and isinstance(limit, (int, float)) and limit > 0:
        return au.fraction(used, limit)
    return None


def _title(entry: dict[str, Any]) -> str:
    label = str(entry.get("label") or "").strip()
    window = _dict(entry.get("window"))
    window_label = str(window.get("label") or "").strip()
    if label and window_label and window_label.lower() not in label.lower():
        return f"{label} ({window_label})"
    return label or window_label or "Limit"


def limits_for(reports: list[dict[str, Any]], provider: str) -> list[dict[str, Any]]:
    """Merge every account's report for one provider into one limit list.

    The broker returns one report per credential, so a provider with two
    accounts appears twice. The actionable number is the account with the most
    headroom, because that is the one the broker rotates to: taking the
    fullest window instead would read "out of quota" while a sibling account
    still had most of its allowance.
    """
    merged: dict[str, dict[str, Any]] = {}
    for report in reports:
        if report.get("provider") != provider:
            continue
        for entry in report.get("limits") or []:
            if not isinstance(entry, dict):
                continue
            amount = _dict(entry.get("amount"))
            fraction = _fraction(amount)
            if fraction is None:
                continue
            title = _title(entry)
            window = _dict(entry.get("window"))
            candidate = au.limit(
                title,
                fraction,
                au.epoch_ms_iso(window.get("resetsAt")),
                used=amount.get("used"),
                allowance=amount.get("limit"),
            )
            current = merged.get(title)
            if current is None or candidate["percent"] < current["percent"]:
                merged[title] = candidate
    return list(merged.values())


def accounts_for(reports: list[dict[str, Any]], provider: str) -> int:
    return sum(1 for report in reports if report.get("provider") == provider)


def account_label(report: dict[str, Any]) -> str:
    """A short, stable name for the credential behind one report.

    The broker stamps `metadata.email` for account-scoped providers, which is
    what distinguishes two subscriptions on the same provider. The local part
    is enough to tell them apart on a bar and keeps the address off screen.
    """
    metadata = _dict(report.get("metadata"))
    email = str(metadata.get("email") or "").strip()
    if email:
        return email.split("@", 1)[0]
    account_id = str(metadata.get("accountId") or "").strip()
    return account_id[:8] if account_id else "account"


def limits_per_account(
    reports: list[dict[str, Any]], provider: str
) -> list[dict[str, Any]]:
    """One limit per window PER credential, labelled by account.

    Use this where the accounts are not interchangeable — two paid Claude
    subscriptions are two things to watch, and collapsing them would hide the
    one that is nearly spent.
    """
    out: list[dict[str, Any]] = []
    matching = [report for report in reports if report.get("provider") == provider]
    for report in matching:
        label = account_label(report)
        for entry in report.get("limits") or []:
            if not isinstance(entry, dict):
                continue
            amount = _dict(entry.get("amount"))
            fraction = _fraction(amount)
            if fraction is None:
                continue
            window = _dict(entry.get("window"))
            title = _title(entry)
            # Only qualify the title when more than one account is in play.
            if len(matching) > 1:
                title = f"{title} · {label}"
            out.append(
                au.limit(
                    title,
                    fraction,
                    au.epoch_ms_iso(window.get("resetsAt")),
                    used=amount.get("used"),
                    allowance=amount.get("limit"),
                )
            )
    return out


def _no_usage_error(broker_provider: str, reports: list[dict[str, Any]]) -> au.CollectorError:
    """Explain an empty result without guessing that the login is missing."""
    if accounts_for(reports, broker_provider) > 0:
        return au.CollectorError(
            f"The auth broker reported no {broker_provider} usage windows"
        )
    if stored_credentials(broker_provider) > 0:
        # Connected, but the provider's quota is not machine-readable: the
        # broker publishes only what its usage probe can fetch.
        return au.CollectorError(
            f"The auth broker holds a {broker_provider} credential but publishes no usage "
            f"for it — this provider exposes no quota API, so there is nothing to read"
        )
    return au.CollectorError(
        f"The auth broker holds no {broker_provider} credential "
        f"(add one with `omp auth-broker login {broker_provider}`)"
    )


def scan_per_account(
    agent_id: str, name: str, broker_provider: str, tier_label: str = ""
) -> dict[str, Any]:
    """Broker-backed scan that keeps every account's windows separate."""
    reports = fetch_reports()
    limits = limits_per_account(reports, broker_provider)
    if not limits:
        raise _no_usage_error(broker_provider, reports)
    accounts = accounts_for(reports, broker_provider)
    tier = tier_label
    if accounts > 1:
        tier = f"{tier_label} · {accounts} accounts".strip(" ·")
    return au.record(agent_id, name, ready=True, limits=limits, tier_label=tier)

def scan_provider(
    agent_id: str, name: str, broker_provider: str, tier_label: str = ""
) -> dict[str, Any]:
    """Standard broker-backed scan: fetch, filter, merge, emit."""
    reports = fetch_reports()
    limits = limits_for(reports, broker_provider)
    if not limits:
        raise _no_usage_error(broker_provider, reports)
    accounts = accounts_for(reports, broker_provider)
    tier = tier_label
    if accounts > 1:
        tier = f"{tier_label} · {accounts} accounts".strip(" ·")
    return au.record(agent_id, name, ready=True, limits=limits, tier_label=tier)
