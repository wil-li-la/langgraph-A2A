"""Verify the refactored navigate_skill by sending a small forward goal.

Usage:
    cd backend && source .venv/bin/activate
    python test_navigate_forward.py            # 0.3m forward from origin
    python test_navigate_forward.py 0.5        # 0.5m forward from origin
    python test_navigate_forward.py 0.3 0.0 0.0  # explicit x y theta

This calls `app.tools.stretch_tools.navigate_skill(x, y, theta)` — the same
function the workflow and the LLM agent use. The goal is absolute (the
robot's nav frame), so "forward a little bit" assumes the robot is at or
near its origin and facing +x. If you've teleop'd it elsewhere, pass
coordinates that match your current pose + a small offset.

Safety:
- Make sure the floor in front of the robot is clear.
- Have the driver running on the robot (`uv run python -m stretch3_zmq.driver
  --config config.yaml`) and the lab nav_service reachable.
- The script first probes the goto port with a 1s timeout before sending
  the goal so you don't hang forever if anything is down.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from urllib.parse import urlparse

# Load .env so ROBOT_IP is picked up without requiring it on the CLI.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from app.tools.stretch_tools import SERVER_IP, get_config, navigate_skill


def _probe(host: str, port: int, timeout_s: float = 1.0) -> bool:
    """Quick TCP probe to confirm something is listening on the goto port."""
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError as e:
        print(f"  ✗ probe failed: {e}", file=sys.stderr)
        return False


def main() -> int:
    args = sys.argv[1:]
    if len(args) == 0:
        x, y, theta = 0.3, 0.0, 0.0
    elif len(args) == 1:
        x, y, theta = float(args[0]), 0.0, 0.0
    elif len(args) == 3:
        x, y, theta = float(args[0]), float(args[1]), float(args[2])
    else:
        print(__doc__)
        return 2

    cfg = get_config()
    goto_port = cfg.ports["goto"]
    print(f"Robot: tcp://{SERVER_IP}:{goto_port}")
    print(f"Goal:  x={x:.3f} y={y:.3f} theta={theta:.3f} (absolute, nav frame)")

    print("Probing goto port...", end=" ", flush=True)
    if not _probe(SERVER_IP, goto_port):
        print("FAIL — is the driver running on the robot?", file=sys.stderr)
        return 1
    print("ok")

    print("Sending goal...", flush=True)
    t0 = time.monotonic()
    try:
        navigate_skill(x, y, theta)
    except RuntimeError as e:
        # navigate_skill raises RuntimeError("goto failed: <status>: <reason>")
        # for any non-"ok" reply from the goto service.
        print(f"  ✗ {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"  ✗ unexpected: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    dt = time.monotonic() - t0
    print(f"  ✓ ok ({dt:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
