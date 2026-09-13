#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"

skills=(
  "six-step-finish"
  "knowledge-capture"
)

for skill in "${skills[@]}"; do
  source_dir="${repo_root}/.agents/skills/${skill}"
  target_dir="${HOME}/.agents/skills/${skill}"

  if [[ ! -d "${source_dir}" ]]; then
    echo "警告: 仓库内技能 ${source_dir} 不存在，跳过"
    continue
  fi

  mkdir -p "${target_dir}"
  cp -R "${source_dir}/." "${target_dir}/"
  echo "已同步技能 ${skill} → ${target_dir}"
done

echo "全部技能同步完成"
