#!/usr/bin/env bash
# Setup missing GitHub repos for OpenClaw skills
# This script initializes git repos for skills that don't have them yet

set -e

SKILLS_DIR="/Users/ghost/.openclaw/workspace/skills"
GITHUB_ORG="RuneweaverStudios"
SKILLS_WITHOUT_REPOS=("clawhub-publisher" "goals" "remotion-video" "scribe" "skill-doctor")

cd "$SKILLS_DIR"

for skill in "${SKILLS_WITHOUT_REPOS[@]}"; do
    skill_dir="$SKILLS_DIR/$skill"
    if [ ! -d "$skill_dir" ]; then
        echo "[SKIP] $skill: directory not found"
        continue
    fi
    
    if [ -d "$skill_dir/.git" ]; then
        echo "[SKIP] $skill: already has git repo"
        continue
    fi
    
    echo "[SETUP] $skill"
    cd "$skill_dir"
    
    # Initialize git repo
    git init
    git branch -M main
    
    # Create .gitignore if it doesn't exist
    if [ ! -f .gitignore ]; then
        cat > .gitignore << 'EOF'
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
venv/
env/
ENV/
*.egg-info/
dist/
build/

# IDE
.vscode/
.idea/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db

# OpenClaw runtime
*.log
*.state.json
EOF
    fi
    
    # Add all files
    git add .
    
    # Initial commit
    git commit -m "Initial commit: $skill skill"
    
    echo "[DONE] $skill: git repo initialized"
    echo "  Next steps:"
    echo "  1. Create repo on GitHub: https://github.com/new"
    echo "     Name: $skill"
    echo "     Owner: $GITHUB_ORG"
    echo "     Description: $(grep -m1 '"description"' _meta.json 2>/dev/null | sed 's/.*"description": *"\([^"]*\)".*/\1/' || echo 'OpenClaw skill')"
    echo "     Visibility: Public"
    echo "     DO NOT initialize with README, .gitignore, or license"
    echo "  2. Add remote and push:"
    echo "     cd $skill_dir"
    echo "     git remote add origin https://github.com/$GITHUB_ORG/$skill.git"
    echo "     git push -u origin main"
    echo ""
done

echo ""
echo "Summary:"
echo "  Skills processed: ${#SKILLS_WITHOUT_REPOS[@]}"
echo "  Check each skill directory for git status"
