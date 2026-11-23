# omarchy-agent-usage-more

[![Sponsor](https://img.shields.io/badge/Sponsor-%E2%9D%A4-pink)](https://github.com/sponsors/joshuaswarren)

Usage collectors for AI subscriptions that Omarchy does not ship and the
existing collector packs do not cover. Nine read a provider API with one key
(ClinePass, OpenRouter, Warp, Kilo, Amp and, opt-in, Factory) or, for Kimi
Code and MiniMax, a key when one is set and the broker otherwise. Ten read an
[omp](https://github.com/badlogic/pi-mono) auth broker (Claude Max, Codex,
Google AI Pro, Cursor, OpenCode Go, z.ai, SuperGrok, Alibaba, Devin and Ollama
Cloud).

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

Direct, one API key each:

| Collector | Source | Reports | Credential |
|---|---|---|---|
| `clinepass` | `api.cline.bot/api/v1/users/me/plan/usage-limits` | 5-hour, weekly, monthly | API key |
| `kimi-plan` | `api.kimi.com/coding/v1/usages` | rate window(s) + weekly allowance | API key, else broker (`kimi-code`) |
| `minimax-plan` | `platform.minimax.io/v1/api/openplatform/coding_plan/remains` | session + weekly, per model family | API key, else broker (`minimax-code`) |
| `openrouter-plan` | `openrouter.ai/api/v1/key` + `/credits` | key spend cap, prepaid balance | API key |
| `warp` | `app.warp.dev/graphql/v2?op=GetRequestLimitInfo` | AI request allowance, bonus credits | API key |
| `kilo` | `api.kilo.ai/api/profile` | prepaid balance, spend limit | API key / CLI session |
| `amp` | `ampcode.com/api/internal?userDisplayBalanceInfo` | prepaid balance | API key |
| `factory` | `app.factory.ai/api/billing/limits` + subscription usage | 5-hour, weekly, monthly, plan tokens | droid CLI session (**opt-in**, see below) |

Broker-backed, no local credential at all:

| Collector | Broker provider | Reports |
|---|---|---|
| `claude-max` | `anthropic` | 5-hour and 7-day windows, **one set per account** |
| `codex-plan` | `openai-codex` | 5-hour and 7-day windows, including the Spark tier |
| `google-ai-pro` | `google-antigravity` | weekly and 5-hour, per model family |
| `cursor-plan` | `cursor` | monthly request and spend caps |
| `opencode-plan` | `opencode-go` | 5-hour, weekly, monthly |
| `zai-plan` | `zai` | 5-hour token quota, monthly Zread |
| `grok-plan` | `xai-oauth` | SuperGrok weekly credits, Grok Build weekly |
| `alibaba` | `alibaba-coding-plan`, `alibaba-token-plan` | whichever plan the credential covers |
| `devin-plan` | `devin` | plan windows |
| `ollama-cloud` | `ollama-cloud` | plan windows when Ollama publishes any |

Every collector prints a record even when it fails, carrying
`usageStatusText` and `authHelpText` so the panel never shows an empty tab.


## One row per subscription

Omarchy's own updater rewrites `<id>.json` for every stock collector (`claude`,
`codex`, `grok`, `kimi`, `cursor`, `devin`, `zai`, `minimax`, `openrouter`,
`opencode` and more) on each panel refresh, and writes an empty record where
the machine has no local sign-in. A record under a stock id would be
overwritten within minutes, so a collector whose provider has a stock twin
takes a `-plan` id (`devin-plan`, `kimi-plan`, ...), like `claude-max` and
`codex-plan`. Hide the stock twin in the Agents widget settings
(`providers.<id>.enabled: false`) so each subscription has one tab. The panel
draws marks by id, so a `-plan` row shows the module's bar glyph instead of the
provider's logo.

## Broker-backed collectors

`omp auth-broker` already holds the OAuth credentials omp uses and publishes
an aggregate usage report on `GET /v1/usage`, refreshed server-side. That is
the only practical source for providers whose quota otherwise lives behind a
logged-in browser session — and it works on a machine that has never signed
into the provider's CLI.

`lib/broker.py` resolves the endpoint from `OMP_AUTH_BROKER_URL` /
`OMP_AUTH_BROKER_TOKEN`, then `auth.broker.url` / `auth.broker.token` in
`~/.omp/agent/config.yml` (including omp's `!command` indirection), then
`omp auth-broker status --json` and `omp auth-broker token`. Nothing is
written back.

When a provider holds several accounts, same-named windows merge to the
account with the **most headroom** — that is the one the broker rotates to, so
an exhausted sibling must not make the bar read "out of quota".

`claude-max` is a separate agent id rather than a second writer for
`claude.json`: Omarchy's stock `claude` collector owns that file and reports
"Waiting for auth" where Claude Code has no local login, and two writers would
flap between the two answers.

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
   `OPENROUTER_API_KEY`, `WARP_API_KEY`, `KILO_API_KEY`, `AMP_API_KEY`
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
bin/omarchy-agent-usage-kimi-plan | jq    # one collector, straight to stdout
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

## Not covered, and why


| Provider | Blocker |
|---|---|
| Gemini / Google AI Pro | Google retired Code Assist for individuals on the CLI client: `loadCodeAssist` answers `UNSUPPORTED_CLIENT … migrate to the Antigravity suite`, and `retrieveUserQuota` returns 403 "no valid license". The same plan is readable through the broker's Antigravity credential, which is what `google-ai-pro` reports. |
| Alibaba Model Studio coding plan | Connected, but Alibaba accepts no API key for quota. The console endpoint (`/data/api.json?action=…queryCodingPlanInstanceInfoV2`) answers `{"code":"ConsoleNeedLogin"}` with the key in the `Authorization`, `x-api-key` and `X-DashScope-API-Key` headers; the Bailian CLI does have `bl usage coding-plan`, but it is a `[Console]` command that answers "No console access token found" under an API-key login. Either route needs `bl auth login --console` (browser) or OpenAPI AK/SK. |
| Ollama Cloud | The broker holds the credential and reports zero limits; `ollama.com/api/tags` authenticates inference and lists models but carries no quota. Usage is rendered on `ollama.com/settings`. |
| Kilo, Amp | Their CLIs store nothing until you sign in (`~/.config/kilo/kilo.jsonc` is bare, `~/.config/amp` holds no token), and CodexBar keeps its copies in the OS keychain. |

A provider can be connected and still report nothing: the broker publishes
only what its per-provider usage probe can fetch. `lib/broker.py`
distinguishes the three cases — no credential (`omp auth-broker login
<provider>` is the fix), a credential with an empty usage window, and a
credential the broker has no probe for — so the panel never tells you to sign
in to an account that is already wired up.

## Credit

Provider endpoints and quota math were derived from
[steipete/CodexBar](https://github.com/steipete/CodexBar) (MIT), which
implements all of these on macOS. The collector/runner shape follows
[hancengiz/omarchy-agent-usage-extras](https://github.com/hancengiz/omarchy-agent-usage-extras)
(MIT). This project is not affiliated with either.

## Support

[Sponsor on GitHub](https://github.com/sponsors/joshuaswarren) or send Lightning to `joshuaswarren@strike.me`.

## License

MIT — see [LICENSE](LICENSE).