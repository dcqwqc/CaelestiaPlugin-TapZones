import QtQuick
import Caelestia.Plugins
import dcqwqc.tapzones

Item {
    property SettingsObject settings: null

    visible: false
    implicitWidth: 0
    implicitHeight: 0

    // The daemon reads persisted plugin settings directly and publishes its
    // runtime status atomically under XDG_RUNTIME_DIR. No polling subprocess is
    // needed here; SettingsUi watches that JSON file with FileView.
}
