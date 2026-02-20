#!/usr/bin/env python3
"""Test /api/message flow: Mistral analyze → direct answer / follow-up / handoff.
Requires: gateway on 18800 (restart gateway after adding /api/message), Ollama with mistral.
Usage: python3 scripts/test_message_flow.py [--gateway URL]
"""
import argparse
import json
import sys

try:
    import httpx
except ImportError:
    print("pip install httpx", file=sys.stderr)
    sys.exit(1)

GATEWAY_URL = "http://localhost:18800"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--gateway", default=GATEWAY_URL, help="Gateway base URL")
    args = p.parse_args()
    base = args.gateway.rstrip("/")

    # 1. Health
    try:
        r = httpx.get(f"{base}/health", timeout=5)
        r.raise_for_status()
        print("1. Gateway health: OK")
    except Exception as e:
        print(f"1. Gateway health: FAIL — {e}")
        sys.exit(1)

    # 2. Route OFF: simple message → expect direct answer (or fallback)
    print("2. POST /api/message (route_to_agents=false, 'hello')...")
    try:
        r = httpx.post(
            f"{base}/api/message",
            json={"message": "Hello, who are you?", "route_to_agents": False},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            print(f"   Error: {data.get('error')}")
        elif "response" in data:
            print(f"   OK: got response (source={data.get('source', '?')}), len={len(data['response'])}")
        else:
            print(f"   Unexpected: {json.dumps(data)[:200]}")
    except Exception as e:
        print(f"   FAIL: {e}")
        sys.exit(1)

    # 3. Route ON: task that likely needs handoff → expect route result or need_follow_up or direct
    print("3. POST /api/message (route_to_agents=true, 'research AI trends')...")
    try:
        r = httpx.post(
            f"{base}/api/message",
            json={"message": "Research recent AI trends in one sentence.", "route_to_agents": True},
            timeout=90,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            print(f"   Error: {data.get('error')}")
        elif data.get("need_follow_up"):
            print(f"   OK: need_follow_up, questions={data.get('questions', [])}")
        elif "response" in data:
            print(f"   OK: direct response (source={data.get('source')})")
        elif "spawned" in data or data.get("capability"):
            print(f"   OK: routed (spawned={data.get('spawned')}, capability={data.get('capability')})")
        else:
            print(f"   OK: {list(data.keys())}")
    except Exception as e:
        print(f"   FAIL: {e}")
        sys.exit(1)

    # 4. Legacy /api/chat still works
    print("4. POST /api/chat (legacy)...")
    try:
        r = httpx.post(
            f"{base}/api/chat",
            json={"message": "Say exactly: legacy chat OK"},
            timeout=30,
        )
        r.raise_for_status()
        d = r.json()
        if d.get("response"):
            print("   OK: legacy /api/chat works")
        else:
            print(f"   Unexpected: {list(d.keys())}")
    except Exception as e:
        print(f"   FAIL: {e}")

    # 5. Legacy /api/route still works
    print("5. POST /api/route (spawn=false, legacy)...")
    try:
        r = httpx.post(
            f"{base}/api/route",
            json={"task": "list files", "spawn": False},
            timeout=30,
        )
        r.raise_for_status()
        d = r.json()
        if "capability" in d or "task_id" in d:
            print("   OK: legacy /api/route works")
        else:
            print(f"   Keys: {list(d.keys())}")
    except Exception as e:
        print(f"   FAIL: {e}")

    print("\nAll message-flow checks done.")


if __name__ == "__main__":
    main()
