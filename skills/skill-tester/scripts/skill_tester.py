#!/usr/bin/env python3
"""
OpenClaw Skill Tester — Run local tests for each skill before publishing to ClawHub.

Discovers skills in workspace/skills, runs configured test commands (or heuristics),
and reports pass/fail. Use before uploading to ClawHub to ensure all skills work.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


def _openclaw_home():
    return Path(os.environ.get("OPENCLAW_HOME") or os.path.expanduser("~/.openclaw"))


def _skills_dir():
    return _openclaw_home() / "workspace" / "skills"


def _test_config_path():
    return Path(__file__).resolve().parent / "skill_tests.json"


def discover_skills():
    """Return list of skill dirs that look like skills (have SKILL.md or _meta.json)."""
    skills_root = _skills_dir()
    if not skills_root.exists():
        return []
    out = []
    for name in sorted(skills_root.iterdir()):
        if not name.is_dir():
            continue
        if name.name.startswith("."):
            continue
        if (name / "SKILL.md").exists() or (name / "_meta.json").exists():
            out.append(name.name)
    return out


def load_test_config():
    """Load skill_tests.json: map skill_slug -> list of {cmd, cwd?, env?, expect_exit?, expect_json_keys?}."""
    p = _test_config_path()
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def run_cmd(cmd, cwd=None, env=None, timeout=30):
    """Run command; return (exit_code, stdout, stderr)."""
    cwd = Path(cwd) if cwd else _openclaw_home()
    env = env or os.environ.copy()
    env.setdefault("OPENCLAW_HOME", str(_openclaw_home()))
    try:
        r = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError:
        return -1, "", "command not found"


def run_tests_for_skill(skill_slug, test_config, skills_root):
    """Run all test entries for one skill. Return list of {name, passed, message}."""
    results = []
    skill_dir = skills_root / skill_slug
    tests = test_config.get(skill_slug, [])
    if not tests:
        # Heuristic: try scripts with --help or --json
        scripts_dir = skill_dir / "scripts"
        if scripts_dir.exists():
            for py in scripts_dir.glob("*.py"):
                code, out, err = run_cmd(
                    [sys.executable, str(py), "--help"],
                    cwd=skill_dir,
                    timeout=10,
                )
                if code in (0, 2):  # many use 2 for no args
                    results.append({"name": f"{py.name} --help", "passed": True, "message": f"exit {code}"})
                else:
                    code2, out2, err2 = run_cmd(
                        [sys.executable, str(py), "--json"],
                        cwd=skill_dir,
                        timeout=10,
                    )
                    if code2 == 0 and out2.strip().startswith("{"):
                        results.append({"name": f"{py.name} --json", "passed": True, "message": "JSON ok"})
                    else:
                        results.append({"name": str(py.name), "passed": False, "message": f"--help exit {code}, --json exit {code2}"})
        if not results:
            results.append({"name": "no tests", "passed": True, "message": "no test config; no scripts"})
        return results

    for i, t in enumerate(tests):
        name = t.get("name", f"test_{i}")
        cmd = t.get("cmd")
        if isinstance(cmd, str):
            cmd = ["sh", "-c", cmd]
        cwd = t.get("cwd")
        if cwd:
            cwd = skill_dir / cwd if not Path(cwd).is_absolute() else cwd
        else:
            cwd = skill_dir
        expect_exit = t.get("expect_exit", 0)
        expect_json_keys = t.get("expect_json_keys", [])
        timeout = t.get("timeout", 25)
        code, out, err = run_cmd(cmd, cwd=cwd, timeout=timeout)
        passed = code == expect_exit
        if passed and expect_json_keys and out.strip():
            try:
                data = json.loads(out.strip())
                for k in expect_json_keys:
                    if k not in data:
                        passed = False
                        break
            except json.JSONDecodeError:
                passed = False
        msg = f"exit {code}" + (f", has {expect_json_keys}" if expect_json_keys else "")
        if not passed:
            msg = f"expected exit {expect_exit}, got {code}; " + (err or out or "")[:200]
        results.append({"name": name, "passed": passed, "message": msg})
    return results


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Test OpenClaw skills locally")
    ap.add_argument("--skill", type=str, help="Test only this skill (slug)")
    ap.add_argument("--all", action="store_true", help="Test all discovered skills")
    ap.add_argument("--list", action="store_true", help="List discoverable skills and exit")
    ap.add_argument("--json", action="store_true", help="Output JSON report")
    ap.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = ap.parse_args()

    skills_root = _skills_dir()
    if not skills_root.exists():
        print("Skills dir not found:", skills_root, file=sys.stderr)
        sys.exit(2)
    discovered = discover_skills()
    config = load_test_config()

    if args.list:
        if args.json:
            print(json.dumps({"skills": discovered, "config_skills": list(config.keys())}))
        else:
            print("Discovered skills:", ", ".join(discovered))
        sys.exit(0)

    to_test = [args.skill] if args.skill else (discovered if args.all else [])
    if not to_test:
        print("Use --skill SLUG or --all to run tests. Use --list to see skills.", file=sys.stderr)
        sys.exit(2)

    report = {"skills": {}, "passed": 0, "failed": 0}
    for slug in to_test:
        if slug not in discovered:
            report["skills"][slug] = {"passed": False, "tests": [{"name": "?", "passed": False, "message": "skill not found"}]}
            report["failed"] += 1
            continue
        tests = run_tests_for_skill(slug, config, skills_root)
        all_passed = all(t["passed"] for t in tests)
        report["skills"][slug] = {"passed": all_passed, "tests": tests}
        if all_passed:
            report["passed"] += 1
        else:
            report["failed"] += 1
        if args.verbose or not all_passed:
            status = "PASS" if all_passed else "FAIL"
            print(f"[{status}] {slug}")
            for t in tests:
                sym = "✓" if t["passed"] else "✗"
                print(f"  {sym} {t['name']}: {t['message']}")

    if args.json:
        print(json.dumps(report, indent=2))
    elif not args.verbose:
        for slug, data in report["skills"].items():
            status = "PASS" if data["passed"] else "FAIL"
            print(f"[{status}] {slug}")

    sys.exit(0 if report["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
