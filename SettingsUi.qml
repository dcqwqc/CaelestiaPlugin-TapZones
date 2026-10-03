pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Caelestia.Config
import qs.components
import qs.components.controls
import qs.modules.nexus.common
import dcqwqc.tapzones

ColumnLayout {
    id: root

    property var settings: null
    property var status: ({})
    readonly property string helper: `${Quickshell.env("HOME")}/.local/share/caelestia/plugins/tapzones/scripts/tapzones`
    readonly property var zones: ["TL", "TR", "BL", "BR"]
    readonly property var actions: ["none", "volume_up", "volume_down", "media_toggle", "next", "previous", "quick_settings"]
    readonly property var policies: [
        {
            value: "required",
            label: qsTr("Required"),
            icon: "sensors"
        },
        {
            value: "preferred",
            label: qsTr("Preferred"),
            icon: "auto_awesome"
        },
        {
            value: "off",
            label: qsTr("Mic only"),
            icon: "mic"
        }
    ]

    Layout.fillWidth: true
    spacing: Tokens.spacing.extraSmall

    function zoneName(zone): string {
        switch (zone) {
        case "TL":
            return qsTr("Top left");
        case "TR":
            return qsTr("Top right");
        case "BL":
            return qsTr("Bottom left");
        case "BR":
            return qsTr("Bottom right");
        default:
            return zone ?? "—";
        }
    }

    function zoneIcon(zone): string {
        switch (zone) {
        case "TL":
            return "north_west";
        case "TR":
            return "north_east";
        case "BL":
            return "south_west";
        case "BR":
            return "south_east";
        default:
            return "touch_app";
        }
    }

    function prettyAction(action): string {
        switch (action) {
        case "none":
            return qsTr("None");
        case "volume_up":
            return qsTr("Volume up");
        case "volume_down":
            return qsTr("Volume down");
        case "media_toggle":
            return qsTr("Play / pause");
        case "next":
            return qsTr("Next track");
        case "previous":
            return qsTr("Previous track");
        case "quick_settings":
            return qsTr("Quick Settings");
        default:
            return action;
        }
    }

    function actionIcon(action): string {
        switch (action) {
        case "volume_up":
            return "volume_up";
        case "volume_down":
            return "volume_down";
        case "media_toggle":
            return "play_pause";
        case "next":
            return "skip_next";
        case "previous":
            return "skip_previous";
        case "quick_settings":
            return "tune";
        default:
            return "remove";
        }
    }

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
        calibration.command = [helper, "calibrate", zone, "--count", String(settings?.calibrationCount ?? 6)];
        calibration.running = true;
        refreshTimer.restart();
    }

    function startEnhancedCalibration(): void {
        calibration.command = [helper, "calibrate-enhanced"];
        calibration.running = true;
        refreshTimer.restart();
    }

    function resetEnhancedCalibration(): void {
        calibration.command = [helper, "reset-enhanced"];
        calibration.running = true;
        refreshTimer.restart();
    }

    function enhancedCalibrationText(): string {
        const c = status.calibration;
        if (c && c.mode === "enhanced-auto" && !c.complete) {
            const counts = c.counts ?? {};
            return qsTr("Tap %1 now · TL %2 · TR %3 · BL %4 · BR %5 · rejected %6").arg(zoneName(c.zone)).arg(counts.TL ?? 0).arg(counts.TR ?? 0).arg(counts.BL ?? 0).arg(counts.BR ?? 0).arg(c.rejected ?? 0);
        }
        const quality = status.enhancedQuality;
        if (quality && Number(quality.total ?? 0) > 0)
            return qsTr("v3 validation %1/%2 (%3%) · left/right %4% · top/bottom %5%").arg(quality.correct).arg(quality.total).arg(Math.round(Number(quality.accuracy ?? 0) * 100)).arg(Math.round(Number(quality.lrAccuracy ?? 0) * 100)).arg(Math.round(Number(quality.tbAccuracy ?? 0) * 100));
        return qsTr("v3 not calibrated yet. The current v2 model stays active until v3 proves it is at least as reliable.");
    }

    function toggleLiveTest(): void {
        tester.command = status.testActive ? [helper, "test-stop"] : [helper, "test-start"];
        tester.running = true;
        refreshTimer.restart();
    }

    function readinessText(): string {
        const active = String(status.activeModel ?? "v2-axis");
        const quality = active === "v3-dispersion" ? status.enhancedQuality : status.calibrationQuality;
        if (quality && Number(quality.total ?? 0) > 0)
            return qsTr("Active model: %1 · %2/%3 correct (%4%) · left/right %5% · top/bottom %6%.").arg(active).arg(quality.correct).arg(quality.total).arg(Math.round(Number(quality.accuracy ?? 0) * 100)).arg(Math.round(Number(quality.lrAccuracy ?? 0) * 100)).arg(Math.round(Number(quality.tbAccuracy ?? 0) * 100));
        return qsTr("Active model: %1 · calibration data is incomplete.").arg(active);
    }

    function triggerText(): string {
        if (status.triggerSource === "microphone" && status.degraded)
            return qsTr("Microphone fallback is active because the accelerometer is unavailable. A reboot may restore sensor-fusion accuracy.");
        if (status.triggerSource === "microphone")
            return qsTr("Microphone-only trigger is active.");
        if (status.triggerSource === "accelerometer")
            return qsTr("Accelerometer trigger is active.");
        if (status.degraded)
            return qsTr("The required accelerometer is unavailable.");
        return qsTr("Waiting for sensor samples.");
    }

    function testResultText(): string {
        const event = status.testEvent;
        if (!event)
            return status.testActive ? qsTr("Armed. Tap once — the result freezes immediately.") : qsTr("Press Test, tap once, then press Test again for another try. Actions stay disabled during the test.");
        const source = event.triggerSource === "microphone" ? qsTr("microphone") : qsTr("accelerometer");
        const trained = Number(event.trainedZones?.length ?? 0);
        if (!event.zone)
            return qsTr("Tap detected · %1 · no trained zones yet. Calibrate any corner and the next test can start predicting it.").arg(source);
        const pct = Math.round(Number(event.confidence ?? 0) * 100);
        const suffix = event.profileReady ? qsTr("all 4 zones trained") : qsTr("%1/4 zones trained").arg(trained);
        const model = String(event.model ?? "v2-axis");
        return event.accepted ? qsTr("%1 · %2% confidence · accepted · %3 · %4 · %5").arg(zoneName(event.zone)).arg(pct).arg(source).arg(model).arg(suffix) : qsTr("%1 · %2% confidence · below your %3% threshold · %4 · %5 · %6").arg(zoneName(event.zone)).arg(pct).arg(Math.round(Number(event.threshold ?? 0) * 100)).arg(source).arg(model).arg(suffix);
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
        interval: (root.status.testActive || (root.status.calibration && !root.status.calibration.complete)) ? 100 : 700
        repeat: true
        running: true
        onTriggered: root.refresh()
    }

    Component {
        id: policyMenuItem
        MenuItem {
            required property string storedValue
        }
    }

    Component {
        id: actionMenuItem
        MenuItem {
            required property string storedValue
        }
    }

    SectionHeader {
        first: true
        text: qsTr("Tap Zones")
    }

    ToggleRow {
        Layout.fillWidth: true
        first: true
        text: qsTr("Enable detector")
        subtext: qsTr("Normal actions run only after all four zones are calibrated.")
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
        subtext: qsTr("Minimum confidence required for normal actions")
        from: 50
        to: 95
        stepSize: 1
        value: Number(root.settings?.confidence ?? 72)
        onMoved: v => root.settings.confidence = Math.round(v)
    }

    Item {
        id: policyPicker
        Layout.fillWidth: true
        implicitHeight: policyRow.implicitHeight

        readonly property var entries: root.policies.map(policy => policyMenuItem.createObject(policyPicker, {
                text: policy.label,
                icon: policy.icon,
                storedValue: policy.value
            }))

        SelectRow {
            id: policyRow
            anchors.fill: parent
            label: qsTr("Accelerometer")
            subtext: qsTr("Preferred automatically falls back to the microphone when the sensor is unavailable.")
            menuItems: policyPicker.entries
            active: {
                const value = String(root.settings?.accelPolicy ?? "preferred");
                const index = root.policies.findIndex(policy => policy.value === value);
                return policyPicker.entries[Math.max(0, index)] ?? null;
            }
            onSelected: item => {
                if (root.settings)
                    root.settings.accelPolicy = item.storedValue;
            }
        }
    }

    SectionHeader {
        text: qsTr("Best calibration")
    }

    StyledRect {
        Layout.fillWidth: true
        implicitHeight: bestCalibrationLayout.implicitHeight + Tokens.padding.large * 2
        radius: Tokens.rounding.extraLarge
        color: Colours.tPalette.m3surfaceContainer

        ColumnLayout {
            id: bestCalibrationLayout
            anchors.fill: parent
            anchors.margins: Tokens.padding.large
            spacing: Tokens.spacing.medium

            StyledText {
                Layout.fillWidth: true
                text: qsTr("Research v3 · dispersion + axis fusion")
                color: Colours.palette.m3onSurface
                font: Tokens.font.label.large
            }

            StyledText {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: Colours.palette.m3outline
                font: Tokens.font.body.small
                text: qsTr("This calibration alternates corners instead of training one corner at a time. It measures frequency-specific arrival times, stereo delay, spectral envelope and impact decay. Bad/clipped/noisy taps are rejected automatically. The current v2 model remains active unless v3 validates at least as well.")
            }

            IconTextButton {
                Layout.fillWidth: true
                icon: root.status.calibration?.mode === "enhanced-auto" && !root.status.calibration?.complete ? "hearing" : "model_training"
                text: {
                    const c = root.status.calibration;
                    if (c && c.mode === "enhanced-auto" && !c.complete)
                        return c.armed ? qsTr("TAP %1 NOW").arg(root.zoneName(c.zone)) : qsTr("Get ready · next: %1").arg(root.zoneName(c.zone));
                    return root.status.enhancedProfileReady ? qsTr("Recalibrate research v3") : qsTr("Start best calibration");
                }
                type: root.status.calibration?.mode === "enhanced-auto" && root.status.calibration?.armed ? IconTextButton.Filled : IconTextButton.Tonal
                shapeMorph: true
                verticalPadding: Tokens.padding.large
                onClicked: {
                    const c = root.status.calibration;
                    if (!(c && c.mode === "enhanced-auto" && !c.complete))
                        root.startEnhancedCalibration();
                }
            }

            GridLayout {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: Tokens.spacing.small
                rowSpacing: Tokens.spacing.small

                Repeater {
                    model: root.zones

                    StyledRect {
                        required property string modelData
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        implicitHeight: zoneLabel.implicitHeight + Tokens.padding.medium * 2
                        radius: Tokens.rounding.large
                        color: {
                            const c = root.status.calibration;
                            return c && c.mode === "enhanced-auto" && c.zone === modelData && c.armed ? Colours.palette.m3primaryContainer : Colours.tPalette.m3surfaceContainerHigh;
                        }

                        StyledText {
                            id: zoneLabel
                            anchors.centerIn: parent
                            text: {
                                const c = root.status.calibration;
                                const liveCount = c && c.mode === "enhanced-auto" ? Number(c.counts?.[modelData] ?? 0) : Number(root.status.enhancedProfile?.[modelData]?.count ?? 0);
                                return `${root.zoneName(modelData)} · ${liveCount}`;
                            }
                            color: Colours.palette.m3onSurface
                            font: Tokens.font.label.medium
                        }
                    }
                }
            }

            StyledText {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: root.enhancedCalibrationText()
                color: Colours.palette.m3outline
                font: Tokens.font.body.small
            }

            StyledText {
                Layout.fillWidth: true
                visible: Boolean(root.status.lastCalibrationReject)
                wrapMode: Text.WordWrap
                text: qsTr("Last tap ignored: %1 · just tap the requested corner again.").arg(root.status.lastCalibrationReject ?? "")
                color: Colours.palette.m3error
                font: Tokens.font.body.small
            }

            IconTextButton {
                Layout.fillWidth: true
                icon: "restart_alt"
                text: qsTr("Reset research v3 calibration")
                type: IconTextButton.Text
                onClicked: root.resetEnhancedCalibration()
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
        text: qsTr("Fallback model remains available: %1").arg(root.status.calibrationQuality ? `${Math.round(Number(root.status.calibrationQuality.accuracy ?? 0) * 100)}% validation` : qsTr("not calibrated"))
    }

    SectionHeader {
        text: qsTr("Live recognition test")
    }

    StyledRect {
        Layout.fillWidth: true
        implicitHeight: testLayout.implicitHeight + Tokens.padding.large * 2
        radius: Tokens.rounding.extraLarge
        color: Colours.tPalette.m3surfaceContainer

        ColumnLayout {
            id: testLayout

            anchors.fill: parent
            anchors.margins: Tokens.padding.large
            spacing: Tokens.spacing.medium

            RowLayout {
                Layout.fillWidth: true
                spacing: Tokens.spacing.medium

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0

                    StyledText {
                        Layout.fillWidth: true
                        text: root.status.testActive ? (root.status.testArmed ? qsTr("Tap now") : qsTr("Getting ready…")) : qsTr("Classifier playground")
                        font: Tokens.font.title.small
                        color: Colours.palette.m3onSurface
                    }

                    StyledText {
                        Layout.fillWidth: true
                        text: root.status.testActive ? (root.status.testArmed ? qsTr("Waiting for one new tap · actions disabled") : qsTr("Waiting for quiet so the next sound cannot be a false tap")) : (root.status.testEvent ? qsTr("Result locked · press Test again to retry") : (root.status.profileReady ? qsTr("See what Tap Zones thinks you tapped") : qsTr("v2 location model needs fresh calibration · train all four corners")))
                        color: Colours.palette.m3outline
                        font: Tokens.font.label.small
                    }
                }

                IconTextButton {
                    icon: root.status.testActive ? "stop_circle" : (root.status.testEvent ? "restart_alt" : "play_circle")
                    text: root.status.testActive ? qsTr("Stop") : (root.status.testEvent ? qsTr("Test again") : qsTr("Test"))
                    type: root.status.testActive ? IconTextButton.Filled : IconTextButton.Tonal
                    onClicked: root.toggleLiveTest()
                }
            }

            GridLayout {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: Tokens.spacing.small
                rowSpacing: Tokens.spacing.small

                Repeater {
                    model: root.zones

                    StyledRect {
                        required property string modelData

                        readonly property bool selected: root.status.testEvent?.zone === modelData

                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        implicitHeight: 62
                        radius: Tokens.rounding.large
                        color: selected ? (root.status.testEvent?.accepted ? Colours.palette.m3primaryContainer : Colours.palette.m3secondaryContainer) : Colours.tPalette.m3surfaceContainerHighest
                        border.width: selected ? 1 : 0
                        border.color: selected ? Colours.palette.m3primary : "transparent"

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: Tokens.padding.medium
                            spacing: Tokens.spacing.small

                            MaterialIcon {
                                text: root.zoneIcon(parent.parent.modelData)
                                color: parent.parent.selected ? Colours.palette.m3primary : Colours.palette.m3onSurfaceVariant
                                fontStyle: Tokens.font.icon.medium
                                fill: parent.parent.selected ? 1 : 0
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0

                                StyledText {
                                    text: root.zoneName(parent.parent.parent.modelData)
                                    color: Colours.palette.m3onSurface
                                    font: Tokens.font.body.small
                                }

                                StyledText {
                                    visible: parent.parent.parent.selected
                                    text: qsTr("%1%").arg(Math.round(Number(root.status.testEvent?.confidence ?? 0) * 100))
                                    color: Colours.palette.m3primary
                                    font: Tokens.font.label.small
                                }
                            }
                        }
                    }
                }
            }

            StyledText {
                Layout.fillWidth: true
                text: root.testResultText()
                wrapMode: Text.WordWrap
                color: root.status.testEvent?.accepted ? Colours.palette.m3primary : Colours.palette.m3onSurfaceVariant
                font: Tokens.font.body.small
            }
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
            spacing: Tokens.spacing.extraSmall

            StyledText {
                Layout.topMargin: Tokens.spacing.small
                text: root.zoneName(zoneRow.modelData)
                font: Tokens.font.title.small
                color: Colours.palette.m3onSurface
            }

            Repeater {
                model: [1, 2, 3]

                Item {
                    id: actionPicker
                    required property int modelData

                    Layout.fillWidth: true
                    implicitHeight: actionRow.implicitHeight

                    readonly property string actionValue: root.currentAction(zoneRow.modelData, actionPicker.modelData)
                    readonly property var entries: root.actions.map(action => actionMenuItem.createObject(actionPicker, {
                            text: root.prettyAction(action),
                            icon: root.actionIcon(action),
                            storedValue: action
                        }))

                    RowLayout {
                        id: actionRow
                        anchors.left: parent.left
                        anchors.right: parent.right
                        spacing: Tokens.spacing.small

                        StyledText {
                            Layout.preferredWidth: 92
                            text: actionPicker.modelData === 1 ? qsTr("Single tap") : actionPicker.modelData === 2 ? qsTr("Double tap") : qsTr("Triple tap")
                            color: Colours.palette.m3onSurfaceVariant
                            font: Tokens.font.body.small
                        }

                        SplitButton {
                            Layout.fillWidth: true
                            type: SplitButton.Tonal
                            menuItems: actionPicker.entries
                            active: {
                                const index = root.actions.indexOf(actionPicker.actionValue);
                                return actionPicker.entries[Math.max(0, index)] ?? null;
                            }
                            stateLayer.onClicked: expanded = !expanded
                            menu.onItemSelected: item => root.setAction(zoneRow.modelData, actionPicker.modelData, item.storedValue)
                        }

                        IconTextButton {
                            icon: "play_arrow"
                            text: qsTr("Test")
                            type: IconTextButton.Text
                            onClicked: {
                                tester.command = [root.helper, "action-test", actionPicker.actionValue];
                                tester.running = true;
                            }
                        }
                    }
                }
            }
        }
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
        text: root.status.running ? `${qsTr("Service running · microphone: %1 · accelerometer: %2").arg(root.status.audio ?? "unknown").arg(root.status.accel ?? "unknown")}\n${root.triggerText()}\n${root.status.triggerSource === "microphone" ? qsTr("Adaptive mic floor %1 · trigger RMS %2").arg(Number(root.status.noiseFloor ?? 0).toFixed(4)).arg(Number(root.status.tapRmsThreshold ?? 0).toFixed(4)) + "\n" : ""}${root.readinessText()}` : `${qsTr("Service unavailable. Check the Tap Zones service.")}\n${root.readinessText()}`
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
