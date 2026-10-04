# Caelestia Tap Zones

Tap Zones is a local-only four-corner laptop-tap detector for Caelestia. It is intentionally disabled until calibrated.

## Privacy and architecture

`tapzones.service` reads accelerometer samples from the dynamically found `accel_3d` IIO device (about 10 Hz) and asks PipeWire's `pw-record` for the current default stereo source. Raw 48 kHz PCM is held only in a short in-memory window and is never written, sent, or logged. The persisted XDG state profile contains normalized numeric fingerprints only.

Tap detection and location are separate. Detection stays low-latency on 20 ms windows. Once an impact is accepted, Tap Zones keeps a short ~80–100 ms in-memory stereo snippet around that onset and derives a v2 location fingerprint: GCC-PHAT stereo delay, left/right level ratios, seven normalized frequency bands plus their left/right asymmetry, per-channel spectral centroids, and the early decay envelope. Raw PCM is discarded immediately after the fingerprint is calculated.

The v2 classifier treats location as two separate problems: **left/right** and **top/bottom**. Each axis selects a small robust feature set from the calibration data using leave-one-out scoring, then combines both axis predictions into TL/TR/BL/BR. This matches Mirai's actual microphone data much better than a single four-way distance model. Calibration diagnostics report overall, left/right, and top/bottom validation accuracy separately.

## Trigger policy and calibration readiness

The same trigger policy gates both guided calibration samples and normal tap detection:

- `required` accepts only an accelerometer impulse. If the accelerometer cannot be sampled, it does not substitute microphone audio.
- `preferred` uses the accelerometer whenever it can be sampled. Only while samples are unavailable does it degrade to a conservative microphone transient gate (both sufficient RMS and crest are required).
- `off` intentionally uses that microphone transient gate and ignores the accelerometer gate.

The diagnostics panel reports the active trigger source and whether `preferred` is currently in microphone-only fallback; when accelerometer samples cannot be read, it notes that a reboot may restore accelerometer accuracy. Detection cannot classify a location or run an action until **TL, TR, BL, and BR each contain at least five v2 fingerprints**. Guided calibration defaults to **six taps per corner** so the sixth sample can also be used for leave-one-out quality validation.

## Install, calibrate, use

```bash
./install.sh install
```

Enable `dcqwqc/tapzones` in Caelestia, open its native settings page, keep the laptop in its usual position, and calibrate TL, TR, BL, and BR for the prompted **six taps each**. Tap only when the selected corner says `TAP NOW`; the UI shows the resulting cross-validation quality after all four corners are ready. Then enable detection. Settings cover sensitivity, confidence, accelerometer policy, cooldown, grouping window, calibration count and each zone's one/two/three-tap action.

Built-ins use fixed argv (`wpctl` for volume and dynamically discovered MPRIS calls via `busctl` for media), never a shell. `quick_settings` remains safely unavailable when no stable local Caelestia IPC action is discoverable. Optional custom actions may be supplied by overriding an action with `{"type":"custom","argv":["program","literal-argument"]}` in `~/.config/tapzones/config.json`; no interpolation is performed.

## Checks and troubleshooting

```bash
python scripts/tapzones.py accel-discover
python -m unittest discover -s tests -v
systemctl --user status tapzones.service
python scripts/tapzones.py status
```

If PipeWire has no source in the current session, diagnostics says `unavailable`. `required` can still calibrate and detect from accelerometer impulses; `preferred` fallback and `off` need a microphone transient and therefore wait for a source. If false positives occur, use **required** accelerometer policy, lower sensitivity, raise confidence, or increase cooldown. Audio is never put in logs.

## Optional safe IIO watchdog

`extras/yoga-ish-watchdog-safe.py` is an optional, standalone watchdog for systems whose IIO sensor service occasionally gets stuck. It regards identical stationary accelerometer readings as healthy, never resets Intel ISH or PCI hardware, and does nothing when the accelerometer is missing. Its only recovery action is to restart `iio-sensor-proxy.service` after three consecutive sysfs read failures from an already discovered accelerometer.

```bash
python extras/yoga-ish-watchdog-safe.py --once
```

The script is not installed or enabled by Tap Zones; use it only if you intentionally arrange appropriate service permissions.

## Uninstall

```bash
./install.sh uninstall
rm -rf ~/.local/state/tapzones ~/.config/tapzones
```

The second command removes only saved feature profiles/configuration; it does not and cannot remove audio because the plugin never saves any.

### Live recognition test

The plugin settings include a continuous live classifier playground. Press **Start Live Test** once, then tap TL/TR/BL/BR repeatedly without restarting the test. Each new physical tap immediately replaces the previous prediction, shows its confidence percentage, and highlights the predicted corner with a high-contrast Caelestia success-green card. Consecutive taps on the same predicted corner inside multiTapWindowMs are shown immediately as **1x / 2x DOUBLE / 3x TRIPLE**. The test path skips inactive research-v3 DSP, uses a shorter capture tail, publishes results immediately, and polls the UI at 50 ms while active. Normal mapped actions remain suppressed. Press **Stop Test** only when you are finished.
### Adaptive tap detection

When the accelerometer is unavailable, Tap Zones uses a stateful microphone detector rather than a fixed loudness threshold. It learns the local noise floor from quiet 20 ms windows, requires a fast RMS + peak onset, emits once per rising edge, and will not re-arm until the impact has released. Calibration also waits for quiet after the button press before it accepts a sample, so the UI click/handling noise cannot become the corner sample.

## Research v3: dispersion-aware localization

The optional v3 model is informed by published surface-impact localization work such as **UbiTap / S-UbiTap**, **MM-Tap**, and open-source **SurfaceTap**. Those systems show why a single broadband inter-microphone delay is fragile on rigid surfaces: structural waves are dispersive, so different frequencies arrive at different times and propagation behavior depends on the surface.

Mirai exposes only a 48 kHz two-channel raw DMIC, so Tap Zones does **not** copy UbiTap's 192 kHz / 3+ microphone geometry. Instead v3 keeps v2's robust axis decomposition and adds frequency-specific arrival curves over eight bands, per-band left/right arrival differences, dispersion slopes, and arrival-validity features. It learns the useful dimensions from Mirai's own calibration data.

The **Best calibration** flow deliberately interleaves TL/TR/BL/BR rather than collecting one corner in a long block, reducing time/order drift. It rejects clipped, low-SNR, non-broadband, and strong outlier samples. It starts with eight accepted taps per corner and can extend to twelve. v3 is stored separately in `profile-v3.json` and **cannot replace v2 unless its own leave-one-out validation is at least 90% and at least as good as the currently validated v2 model**. If it does not beat the fallback, v2 stays active.

On Mirai, the real interleaved v3 dataset did not outperform the validated v2-axis model, so the native settings page now recommends **v2-axis calibration** and keeps v3 as research data only. The v3 backend remains safety-gated and can never replace v2 unless it validates at least as well, but it is no longer presented as the normal calibration workflow.
