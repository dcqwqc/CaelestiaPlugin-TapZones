#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; dest="$HOME/.local/share/caelestia/plugins/tapzones"; unit="$HOME/.config/systemd/user/tapzones.service"
case "${1:-install}" in
install) mkdir -p "$(dirname "$dest")" "$(dirname "$unit")"; if [ -e "$dest" ] && [ ! -L "$dest" ]; then echo "refusing to replace non-symlink $dest" >&2; exit 1; fi; ln -sfn "$root" "$dest"; ln -sfn "$root/systemd/tapzones.service" "$unit"; systemctl --user daemon-reload; systemctl --user enable --now tapzones.service;;
uninstall) systemctl --user disable --now tapzones.service 2>/dev/null || true; rm -f "$unit"; [ -L "$dest" ] && rm -f "$dest"; systemctl --user daemon-reload;;
*) echo "usage: $0 [install|uninstall]" >&2;exit 2;; esac
