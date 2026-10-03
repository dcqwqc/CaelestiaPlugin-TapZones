pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Caelestia.Config
import qs.components
import qs.modules.nexus.common

ColumnLayout {
    id: root
    property var settings: null
    property var status: ({})
    readonly property string helper: `${Quickshell.env("HOME")}/.local/share/caelestia/plugins/tapzones/scripts/tapzones`
    readonly property var zones: ["TL", "TR", "BL", "BR"]
    readonly property var actions: ["none", "volume_up", "volume_down", "media_toggle", "next", "previous", "quick_settings"]
    Layout.fillWidth: true
    spacing: Tokens.spacing.extraSmall

    function actionMap(): var {
        try {
            return JSON.parse(String(settings?.actionsJson ?? "{}"));
        } catch (e) {
            return {};
        }
    }
    function currentAction(zone, count): string {
        return String(actionMap()[zone]?.[String(count)] ?? "none");
    }
    function setAction(zone, count, value): void {
        if (!settings)
            return;
        const map = actionMap();
        if (!map[zone])
            map[zone] = {};
        map[zone][String(count)] = value;
        settings.actionsJson = JSON.stringify(map);
    }
    function refresh(): void {
        if (!statusProcess.running)
            statusProcess.running = true;
    }
    function calibrate(zone): void {
        calibration.command = [helper, "calibrate", zone, "--count", String(settings?.calibrationCount ?? 12)];
        calibration.running = true;
        refreshTimer.restart();
    }

    Process {
        id: statusProcess
        command: [root.helper, "status"]
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    root.status = JSON.parse(text);
                } catch (e) {
                    root.status = {
                        running: false
                    };
                }
            }
        }
    }
    Process {
        id: calibration
    }
    Process {
        id: tester
    }
    Timer {
        id: refreshTimer
        interval: 700
        repeat: true
        running: true
        onTriggered: root.refresh()
    }

    SectionHeader {
        first: true
        text: qsTr("Tap Zones")
    }
    SwitchRow {
        Layout.fillWidth: true
        first: true
        label: qsTr("Enable detector")
        subtext: qsTr("Stays safely off until you calibrate all four zones.")
        checked: Boolean(root.settings?.enabled ?? false)
        onToggled: checked => {
            if (root.settings)
                root.settings.enabled = checked;
        }
    }
    StepperRow {
        Layout.fillWidth: true
        label: qsTr("Sensitivity")
        subtext: qsTr("Higher accepts weaker physical impulses")
        from: 1
        to: 100
        stepSize: 1
        value: Number(root.settings?.sensitivity ?? 55)
        onMoved: v => root.settings.sensitivity = Math.round(v)
    }
    StepperRow {
        Layout.fillWidth: true
        last: true
        label: qsTr("Confidence")
        subtext: qsTr("Minimum classifier confidence")
        from: 50
        to: 95
        stepSize: 1
        value: Number(root.settings?.confidence ?? 72)
        onMoved: v => root.settings.confidence = Math.round(v)
    }
    RowLayout {
        Layout.fillWidth: true
        StyledText {
            Layout.preferredWidth: 128
            text: qsTr("Accelerometer")
        }
        ComboBox {
            id: accelPolicy
            Layout.fillWidth: true
            model: ["required", "preferred", "off"]
            Component.onCompleted: currentIndex = Math.max(0, model.indexOf(String(root.settings?.accelPolicy ?? "required")))
            onActivated: index => root.settings.accelPolicy = model[index]
        }
    }

    SectionHeader {
        text: qsTr("Guided calibration")
    }
    StyledText {
        Layout.fillWidth: true
        Layout.leftMargin: Tokens.padding.small
        Layout.rightMargin: Tokens.padding.small
        wrapMode: Text.WordWrap
        color: Colours.palette.m3outline
        font: Tokens.font.body.small
        text: qsTr("Place the laptop normally. Select a corner, then tap it once per prompt. The daemon stores only normalized features — never audio.")
    }
    Repeater {
        model: root.zones
        Button {
            required property string modelData
            Layout.fillWidth: true
            text: {
                const c = root.status.calibration;
                if (c && c.zone === modelData)
                    return c.complete ? `${modelData} — complete (${c.have}/${c.need})` : `${modelData} — tap now (${c.have}/${c.need})`;
                const count = root.status.profile?.[modelData]?.count ?? 0;
                return count ? `${modelData} — ${count} samples; recalibrate` : `${modelData} — start ${root.settings?.calibrationCount ?? 12} taps`;
            }
            onClicked: root.calibrate(modelData)
        }
    }
    Button {
        Layout.fillWidth: true
        text: qsTr("Reset every calibration profile")
        onClicked: {
            calibration.command = [root.helper, "reset"];
            calibration.running = true;
            root.refresh();
        }
    }

    SectionHeader {
        text: qsTr("Actions")
    }
    Repeater {
        model: root.zones
        ColumnLayout {
            id: zoneRow
            required property string modelData
            Layout.fillWidth: true
            StyledText {
                text: modelData
                font: Tokens.font.title.small
                color: Colours.palette.m3onSurface
            }
            Repeater {
                model: [1, 2, 3]
                RowLayout {
                    id: tapRow
                    required property int modelData
                    Layout.fillWidth: true
                    StyledText {
                        Layout.preferredWidth: 86
                        text: modelData === 1 ? qsTr("Single tap") : modelData === 2 ? qsTr("Double tap") : qsTr("Triple tap")
                    }
                    ComboBox {
                        Layout.fillWidth: true
                        model: root.actions
                        Component.onCompleted: currentIndex = Math.max(0, root.actions.indexOf(root.currentAction(zoneRow.modelData, tapRow.modelData)))
                        onActivated: index => root.setAction(zoneRow.modelData, tapRow.modelData, root.actions[index])
                    }
                    Button {
                        text: qsTr("Test")
                        onClicked: {
                            tester.command = [root.helper, "action-test", root.currentAction(zoneRow.modelData, tapRow.modelData)];
                            tester.running = true;
                        }
                    }
                }
            }
        }
    }
    StyledText {
        Layout.fillWidth: true
        Layout.leftMargin: Tokens.padding.small
        Layout.rightMargin: Tokens.padding.small
        wrapMode: Text.WordWrap
        color: Colours.palette.m3outline
        font: Tokens.font.body.small
        text: qsTr("Quick Settings is shown only as a safe unavailable placeholder because this Caelestia install exposes no stable IPC action. Custom commands are intentionally not authored in the UI; use a JSON argv list in the local config if needed.")
    }

    SectionHeader {
        text: qsTr("Diagnostics")
    }
    StyledText {
        Layout.fillWidth: true
        Layout.leftMargin: Tokens.padding.small
        Layout.rightMargin: Tokens.padding.small
        wrapMode: Text.WordWrap
        color: Colours.palette.m3outline
        font: Tokens.font.body.small
        text: root.status.running ? qsTr("Service running · microphone: %1 · accelerometer: %2").arg(root.status.audio ?? "unknown").arg(root.status.accel ?? "unknown") : qsTr("Service unavailable. Run install.sh install and check systemctl --user status tapzones.")
    }
    StepperRow {
        Layout.fillWidth: true
        label: qsTr("Cooldown")
        subtext: qsTr("Milliseconds after an accepted tap")
        from: 100
        to: 3000
        stepSize: 50
        value: Number(root.settings?.cooldownMs ?? 700)
        onMoved: v => root.settings.cooldownMs = Math.round(v)
    }
    StepperRow {
        Layout.fillWidth: true
        last: true
        label: qsTr("Multi-tap window")
        subtext: qsTr("Milliseconds used to group taps")
        from: 180
        to: 1000
        stepSize: 20
        value: Number(root.settings?.multiTapWindowMs ?? 420)
        onMoved: v => root.settings.multiTapWindowMs = Math.round(v)
    }
}
