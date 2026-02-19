# GitHub Repo Setup Status

## ✅ Skills with GitHub Repos (Already Pushed)

All these skills have git repos initialized and remotes pointing to GitHub:

- agent-swarm
- agent-swarm-claude-only
- agent-swarm-local
- auto-clipper
- blog-generator
- chat-history-analyzer
- composio-composer-xskill
- cron-health-check
- doppleganger
- FACEPALM
- funclip-commander
- gateway-guard
- gateway-watchdog
- launchagent-manager
- project-manager-agent
- self-optimizer
- skill-doc-formatter
- skill-tester
- subagent-dashboard
- subagent-tracker
- tweet-crafter
- what-just-happened
- youtube_downloads

## 🔄 Skills Ready to Push (Git Initialized, Need GitHub Repos)

These skills have git repos initialized locally but need GitHub repos created and remotes added:

### 1. **goals**
- **Description:** Determines user goals from the Notes folder (scribe), categorizes into personal/professional and short/long-term, and produces a morning action plan with motivation and inspiration.
- **Git Status:** ✅ Initialized, committed
- **Action Required:**
  1. Create repo: https://github.com/new
     - Name: `goals`
     - Owner: `RuneweaverStudios`
     - Description: "Determines user goals from the Notes folder (scribe), categorizes into personal/professional and short/long-term, and produces a morning action plan with motivation and inspiration."
     - Visibility: Public
     - **DO NOT** initialize with README, .gitignore, or license
  2. Add remote and push:
     ```bash
     cd /Users/ghost/.openclaw/workspace/skills/goals
     git remote add origin https://github.com/RuneweaverStudios/goals.git
     git push -u origin main
     ```

### 2. **remotion-video**
- **Description:** Create and render programmatic videos with Remotion (React-based MP4/PNG). Scaffold projects, run Studio, render locally or via Lambda.
- **Git Status:** ✅ Initialized, committed
- **Action Required:**
  1. Create repo: https://github.com/new
     - Name: `remotion-video`
     - Owner: `RuneweaverStudios`
     - Description: "Create and render programmatic videos with Remotion (React-based MP4/PNG). Scaffold projects, run Studio, render locally or via Lambda."
     - Visibility: Public
     - **DO NOT** initialize with README, .gitignore, or license
  2. Add remote and push:
     ```bash
     cd /Users/ghost/.openclaw/workspace/skills/remotion-video
     git remote add origin https://github.com/RuneweaverStudios/remotion-video.git
     git push -u origin main
     ```

### 3. **scribe**
- **Description:** Scans OpenClaw logs, config files, chat history, cursor history, behavior, desires, tastes, and drafts to take comprehensive daily and weekly notes with summaries.
- **Git Status:** ✅ Initialized, committed
- **Action Required:**
  1. Create repo: https://github.com/new
     - Name: `scribe`
     - Owner: `RuneweaverStudios`
     - Description: "Scans OpenClaw logs, config files, chat history, cursor history, behavior, desires, tastes, and drafts to take comprehensive daily and weekly notes with summaries."
     - Visibility: Public
     - **DO NOT** initialize with README, .gitignore, or license
  2. Add remote and push:
     ```bash
     cd /Users/ghost/.openclaw/workspace/skills/scribe
     git remote add origin https://github.com/RuneweaverStudios/scribe.git
     git push -u origin main
     ```

### 4. **skill-doctor**
- **Description:** Scans the skills folder to detect new, unused, or missing dependencies, fix them, and test a skill in or out of sandbox.
- **Git Status:** ✅ Initialized, committed
- **Action Required:**
  1. Create repo: https://github.com/new
     - Name: `skill-doctor`
     - Owner: `RuneweaverStudios`
     - Description: "Scans the skills folder to detect new, unused, or missing dependencies, fix them, and test a skill in or out of sandbox."
     - Visibility: Public
     - **DO NOT** initialize with README, .gitignore, or license
  2. Add remote and push:
     ```bash
     cd /Users/ghost/.openclaw/workspace/skills/skill-doctor
     git remote add origin https://github.com/RuneweaverStudios/skill-doctor.git
     git push -u origin main
     ```

### 5. **clawhub-publisher**
- **Description:** Syncs OpenClaw skills to ClawHub. Scans local skills that are developed by the user (GitHub remote matches configured org), checks existing versions on ClawHub, and publishes first or latest version with rate-limit delays.
- **Git Status:** ⚠️ Has git repo but may need remote check
- **Action Required:**
  ```bash
  cd /Users/ghost/.openclaw/workspace/skills/clawhub-publisher
  git remote -v
  # If no remote, add:
  git remote add origin https://github.com/RuneweaverStudios/clawhub-publisher.git
  git push -u origin main
  ```

## 📝 Quick Setup Script

After creating the GitHub repos, you can run this to add remotes and push all at once:

```bash
#!/bin/bash
cd /Users/ghost/.openclaw/workspace/skills

for skill in goals remotion-video scribe skill-doctor; do
    cd "$skill"
    if ! git remote | grep -q origin; then
        git remote add origin "https://github.com/RuneweaverStudios/$skill.git"
        git push -u origin main
    fi
    cd ..
done
```

## 📊 Summary

- **Total skills:** 28
- **With GitHub repos:** 23 ✅
- **Ready to push (need GitHub repos created):** 4
- **Needs remote check:** 1 (clawhub-publisher)

---

**Last updated:** $(date)
