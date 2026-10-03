# Caelestia Tap Zones

Tap Zones is a local-only four-corner laptop-tap detector for Caelestia. It is intentionally disabled until calibrated.

## Privacy and architecture

`tapzones.service` reads accelerometer samples from the dynamically found `accel_3d` IIO device (about 10 Hz) and asks PipeWire's `pw-record` for the current default stereo source. Raw 48 kHz PCM is held only in a short in-memory window and is never written, sent, or logged. The persisted XDG state profile contains normalized feature vectors and centroid/std-dev summaries only.

Features combine acceleration direction and impulse, stereo energy balance, cross-correlation/TDOA lag, RMS/crest/decay, FFT centroid, and three FFT bands. A normalized nearest-centroid classifier rejects low-confidence and out-of-distribution observations. With no microphone the detector transparently remains accelerometer-only; the default `required` accelerometer policy avoids audio false positives.

## Install, calibrate, use

```bash
./install.sh install
```

Enable `dcqwqc/tapzones` in Caelestia, open its native settings page, keep the laptop in its usual position, and tap TL, TR, BL, and BR for the prompted 12 samples each. Then enable detection. Settings cover sensitivity, confidence, accelerometer policy, cooldown, grouping window, calibration count and each zone's one/two/three-tap action.

Built-ins use fixed argv (`wpctl` for volume and dynamically discovered MPRIS calls via `busctl` for media), never a shell. `quick_settings` remains safely unavailable when no stable local Caelestia IPC action is discoverable. Optional custom actions may be supplied by overriding an action with `{"type":"custom","argv":["program","literal-argument"]}` in `~/.config/tapzones/config.json`; no interpolation is performed.

## Checks and troubleshooting

```bash
python scripts/tapzones.py accel-discover
python -m unittest discover -s tests -v
systemctl --user status tapzones.service
python scripts/tapzones.py status
```

If PipeWire has no source in the current session, diagnostics says `unavailable`; calibration still records accelerometer features. If false positives occur, use **required** accelerometer policy, lower sensitivity, raise confidence, or increase cooldown. Audio is never put in logs.

## Uninstall

```bash
./install.sh uninstall
rm -rf ~/.local/state/tapzones ~/.config/tapzones
```

The second command removes only saved feature profiles/configuration; it does not and cannot remove audio because the plugin never saves any.
