#!/usr/bin/env python3
"""Patch Stop-hook mulch command in all worktrees so mulch learn runs only when .mulch/mulch.config.yaml exists (fixes ENOENT in worktrees)."""
from pathlib import Path
import json
import sys

WORKSPACE = Path(__file__).resolve().parent.parent
WORKTREES_DIR = WORKSPACE / ".overstory" / "worktrees"
MULCH_SAFE = "command -v mulch >/dev/null 2>&1 && test -f .mulch/mulch.config.yaml && [ -z \"${OVERSTORY_AGENT_NAME}\" ] && (pwd | grep -q '.overstory/worktrees' || mulch learn) || true"
# For writing into JSON: escape double-quotes so the value is valid JSON
MULCH_SAFE_JSON = MULCH_SAFE.replace('"', '\\"')


def patch_file(path: Path, old: str, new: str) -> bool:
    text = path.read_text()
    if old not in text:
        return False
    path.write_text(text.replace(old, new, 1))
    return True


def main() -> int:
    if not WORKTREES_DIR.is_dir():
        print("No worktrees dir:", WORKTREES_DIR)
        return 0
    patched = 0
    for wt in WORKTREES_DIR.iterdir():
        if not wt.is_dir():
            continue
        # .overstory/hooks.json: "mulch learn" or previous safe -> current safe
        hooks_json = wt / ".overstory" / "hooks.json"
        if hooks_json.exists():
            text = hooks_json.read_text()
            if MULCH_SAFE not in text:
                prev_safe = "command -v mulch >/dev/null 2>&1 && test -f .mulch/mulch.config.yaml && (pwd | grep -q '.overstory/worktrees' || mulch learn) || true"
                if patch_file(hooks_json, '"command": "mulch learn"', f'"command": "{MULCH_SAFE_JSON}"'):
                    patched += 1
                    print("Patched", hooks_json)
                elif prev_safe in text and patch_file(hooks_json, prev_safe, MULCH_SAFE_JSON):
                    patched += 1
                    print("Patched", hooks_json)
        # .claude/settings.local.json: "[ -z \"$OVERSTORY_AGENT_NAME\" ] && exit 0; mulch learn" -> safe
        settings_json = wt / ".claude" / "settings.local.json"
        if settings_json.exists():
            # In file the quotes inside the value are escaped as \"
            old = '[ -z \\"$OVERSTORY_AGENT_NAME\\" ] && exit 0; mulch learn'
            new = f'[ -z \\"$OVERSTORY_AGENT_NAME\\" ] && exit 0; {MULCH_SAFE}'
            if patch_file(settings_json, old, new):
                print("Patched", settings_json)
                patched += 1
    print("Patched", patched, "files across worktrees.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
