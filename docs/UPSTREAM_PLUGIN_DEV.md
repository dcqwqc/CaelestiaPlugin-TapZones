# Native Caelestia plugin development migration

Upstream target: https://github.com/caelestia-dots/shell/pull/1703 (experimental / unmerged). The original default branch is preserved.

- Identity: `dcqwqc/TapZones`; minimum upstream development shell: >=2.1.0.
- Entry types: custom.
- Settings: Settings.qml; custom settings UI: SettingsUi.qml.
- Upstream parser supports custom, bar-entry, bar-popout, quick-toggle, status-icon, dashboard-tab. Only bar/popout and quick toggle integrations are currently marked working. Custom entries and legacy slot placements require explicit hosting and must be verified.

## Required test gates
1. Isolated build from PR #1703, no manifest/QML errors, existing entry-point source paths resolvable.
2. Actual visible UI placements, plugin settings persistence, enable/disable/reload, service and hardware behavior.
3. Keep a complete previous plugin directory/config backup and an automatic rollback mechanism before any live deployment.

**State:** Native-development version gate added; plugin remains untested and undeployed.
