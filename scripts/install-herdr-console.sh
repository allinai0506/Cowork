#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
source_dir="${repo_root}/console"
target_dir="${HOME}/.herdr-console"

mkdir -p "${target_dir}"
install -m 755 "${source_dir}/herdr_factory_console.py" "${target_dir}/herdr_factory_console.py"
install -m 755 "${source_dir}/launcher.applescript" "${target_dir}/launcher.applescript"

if [[ "${1:-}" != "--no-restart" ]]; then
  launchctl kickstart -k "gui/$(id -u)/com.user.herdr-factory-console"
fi

echo "Herdr Factory Console synced to ${target_dir}"
