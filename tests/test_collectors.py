#!/usr/bin/python3
"""Parser tests: fixture payload in, panel record out. No network.

Each collector's scan() is exercised with a captured provider response so the
percent math, window titles and reset timestamps stay pinned. Run with:
    python3 tests/test_collectors.py
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import agentusage as au  # noqa: E402
import broker  # noqa: E402

FIXTURES = json.loads((Path(__file__).resolve().parent / "fixtures.json").read_text())
FAILURES: list[str] = []


def load(name: str):
    """Import a hyphenated, extensionless collector as a module."""
    path = ROOT / "bin" / f"omarchy-agent-usage-{name}"
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def check(label: str, actual, expected) -> None:
    if actual != expected:
        FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")


def stub_http(module, payloads: dict):
    """Replace network access with the captured payloads, keyed by URL."""

    def fake_get_json(url, headers=None, timeout=None, label="provider"):
        for fragment, payload in payloads.items():
            if fragment in url:
                return payload
        raise au.CollectorError(f"no fixture for {url}")

    module.au.get_json = fake_get_json
    module.au.resolve_key = lambda *args, **kwargs: "test-key"


def titles(record: dict) -> list[str]:
    return [entry["title"] for entry in record["limits"]]


def percents(record: dict) -> list[int]:
    return [round(entry["percent"] * 100) for entry in record["limits"]]


def test_clinepass() -> None:
    module = load("clinepass")
    stub_http(module, {"api.cline.bot": FIXTURES["clinepass"]})
    record = module.scan()
    check("clinepass ready", record["ready"], True)
    check(
        "clinepass titles",
        titles(record),
        ["Session (5-hour)", "Weekly (7-day)", "Monthly"],
    )
    check("clinepass percents", percents(record), [12, 48, 3])
    check(
        "clinepass reset",
        record["limits"][1].get("resetsAt"),
        "2026-09-19T11:19:38.910991Z",
    )

def test_kimi() -> None:
    module = load("kimi")
    stub_http(module, {"api.kimi.com": FIXTURES["kimi"]})
    record = module.scan()
    check("kimi ready", record["ready"], True)
    check("kimi tier", record["tierLabel"], "Standard")
    # The 300-minute rate window must render as a 5-hour session window, and
    # the plan-level `usage` block as the weekly one.
    check("kimi titles", titles(record), ["Session (5-hour)", "Weekly (7-day)"])
    check("kimi percents", percents(record), [10, 47])
    check("kimi weekly used", record["limits"][1]["used"], 47)
    check("kimi weekly allowance", record["limits"][1]["allowance"], 100)


def test_minimax() -> None:
    module = load("minimax")
    stub_http(module, {"coding_plan/remains": FIXTURES["minimax"]})
    record = module.scan("global")
    check("minimax ready", record["ready"], True)
    # `remaining_percent` is inverted into used; the unprovisioned audio pack
    # (no allowance in either window) is dropped.
    check("minimax titles", titles(record), ["Session", "Weekly", "Video session", "Video weekly"])
    check("minimax percents", percents(record), [25, 60, 0, 50])
    check("minimax reset", record["limits"][0].get("resetsAt"), "2026-09-13T20:00:00Z")


def test_openrouter() -> None:
    module = load("openrouter")
    stub_http(
        module,
        {"/api/v1/key": FIXTURES["openrouter_key"], "/api/v1/credits": FIXTURES["openrouter_credits"]},
    )
    record = module.scan()
    check("openrouter ready", record["ready"], True)
    check("openrouter titles", titles(record), ["Daily key spend"])
    check("openrouter percents", percents(record), [25])
    check("openrouter balance", record["balance"]["remaining"], 0.2804)
    check("openrouter funded", record["balance"]["funded"], 10.0)


def test_factory_windows() -> None:
    module = load("factory")
    limits = module._window_limits(FIXTURES["factory_limits"])
    check(
        "factory titles",
        [entry["title"] for entry in limits],
        ["Session (5-hour)", "Weekly (7-day)", "Monthly"],
    )
    check("factory percents", [round(e["percent"] * 100) for e in limits], [0, 0, 5])
    check(
        "factory reset",
        limits[2].get("resetsAt"),
        "2026-09-16T15:35:49.655000Z",
    )


def test_broker_multi_account_merge() -> None:
    reports = FIXTURES["broker_reports"]
    limits = broker.limits_for(reports, "anthropic")
    titles_out = [entry["title"] for entry in limits]
    check("broker titles", titles_out, ["Claude 5 Hour", "Claude 7 Day"])
    # Two anthropic credentials: 63% and 100% on the weekly window. The
    # account with headroom is the one the broker rotates to, so the merged
    # record must not report the exhausted sibling.
    check("broker merge keeps headroom", [round(e["percent"] * 100) for e in limits], [15, 63])
    check("broker reset", limits[0].get("resetsAt"), "2026-09-13T16:00:00Z")
    check("broker accounts", broker.accounts_for(reports, "anthropic"), 2)


def test_broker_window_titles_and_units() -> None:
    reports = FIXTURES["broker_reports"]
    cursor = broker.limits_for(reports, "cursor")
    # A label that already names its window must not be suffixed twice, and a
    # limit with no usable amount is dropped rather than drawn as 0%.
    check("cursor titles", [entry["title"] for entry in cursor], ["Other Models (Monthly)"])
    check("cursor percent", [round(e["percent"] * 100) for e in cursor], [100])
    check("cursor allowance", cursor[0]["allowance"], 400)
    check("missing provider", broker.limits_for(reports, "nope"), [])


def test_opencode_plan_wiring() -> None:
    module = load("opencode")
    check("opencode owns the opencode id", module.AGENT_ID, "opencode")
    check("opencode row name", module.AGENT_NAME, "OpenCode")
    check("opencode reads the go credential", module.BROKER_PROVIDER, "opencode-go")


def test_record_defaults() -> None:
    record = au.record("demo", "Demo")
    check("record schema", record["schemaVersion"], 1)
    check("record scope", record["scope"], "account")
    check("record not ready", record["ready"], False)
    check("record limits", record["limits"], [])
    # A failed scan still has to emit a usable record, or the panel shows a
    # stale file with no explanation. Capture the emitted JSON rather than
    # letting it litter the test output.
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        failed = au.run("demo", "Demo", lambda: (_ for _ in ()).throw(au.CollectorError("nope")))
    emitted = json.loads(buffer.getvalue())
    check("run exit code", failed, 0)
    check("failed record not ready", emitted["ready"], False)
    check("failed record help", emitted["authHelpText"], "nope")


def test_fraction_guards() -> None:
    check("zero allowance", au.fraction(5, 0), 0.0)
    check("over allowance clamps", au.fraction(15, 10), 1.0)
    check("percent clamps", au.percent_fraction(140), 1.0)
    check("percent floor", au.percent_fraction(-3), 0.0)
    check("bad epoch", au.epoch_ms_iso("nope"), "")
    check("bad iso", au.iso("not-a-date"), "")


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    if FAILURES:
        print("\n".join(f"FAIL {failure}" for failure in FAILURES))
        return 1
    print(f"ok - {len(tests)} collector test groups passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
