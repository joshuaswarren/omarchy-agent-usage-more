#!/bin/bash
# Install the refresh timer. The collectors themselves need no installation:
# they are standalone executables that print one JSON record each.

set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"

command -v jq >/dev/null || {
  echo "jq is required (bin/update-all validates collector output with it)" >&2
  exit 1
}

chmod +x "$SRC"/bin/*
mkdir -p "$UNIT_DIR"

# Point the unit at wherever this checkout actually lives, so a plain git clone
# works as well as an `omarchy plugin add` install.
sed "s|%h/.config/omarchy/plugins/io.github.joshuaswarren.agent-usage-more/bin/update-all|$SRC/bin/update-all|" \
  "$SRC/systemd/omarchy-agent-usage-more.service" >"$UNIT_DIR/omarchy-agent-usage-more.service"
cp "$SRC/systemd/omarchy-agent-usage-more.timer" "$UNIT_DIR/"

systemctl --user daemon-reload
systemctl --user enable --now omarchy-agent-usage-more.timer

"$SRC/bin/update-all" || true

echo "Installed. Records land in ${XDG_STATE_HOME:-$HOME/.local/state}/omarchy/agents/usage/"
echo "Refresh now with: $SRC/bin/update-all"
