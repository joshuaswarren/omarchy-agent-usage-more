# omarchy-agent-usage-more

Usage collectors for AI subscriptions that Omarchy does not ship and the
existing collector packs do not cover: **ClinePass, Kimi Code, MiniMax,
OpenRouter** and (opt-in) **Factory**.

Records land in `~/.local/state/omarchy/agents/usage/`, which is the directory
Omarchy's built-in `omarchy.agents` panel watches — and which any other
renderer can read, including the ASCII-bar plugins. No UI code, no forked
panel: one JSON record per provider, written where the panel already looks.

```
bin/omarchy-agent-usage-<id>  ──prints one JSON record──┐
bin/update-all  (timer, 5 min) ────writes───────────────┴─> ~/.local/state/omarchy/agents/usage/<id>.json
                                                              │
                                        omarchy.agents panel ─┤
                                        gladimdim.ai-limits  ─┘
```

## Providers

| Collector | Source | Windows reported | Credential |
|---|---|---|---|
| `clinepass` | `api.cline.bot/api/v1/users/me/plan/usage-limits` | 5-hour, weekly, monthly | API key |
| `kimi` | `api.kimi.com/coding/v1/usages` | rate window(s) + weekly allowance | API key |
| `minimax` | `platform.minimax.io/v1/api/openplatform/coding_plan/remains` | session + weekly, per model family | API key |
| `openrouter` | `openrouter.ai/api/v1/key` + `/credits` | key spend cap, prepaid balance | API key |
| `factory` | `app.factory.ai/api/billing/limits` + subscription usage | 5-hour, weekly, monthly, plan tokens | droid CLI session (**opt-in**, see below) |

Every collector prints a record even when it fails, carrying
`usageStatusText` and `authHelpText` so the panel can explain itself instead
of showing an empty tab.

## Install

As an Omarchy plugin (refreshes from the shell service every 5 minutes):

```sh
omarchy plugin add https://github.com/joshuaswarren/omarchy-agent-usage-more.git --enable
omarchy-shell shell rescanPlugins
```

Or standalone, with a systemd user timer:

```sh
git clone https://github.com/joshuaswarren/omarchy-agent-usage-more.git
cd omarchy-agent-usage-more && ./install.sh
```

Requires `jq` and `python3` (both ship with Omarchy). The Factory collector
additionally needs `python-cryptography`, which Omarchy's system Python has.

## Credentials

Each collector resolves its key from the first source that has one:

1. environment variable — `CLINE_API_KEY`, `KIMI_API_KEY`, `MINIMAX_API_KEY`,
   `OPENROUTER_API_KEY`
2. `~/.config/omarchy/agents/<id>.json` → `{"apiKey": "..."}`
3. `~/.config/codexbar/config.json` — reused when
   [CodexBar](https://github.com/steipete/CodexBar) already tracks that
   provider on this machine, so the secret is not stored twice
4. a provider-native file, e.g. `~/.config/cline/api-key`

Nothing is written back to those files, and no credential ever appears in a
record.

### Factory is opt-in

Factory's only machine-local credential is the droid CLI's WorkOS refresh
token in `~/.factory/auth.v2.file`. WorkOS invalidates a refresh token when it
is exchanged, so whichever process refreshes last owns the session: polling
this collector on a machine where you also run `droid` will eventually force
droid to sign in again. It therefore stays off until you ask for it:

```sh
mkdir -p ~/.config/omarchy/agents
echo '{"enabled": true}' > ~/.config/omarchy/agents/factory.json
```

The rotated token is stored in
`~/.local/state/omarchy/agents/credentials/factory.json` (0600) rather than
written back into droid's file, and is re-imported from droid automatically
when it goes stale.

## Refresh

```sh
bin/update-all            # all collectors, writes records
bin/omarchy-agent-usage-kimi | jq    # one collector, straight to stdout
omarchy-shell agent-usage-more refresh   # when installed as a plugin
```

## Record contract

The panel's contract, documented in
`/usr/share/omarchy/shell/plugins/agents/README.md`: `schemaVersion`, `id`,
`name`, `updatedAt`, `ready`, `tierLabel`, `usageStatusText`, `authHelpText`,
`limits[] = {title, percent (0.0–1.0), resetsAt, used, allowance}`, and
`balance` for prepaid plans. `lib/agentusage.py` builds it; a new provider is
one file in `bin/` plus a fixture test.

## Tests

```sh
python3 tests/test_collectors.py
```

Fixture-driven, no network: captured provider payloads in, records out, with
the percent math, window titles and reset timestamps pinned.

## Credit

Provider endpoints and quota math were derived from
[steipete/CodexBar](https://github.com/steipete/CodexBar) (MIT), which
implements all of these on macOS. The collector/runner shape follows
[hancengiz/omarchy-agent-usage-extras](https://github.com/hancengiz/omarchy-agent-usage-extras)
(MIT). This project is not affiliated with either.

## License

MIT — see [LICENSE](LICENSE).
