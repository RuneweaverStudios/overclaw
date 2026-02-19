#!/usr/bin/env python3
"""
ClawHub Publisher - Sync local OpenClaw skills to ClawHub.

- Scans workspace/skills for directories with .git and SKILL.md or _meta.json.
- Keeps only skills whose git origin URL contains the configured GitHub org (user's repos).
- For each: checks if skill exists on ClawHub and current version; publishes first or latest version.
- Uses delay between publishes to avoid rate limits.
- Writes completion report to OPENCLAW_HOME/logs.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def openclaw_home() -> Path:
    return Path(os.environ.get("OPENCLAW_HOME", os.path.expanduser("~/.openclaw")))


def load_config(openclaw_home_path: Path) -> Dict[str, Any]:
    cfg = {"githubOrg": "RuneweaverStudios", "delaySeconds": 15, "logDir": "logs"}
    skill_config = Path(__file__).resolve().parent.parent / "config.json"
    if skill_config.exists():
        try:
            with open(skill_config) as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    if os.environ.get("CLAWHUB_PUBLISHER_GITHUB_ORG"):
        cfg["githubOrg"] = os.environ["CLAWHUB_PUBLISHER_GITHUB_ORG"]
    return cfg


def get_git_remote_origin(skill_path: Path) -> Optional[str]:
    try:
        r = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=skill_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0 and r.stdout:
            return r.stdout.strip()
    except Exception:
        pass
    return None


def is_user_skill(origin_url: Optional[str], github_org: str) -> bool:
    if not origin_url:
        return False
    org_lower = github_org.lower()
    url_lower = origin_url.lower()
    return org_lower in url_lower or f"github.com/{org_lower}/" in url_lower


def get_slug(skill_path: Path) -> str:
    # Use directory name as ClawHub slug (matches repo/skill id)
    return skill_path.name


def get_local_version(skill_path: Path) -> str:
    v = "1.0.0"
    meta = skill_path / "_meta.json"
    if meta.exists():
        try:
            with open(meta) as f:
                v = json.load(f).get("version", v)
                if v:
                    return v
        except Exception:
            pass
    skill_md = skill_path / "SKILL.md"
    if skill_md.exists():
        try:
            text = skill_md.read_text()
            m = re.search(r"^version:\s*([^\s'\"]+)", text, re.MULTILINE | re.IGNORECASE)
            if m:
                return m.group(1).strip()
        except Exception:
            pass
    return "1.0.0"


def parse_semver(s: str) -> Tuple[int, int, int]:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", s.strip())
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return (0, 0, 0)


def semver_gt(a: str, b: str) -> bool:
    va, vb = parse_semver(a), parse_semver(b)
    return va > vb


def bump_patch(ver: str) -> str:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(.*)", ver.strip())
    if m:
        return f"{m.group(1)}.{m.group(2)}.{int(m.group(3)) + 1}{m.group(4)}"
    return "1.0.1"


def clawhub_inspect_versions(slug: str) -> Optional[Dict[str, Any]]:
    try:
        r = subprocess.run(
            ["clawhub", "inspect", slug, "--versions", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode != 0:
            return None
        return json.loads(r.stdout)
    except Exception:
        pass
    return None


def clawhub_publish(skill_path: Path, version: str) -> Tuple[bool, str]:
    try:
        r = subprocess.run(
            ["clawhub", "publish", str(skill_path.resolve()), "--version", version],
            capture_output=True,
            text=True,
            timeout=120,
        )
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0:
            if "Version already exists" in out:
                return False, "version_exists"
            if "Rate limit" in out or "rate limit" in out.lower():
                return False, "rate_limit"
            return False, out[:500]
        return True, out
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)


def discover_skills(skills_dir: Path, github_org: str) -> List[Dict[str, Any]]:
    skills = []
    if not skills_dir.exists():
        return skills
    for d in sorted(skills_dir.iterdir()):
        if not d.is_dir():
            continue
        if d.name.startswith("."):
            continue
        if not (d / "SKILL.md").exists() and not (d / "_meta.json").exists():
            continue
        # Include skills with or without .git (e.g. after stripping .git for ClawHub upload)
        origin = get_git_remote_origin(d) if (d / ".git").exists() else None
        if origin is not None and not is_user_skill(origin, github_org):
            continue
        slug = get_slug(d)
        local_version = get_local_version(d)
        skills.append({
            "path": d,
            "slug": slug,
            "local_version": local_version,
            "origin": origin,
        })
    return skills


def run_sync(
    skills_dir: Path,
    openclaw_home_path: Path,
    config: Dict[str, Any],
    dry_run: bool = False,
    delay_sec: Optional[int] = None,
    initial_delay_sec: Optional[int] = None,
) -> Dict[str, Any]:
    delay = delay_sec if delay_sec is not None else config.get("delaySeconds", 20)
    initial_delay = initial_delay_sec if initial_delay_sec is not None else config.get("initialDelaySeconds", 30)
    github_org = config.get("githubOrg", "RuneweaverStudios")
    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "skills_checked": 0,
        "published": [],
        "skipped_up_to_date": [],
        "skipped_new_published": [],
        "failed": [],
        "rate_limited": False,
    }
    skills = discover_skills(skills_dir, github_org)
    report["skills_checked"] = len(skills)
    log_lines = [f"ClawHub sync started. Skills: {len(skills)}"]

    if not dry_run and initial_delay > 0 and skills:
        log_lines.append(f"Waiting {initial_delay}s before first request (rate-limit avoidance)...")
        time.sleep(initial_delay)

    for i, s in enumerate(skills):
        path, slug, local_version = s["path"], s["slug"], s["local_version"]
        log_lines.append(f"[{slug}] local={local_version}")

        existing = clawhub_inspect_versions(slug)
        if not dry_run and existing is not None:
            time.sleep(2)
        if existing is None:
            # New skill: publish with local version
            if dry_run:
                report["skipped_new_published"].append({"slug": slug, "version": local_version})
                log_lines.append(f"  dry-run: would publish {local_version}")
                continue
            ok, msg = clawhub_publish(path, local_version)
            if ok:
                report["published"].append({"slug": slug, "version": local_version})
                log_lines.append(f"  published {local_version}")
            elif msg == "version_exists":
                bumped = bump_patch(local_version)
                ok2, msg2 = clawhub_publish(path, bumped)
                if ok2:
                    report["published"].append({"slug": slug, "version": bumped})
                    log_lines.append(f"  published {bumped} (after exists)")
                else:
                    report["failed"].append({"slug": slug, "reason": msg2})
                    log_lines.append(f"  fail: {msg2[:200]}")
            elif msg == "rate_limit":
                report["rate_limited"] = True
                report["failed"].append({"slug": slug, "reason": "rate_limit"})
                log_lines.append("  rate limited - stop")
                break
            else:
                report["failed"].append({"slug": slug, "reason": msg})
                log_lines.append(f"  fail: {msg[:200]}")
        else:
            latest = (existing.get("latestVersion") or {}).get("version") or (existing.get("versions") or [{}])[0].get("version")
            if not latest:
                latest = "0.0.0"
            if semver_gt(local_version, latest) or local_version != latest:
                # Publish next version: use local if it's newer, else bump ClawHub latest
                use_version = local_version if semver_gt(local_version, latest) else bump_patch(latest)
                if dry_run:
                    report["skipped_new_published"].append({"slug": slug, "version": use_version})
                    log_lines.append(f"  dry-run: would publish {use_version} (ClawHub latest={latest})")
                    continue
                ok, msg = clawhub_publish(path, use_version)
                if ok:
                    report["published"].append({"slug": slug, "version": use_version})
                    log_lines.append(f"  published {use_version} (ClawHub had {latest})")
                elif msg == "rate_limit":
                    report["rate_limited"] = True
                    report["failed"].append({"slug": slug, "reason": "rate_limit"})
                    log_lines.append("  rate limited - stop")
                    break
                else:
                    report["failed"].append({"slug": slug, "reason": msg})
                    log_lines.append(f"  fail: {msg[:200]}")
            else:
                report["skipped_up_to_date"].append({"slug": slug, "version": local_version})
                log_lines.append(f"  up-to-date ({latest})")

        if not dry_run and i < len(skills) - 1 and (report["published"] or report["failed"]):
            time.sleep(delay)

    log_lines.append(f"Done. Published: {len(report['published'])}, up-to-date: {len(report['skipped_up_to_date'])}, failed: {len(report['failed'])}")
    report["log"] = "\n".join(log_lines)

    log_dir = openclaw_home_path / config.get("logDir", "logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "clawhub-publisher.log"
    with open(log_file, "a") as f:
        f.write("\n--- " + report["timestamp"] + " ---\n")
        f.write(report["log"] + "\n")
    summary_file = log_dir / "clawhub-publisher-last.json"
    with open(summary_file, "w") as f:
        json.dump({k: v for k, v in report.items() if k != "log"}, f, indent=2)
    return report


def main():
    ap = argparse.ArgumentParser(description="Sync local OpenClaw skills to ClawHub")
    ap.add_argument("--skills-dir", type=Path, default=None, help="Skills directory (default: OPENCLAW_HOME/workspace/skills)")
    ap.add_argument("--delay", type=int, default=None, help="Seconds between skills (default: from config or 20)")
    ap.add_argument("--initial-delay", type=int, default=None, help="Seconds to wait before first request (default: 30)")
    ap.add_argument("--dry-run", action="store_true", help="Do not publish, only report what would be done")
    ap.add_argument("--json", action="store_true", help="Print only JSON report to stdout")
    args = ap.parse_args()

    home = openclaw_home()
    config = load_config(home)
    skills_dir = args.skills_dir or (home / "workspace" / "skills")

    report = run_sync(
        skills_dir, home, config,
        dry_run=args.dry_run,
        delay_sec=args.delay,
        initial_delay_sec=getattr(args, "initial_delay", None),
    )

    if args.json:
        print(json.dumps({k: v for k, v in report.items() if k != "log"}))
    else:
        print(report["log"])

    if report["failed"] and not report["rate_limited"]:
        sys.exit(1)
    if report["rate_limited"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
