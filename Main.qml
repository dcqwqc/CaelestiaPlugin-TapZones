import QtQuick
import Quickshell
import Quickshell.Io
import Caelestia.Plugins
import dcqwqc.tapzones

Item {
    id: root
    property SettingsObject settings: null
    visible: false
    implicitWidth: 0
    implicitHeight: 0
    readonly property string helper: `${Quickshell.env("HOME")}/.local/share/caelestia/plugins/tapzones/scripts/tapzones`

    // Settings are picked up by the daemon from plugins.json without restarting
    // Quickshell. This process only keeps its lightweight status snapshot fresh.
    Process {
        id: status
        command: [root.helper, "status"]
        running: false
    }
    Timer {
        interval: 5000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: {
            if (!status.running)
                status.running = true;
        }
    }
}
