# skill-tester

**OpenClaw skill: test all skills locally before ClawHub upload.**

Discovers skills in `workspace/skills`, runs configured tests (or heuristics), and reports pass/fail. Add test entries in `scripts/skill_tests.json` to define exactly what to run per skill.

## Quick start

```bash
# List skills
python3 workspace/skills/skill-tester/scripts/skill_tester.py --list

# Test all skills
python3 workspace/skills/skill-tester/scripts/skill_tester.py --all -v

# Test one skill
python3 workspace/skills/skill-tester/scripts/skill_tester.py --skill agent-swarm --json

# JSON report (for CI / sub-agent)
python3 workspace/skills/skill-tester/scripts/skill_tester.py --all --json
```

## Config

Edit `scripts/skill_tests.json` to add or change tests. Example:

```json
{
  "my-skill": [
    {
      "name": "main script",
      "cmd": ["python3", "scripts/main.py", "--json"],
      "expect_exit": 0,
      "expect_json_keys": ["ok"],
      "timeout": 15
    }
  ]
}
```

Exit code: 0 = all passed, 1 = at least one failed, 2 = usage/setup error.
