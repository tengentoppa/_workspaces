#!/usr/bin/env bash
# sync.sh — 依 .code-workspace 與本地 git repo 狀態，同步更新 manifest.yaml
#
# 掃描 workspaces/*.code-workspace 中的 folders，對每個本地 git repo：
#   1. manifest 尚未收錄 → 自動新增 entry
#   2. remote URL / path / branch 與 manifest 不一致 → 自動更新
#
# 用法：
#   ./sync.sh                 # 同步 workspace → manifest
#   ./sync.sh --dry-run       # 預覽，不實際寫入

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MANIFEST="$SCRIPT_DIR/manifest.yaml"
WORKSPACES_DIR="$SCRIPT_DIR/workspaces"

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      echo "用法: ./sync.sh [--dry-run]"
      echo "  --dry-run   預覽變更，不實際寫入 manifest"
      exit 0
      ;;
  esac
done

[[ -f "$MANIFEST" ]] || { echo "✗ manifest.yaml not found: $MANIFEST" >&2; exit 1; }

# ── 1. 讀取目前 manifest 中已有的 repos ─────────────────
declare -A M_URL M_PATH M_BRANCH
while IFS='|' read -r name url path branch; do
  M_URL["$name"]="$url"
  M_PATH["$name"]="$path"
  M_BRANCH["$name"]="$branch"
done < <(awk '
  /^[[:space:]]*-[[:space:]]+name:/ { in_e=1; name=""; url=""; path=""; branch="" }
  in_e && /name:/    { sub(/.*name:[[:space:]]*/,""); gsub(/[[:space:]]*$/,""); name=$0 }
  in_e && /url:/     { sub(/.*url:[[:space:]]*/,""); gsub(/[[:space:]]*$/,""); url=$0 }
  in_e && /path:/    { sub(/.*path:[[:space:]]*/,""); gsub(/[[:space:]]*$/,""); path=$0 }
  in_e && /branch:/  {
    sub(/.*branch:[[:space:]]*/,""); gsub(/[[:space:]]*$/,""); branch=$0
    print name "|" url "|" path "|" branch
    in_e=0
  }
' "$MANIFEST")

# ── 2. 掃描 workspace files ─────────────────────────────
declare -A SEEN
added=() updated=() unchanged=() skipped=()

for ws_file in "$WORKSPACES_DIR"/*.code-workspace; do
  [[ -f "$ws_file" ]] || continue

  # 從 JSONC 提取 folders 的 name|path
  while IFS='|' read -r fname fpath; do
    # 解析到絕對路徑（資料夾不存在就跳過）
    abs_path="$(cd "$WORKSPACES_DIR" && cd "$fpath" 2>/dev/null && pwd)" || {
      skipped+=("${fname:-$fpath}: dir not found")
      continue
    }

    # 跳過非 git repo
    [[ -d "$abs_path/.git" ]] || { skipped+=("${fname:-$(basename "$abs_path")}: not a git repo"); continue; }

    # 相對於 PROJECT_ROOT 的路徑
    rel_path="${abs_path#$PROJECT_ROOT/}"
    [[ "$rel_path" == "$abs_path" ]] && continue
    rel_path="${rel_path//\\//}"

    # git 資訊
    repo_url=$(git -C "$abs_path" remote get-url origin 2>/dev/null || echo "")
    [[ -z "$repo_url" ]] && { skipped+=("${fname:-$(basename "$abs_path")}: no origin remote"); continue; }
    repo_branch=$(git -C "$abs_path" symbolic-ref --short HEAD 2>/dev/null || echo "main")

    name="${fname:-$(basename "$abs_path")}"

    # 避免重複處理（同 repo 可能出現在多個 workspace）
    [[ -n "${SEEN[$name]:-}" ]] && continue
    SEEN["$name"]=1

    # ── 3. 比對 manifest ──────────────────────────────
    if [[ -z "${M_URL[$name]:-}" ]]; then
      # ── 新 repo：append 到 manifest 尾端 ──
      echo "→ [NEW] $name"
      echo "    url:    $repo_url"
      echo "    path:   $rel_path"
      echo "    branch: $repo_branch"
      added+=("$name")

      if (( ! DRY_RUN )); then
        printf '  - name: %s\n    url: %s\n    path: %s\n    branch: %s\n' \
          "$name" "$repo_url" "$rel_path" "$repo_branch" >> "$MANIFEST"
      fi
    else
      # ── 已存在：逐欄比對 ──
      changes=()
      [[ "${M_URL[$name]}"    != "$repo_url" ]]      && changes+=("url: ${M_URL[$name]} → $repo_url")
      [[ "${M_PATH[$name]}"   != "$rel_path" ]]      && changes+=("path: ${M_PATH[$name]} → $rel_path")
      [[ "${M_BRANCH[$name]}" != "$repo_branch" ]]   && changes+=("branch: ${M_BRANCH[$name]} → $repo_branch")

      if (( ${#changes[@]} > 0 )); then
        echo "→ [UPDATE] $name"
        printf '    %s\n' "${changes[@]}"
        updated+=("$name")

        if (( ! DRY_RUN )); then
          awk -v target="$name" -v url="$repo_url" -v path="$rel_path" -v branch="$repo_branch" '
            /^[[:space:]]*- name:/ {
              s = $0; sub(/.*name:[[:space:]]*/, "", s); gsub(/[[:space:]]*$/, "", s)
              if (s == target) in_t = 1
            }
            in_t && /url:/    { sub(/url:.*/, "url: " url) }
            in_t && /path:/   { sub(/path:.*/, "path: " path) }
            in_t && /branch:/ { sub(/branch:.*/, "branch: " branch); in_t = 0 }
            { print }
          ' "$MANIFEST" > "${MANIFEST}.tmp" && mv "${MANIFEST}.tmp" "$MANIFEST"
        fi
      else
        unchanged+=("$name")
      fi
    fi
  done < <(awk '
    /"folders"/ { in_f = 1 }
    in_f && /"name"/ {
      s = $0
      sub(/.*"name"[[:space:]]*:[[:space:]]*"/, "", s)
      sub(/".*/, "", s)
      name = s
    }
    in_f && /"path"/ {
      s = $0
      sub(/.*"path"[[:space:]]*:[[:space:]]*"/, "", s)
      sub(/".*/, "", s)
      print (name != "" ? name : "") "|" s
      name = ""
    }
    in_f && /^[[:space:]]*\]/ { in_f = 0 }
  ' "$ws_file")
done

# ── Summary ───────────────────────────────────────────────
echo
echo "── sync summary ──"
(( ${#added[@]}     > 0 )) && echo "Added     : ${#added[@]}"     && printf '  + %s\n' "${added[@]}"
(( ${#updated[@]}   > 0 )) && echo "Updated   : ${#updated[@]}"   && printf '  ~ %s\n' "${updated[@]}"
echo "Unchanged : ${#unchanged[@]}"
if (( ${#skipped[@]} > 0 )); then
  echo "Skipped   : ${#skipped[@]}"
  printf '  - %s\n' "${skipped[@]}"
fi
(( DRY_RUN )) && echo "(dry-run — manifest not modified)"
echo "Done."
