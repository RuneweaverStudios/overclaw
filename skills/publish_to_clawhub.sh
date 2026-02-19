#!/usr/bin/env bash
# Publish all skills that have a git repo to ClawHub, with delay between each to avoid rate limiting.
# Usage: ./publish_to_clawhub.sh [SKILLS_DIR] [DELAY_SEC]
# Requires: clawhub CLI, valid semver in _meta.json (version) or SKILL.md frontmatter.
set -e
SKILLS_DIR="${1:-/Users/ghost/.openclaw/workspace/skills}"
DELAY_SEC="${2:-15}"
cd "$SKILLS_DIR"
published=0
failed=()

get_version() {
  local dir="$1"
  local v="1.0.0"
  if [ -f "$dir/_meta.json" ]; then
    v=$(python3 -c "import json, sys; p=sys.argv[1]; print(json.load(open(p)).get('version','1.0.0'))" "$dir/_meta.json" 2>/dev/null) || v="1.0.0"
  fi
  if [ "$v" = "1.0.0" ] && [ -f "$dir/SKILL.md" ]; then
    v=$(grep -m1 "^version:" "$dir/SKILL.md" | sed 's/version:[[:space:]]*//;s/["'\'' ]//g') || true
    [ -z "$v" ] && v="1.0.0"
  fi
  echo "$v"
}

bump_patch() {
  local v="$1"
  if [[ "$v" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
    echo "${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.$((BASH_REMATCH[3] + 1))"
  else
    echo "1.0.1"
  fi
}

for d in */; do
  name="${d%/}"
  [ ! -d "$name/.git" ] && continue
  skill_path="$(cd "$name" && pwd)"
  version=$(get_version "$skill_path")
  echo "[$name] Publishing version $version..."
  out=$(clawhub publish "$skill_path" --version "$version" 2>&1) || true
  if echo "$out" | grep -q "Version already exists"; then
    version=$(bump_patch "$version")
    echo "[$name] Version exists, trying $version..."
    out=$(clawhub publish "$skill_path" --version "$version" 2>&1) || true
  fi
  if echo "$out" | grep -q "Rate limit"; then
    echo "[$name] Rate limited - wait and re-run script later for remaining skills."
    failed+=("$name")
  elif echo "$out" | grep -q "Error:"; then
    echo "$out" | head -5
    failed+=("$name")
  else
    echo "[$name] OK"
    ((published++)) || true
    echo "Waiting ${DELAY_SEC}s before next..."
    sleep "$DELAY_SEC"
  fi
done
echo "Published: $published"
[ ${#failed[@]} -eq 0 ] || { echo "Failed or skipped: ${failed[*]}"; exit 1; }
