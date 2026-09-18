"""
Interactive command-line simulation runner for CargoResQ.

Triggers deterministic incident lifecycles to demonstrate end-to-end:
1. cold_chain_critical: Refrigerated insulin breakdown with 40m countdown.
2. competing_rescuers: Fresh produce with multiple carriers bidding.
3. hazmat_no_match: Incompatible cargo failure path.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

try:
    import httpx
except ImportError:
    import urllib.request
    import json
    httpx = None  # fallback to standard library


API_URL = "http://localhost:8000"


def print_banner() -> None:
    print("\n========================================================")
    print(" CargoResQ Emergency Rescue Simulation Engine")
    print("========================================================\n")


def run_scenario(scenario_key: str, clock_speed: float = 0.05) -> None:
    print(f"[+] Connecting to CargoResQ backend at {API_URL}...")
    endpoint = f"{API_URL}/api/v1/simulation/run"
    payload = {
        "scenario": scenario_key,
        "seconds_per_simulated_minute": clock_speed,
    }

    try:
        if httpx:
            res = httpx.post(endpoint, json=payload, timeout=10.0)
            status = res.status_code
            body = res.json()
        else:
            req = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                status = resp.status
                body = json.loads(resp.read().decode("utf-8"))

        if status == 200:
            print(f"[OK] Simulation '{scenario_key}' launched successfully!")
            print(f"     Status:     {body.get('status')}")
            print(f"     Clock rate: {body.get('clockSpeed')}")
            print("\n[i] Open the Desktop Operations Console or Driver App to observe:")
            print("    - Real-time incident alert arrival")
            print("    - Spatial candidate scoring & live ETA updates")
            print("    - WebSocket event broadcast on channel 'incident.*'")
            print("    - Condition monitoring & automatic escrow release")
        else:
            print(f"[!] Server returned status {status}: {body}")
    except Exception as exc:
        print(f"[!] Could not trigger simulation: {exc}")
        print("    Ensure the backend is running (`python main.py`) on port 8000.")


def main() -> int:
    print_banner()
    parser = argparse.ArgumentParser(description="Run CargoResQ demo simulation")
    parser.add_argument(
        "--scenario",
        choices=["cold_chain_critical", "competing_rescuers", "hazmat_no_match"],
        default="cold_chain_critical",
        help="Simulation scenario to execute",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=0.05,
        help="Seconds per simulated minute (default: 0.05s)",
    )
    args = parser.parse_args()

    print(f"Selected Scenario: {args.scenario}")
    run_scenario(args.scenario, args.speed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
