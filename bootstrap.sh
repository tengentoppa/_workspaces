#!/usr/bin/env bash
# bootstrap.sh — 依 manifest.yaml 把所有來源 repo clone 到正確相對位置。
#
# 用法：
#   cd <Project>/_workspaces
#   ./bootstrap.sh             # 缺的就 clone，已存在則跳過
#   ./bootstrap.sh --fetch     # 已存在的 repo 額外做 fetch --all --prune
#
# 不會切換 branch、不會強制覆寫，避免動到本地未推送的工作。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MANIFEST="$SCRIPT_DIR/manifest.yaml"

DO_FETCH=0
if [[ "${1:-}" == "--fetch" ]]; then
  DO_FETCH=1
fi

if [[ ! -f "$MANIFEST" ]]; then
  echo "manifest.yaml not found at $MANIFEST" >&2
  exit 1
fi

# 用 awk 解析 yaml 的 repos 區段（不依賴 yq，避免要先裝工具）
# 期待格式：每個 entry 連續四行 name/url/path/branch，順序無關。
mapfile -t LINES < <(awk '
  /^[[:space:]]*-[[:space:]]+name:/ { in_entry=1; name=""; url=""; path=""; branch=""; }
  in_entry && /name:/    { sub(/.*name:[[:space:]]*/,""); name=$0 }
  in_entry && /url:/     { sub(/.*url:[[:space:]]*/,""); url=$0 }
  in_entry && /path:/    { sub(/.*path:[[:space:]]*/,""); path=$0 }
  in_entry && /branch:/  {
    sub(/.*branch:[[:space:]]*/,""); branch=$0;
    print name "|" url "|" path "|" branch;
    in_entry=0;
  }
' "$MANIFEST")

missing=()
ok=()
fetched=()

for line in "${LINES[@]}"; do
  IFS='|' read -r name url path branch <<< "$line"
  target="$PROJECT_ROOT/$path"

  if [[ -d "$target/.git" ]]; then
    if (( DO_FETCH )); then
      echo "→ fetch $name ($path)"
      if git -C "$target" fetch --all --prune; then
        fetched+=("$name")
      fi
    else
      echo "✓ exists $name ($path)"
      ok+=("$name")
    fi
    continue
  fi

  echo "→ clone $name → $path (branch: $branch)"
  mkdir -p "$(dirname "$target")"
  if git clone --branch "$branch" "$url" "$target"; then
    ok+=("$name")
  else
    echo "✗ clone failed: $name" >&2
    missing+=("$name")
  fi
done

echo
echo "── summary ──"
echo "OK     : ${#ok[@]}"
(( DO_FETCH )) && echo "Fetched: ${#fetched[@]}"
echo "Missing: ${#missing[@]}"
if (( ${#missing[@]} > 0 )); then
  printf '  - %s\n' "${missing[@]}"
  exit 1
fi
