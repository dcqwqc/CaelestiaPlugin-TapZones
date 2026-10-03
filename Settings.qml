import Caelestia.Plugins

// The daemon reads these from Caelestia's persisted plugin settings. Calibration
// profiles themselves deliberately live under XDG_STATE_HOME, never in shell config.
SettingsObject {
    property bool enabled: false
    property int sensitivity: 55
    property int confidence: 72
    property string accelPolicy: "required"
    property int cooldownMs: 700
    property int multiTapWindowMs: 420
    property int calibrationCount: 12
    property string actionsJson: "{\"TL\":{\"1\":\"volume_up\",\"2\":\"media_toggle\",\"3\":\"next\"},\"TR\":{\"1\":\"volume_down\",\"2\":\"previous\",\"3\":\"media_toggle\"},\"BL\":{\"1\":\"previous\",\"2\":\"volume_down\",\"3\":\"none\"},\"BR\":{\"1\":\"next\",\"2\":\"volume_up\",\"3\":\"none\"}}"

    SettingMeta on enabled {
        label: "Enable tap detector"
        description: "Disabled by default and remains guarded until calibration is complete."
        icon: "touch_app"
        inputType: SettingMeta.Switch
    }
    SettingMeta on sensitivity {
        label: "Sensitivity"
        description: "Higher values accept weaker impulses."
        icon: "tune"
        inputType: SettingMeta.Slider
        min: 1
        max: 100
        step: 1
    }
    SettingMeta on confidence {
        label: "Required confidence"
        description: "Rejects uncertain or out-of-distribution taps."
        icon: "verified"
        inputType: SettingMeta.Slider
        min: 50
        max: 95
        step: 1
    }
    SettingMeta on accelPolicy {
        label: "Accelerometer policy"
        description: "Required is most resistant to accidental audio triggers."
        icon: "sensors"
        inputType: SettingMeta.SplitButton
        options: ["required", "preferred", "off"]
    }
    SettingMeta on cooldownMs {
        label: "Cooldown (ms)"
        description: "Minimum interval after an accepted gesture."
        icon: "timer"
        inputType: SettingMeta.SpinBox
        min: 100
        max: 3000
        step: 50
    }
    SettingMeta on multiTapWindowMs {
        label: "Multi-tap window (ms)"
        description: "Groups one, two or three nearby accepted taps."
        icon: "filter_3"
        inputType: SettingMeta.SpinBox
        min: 180
        max: 1000
        step: 20
    }
    SettingMeta on calibrationCount {
        label: "Samples per zone"
        description: "Guided calibration sample count."
        icon: "repeat"
        inputType: SettingMeta.SpinBox
        min: 6
        max: 30
        step: 1
    }
}
