#!/usr/bin/env bash
# Push skills to GitHub after creating repos
# Run this AFTER creating the GitHub repos via web UI

set -e

SKILLS_DIR="/Users/ghost/.openclaw/workspace/skills"
GITHUB_ORG="RuneweaverStudios"

cd "$SKILLS_DIR"

# Skills that need GitHub repos created and pushed
SKILLS_TO_PUSH=("goals" "remotion-video" "scribe" "skill-doctor")

for skill in "${SKILLS_TO_PUSH[@]}"; do
    skill_dir="$SKILLS_DIR/$skill"
    if [ ! -d "$skill_dir" ]; then
        echo "[SKIP] $skill: directory not found"
        continue
    fi
    
    if [ ! -d "$skill_dir/.git" ]; then
        echo "[SKIP] $skill: no git repo"
        continue
    fi
    
    cd "$skill_dir"
    
    # Check if remote exists
    if git remote | grep -q origin; then
        remote_url=$(git remote get-url origin)
        echo "[CHECK] $skill: remote exists ($remote_url)"
        
        # Verify it's the correct remote
        expected_url="https://github.com/$GITHUB_ORG/$skill.git"
        if [ "$remote_url" != "$expected_url" ] && [ "$remote_url" != "$expected_url/.git" ]; then
            echo "  ⚠️  Remote URL mismatch. Expected: $expected_url"
            echo "  Fixing remote..."
            git remote set-url origin "$expected_url"
        fi
    else
        echo "[SETUP] $skill: adding remote"
        git remote add origin "https://github.com/$GITHUB_ORG/$skill.git"
    fi
    
    # Check if already pushed
    if git ls-remote --heads origin main 2>/dev/null | grep -q main; then
        echo "[SKIP] $skill: already pushed to GitHub"
    else
        echo "[PUSH] $skill: pushing to GitHub..."
        git push -u origin main || {
            echo "  ❌ Push failed. Make sure the GitHub repo exists:"
            echo "     https://github.com/$GITHUB_ORG/$skill"
            echo "     Create it at: https://github.com/new"
        }
    fi
    
    cd ..
    echo ""
done

echo "Done! Check any errors above."
