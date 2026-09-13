import QtQuick
import Quickshell
import Quickshell.Io

// Refresh service: runs bin/update-all on a timer so the built-in
// omarchy.agents panel (and any renderer watching the usage directory) sees
// current records. No UI of its own — the panel does the drawing.
//
// The shell-service shape here follows hancengiz/omarchy-agent-usage-extras
// (MIT), reduced to the refresh loop.

Item {
  id: root

  property var shell: null
  property var manifest: null

  // Seconds between refreshes; AGENT_USAGE_MORE_INTERVAL overrides, floor 60.
  readonly property int intervalSec: {
    var raw = String(Quickshell.env("AGENT_USAGE_MORE_INTERVAL") || "")
    var parsed = parseInt(raw, 10)
    return (isFinite(parsed) && parsed >= 60) ? parsed : 300
  }

  // Quickshell's Qt.resolvedUrl() returns a Url that resists string coercion,
  // so the plugin directory is rebuilt from HOME plus the manifest id.
  readonly property string pluginDir: {
    var home = Quickshell.env("HOME") || ""
    var configHome = Quickshell.env("XDG_CONFIG_HOME") || (home + "/.config")
    var id = (manifest && manifest.id) ? manifest.id : "io.github.joshuaswarren.agent-usage-more"
    return configHome + "/omarchy/plugins/" + id
  }

  Process {
    id: refreshProcess
    command: [root.pluginDir + "/bin/update-all"]
  }

  function refresh() {
    if (!refreshProcess.running)
      refreshProcess.running = true
  }

  IpcHandler {
    target: "agent-usage-more"
    function refresh(): void { root.refresh() }
  }

  Timer {
    interval: root.intervalSec * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }
}
