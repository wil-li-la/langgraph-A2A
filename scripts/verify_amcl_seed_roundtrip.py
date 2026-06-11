#!/usr/bin/env python3
"""Round-trip verifier for the AMCL seed pipeline.

   lab dashboard click  →  backend POST /api/nav/pose  →  ZMQ REQ 5564
                                                              ↓
                                                         robot bridge
                                                              ↓
                                                    /amcl/initialpose
                                                              ↓
                                                          AMCL refines
                                                              ↓
                                                    ZMQ PUB 5563 (amcl_pose)
                                                              ↓
                                                  backend SSE /api/nav/status/stream
                                                              ↓
                                                  ←  pose update visible at lab

Run after the robot ships the spec-amcl-seed-port-5564.md endpoint.

Usage:
  python scripts/verify_amcl_seed_roundtrip.py --x 1.5 --y 0.8 --theta 0.0
  python scripts/verify_amcl_seed_roundtrip.py --x 0 --y 0 --theta 0 --robot 192.168.1.38
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import urllib.error

import msgpack
import zmq


def fail(msg: str) -> None:
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"✅ {msg}")


def info(msg: str) -> None:
    print(f"·  {msg}")


def probe_zmq(addr: str, kind: str, timeout_s: float = 2.0,
              required: bool = True) -> bool:
    """Probe a ZMQ endpoint. Return True if live. If required=False, a
    silent endpoint is warned about and the verifier continues."""
    ctx = zmq.Context.instance()
    if kind == "sub":
        s = ctx.socket(zmq.SUB)
        s.setsockopt(zmq.SUBSCRIBE, b"")
        s.setsockopt(zmq.RCVTIMEO, int(timeout_s * 1000))
        s.setsockopt(zmq.LINGER, 0)
        s.connect(addr)
        try:
            raw = s.recv()
            ok(f"{addr} ({kind}) live, got {len(raw)} bytes")
            return True
        except zmq.Again:
            if required:
                fail(f"{addr} ({kind}) silent within {timeout_s}s")
            print(f"⚠️  {addr} ({kind}) silent — non-critical, continuing")
            return False
        finally:
            s.close()
    else:
        raise ValueError(f"unknown kind {kind!r}")


def post_pose(backend: str, x: float, y: float, theta: float) -> dict:
    body = json.dumps({"x": x, "y": y, "theta": theta}).encode()
    req = urllib.request.Request(
        f"{backend}/api/nav/pose",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def fetch_pose(backend: str) -> dict:
    with urllib.request.urlopen(f"{backend}/api/nav/pose", timeout=3) as r:
        return json.loads(r.read())


def wait_pose_near(backend: str, x: float, y: float, theta: float,
                   tol_xy: float, tol_theta: float, timeout_s: float) -> dict | None:
    """Poll /api/nav/pose until pose is within tolerance of (x,y,theta),
    or timeout. Returns the matching pose dict or None."""
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = fetch_pose(backend)
        if last and last.get("source") == "amcl":
            dx = abs(last["x"] - x)
            dy = abs(last["y"] - y)
            dth = abs(((last["theta"] - theta + 3.14159) % (2 * 3.14159)) - 3.14159)
            if dx <= tol_xy and dy <= tol_xy and dth <= tol_theta:
                return last
        time.sleep(0.1)
    return last


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", type=float, required=True, help="seed x in amcl_map frame (m)")
    ap.add_argument("--y", type=float, required=True, help="seed y in amcl_map frame (m)")
    ap.add_argument("--theta", type=float, required=True, help="seed yaw (rad)")
    ap.add_argument("--robot", default="192.168.1.38", help="robot ZMQ host")
    ap.add_argument("--backend", default="http://localhost:9999", help="lab backend URL")
    ap.add_argument("--tol-xy", type=float, default=0.15, help="xy tolerance (m)")
    ap.add_argument("--tol-theta", type=float, default=0.20, help="theta tolerance (rad)")
    ap.add_argument("--converge-timeout", type=float, default=5.0,
                    help="seconds to wait for AMCL to converge to seed")
    args = ap.parse_args()

    print(f"=== AMCL seed round-trip → ({args.x:.3f}, {args.y:.3f}, {args.theta:.3f}) ===\n")

    info("Step 1: probe robot publishers")
    # map_pub:5562 isn't needed for round-trip; backend has a disk fallback.
    probe_zmq(f"tcp://{args.robot}:5562", "sub", timeout_s=2.0, required=False)
    # amcl_pose:5563 IS required — the convergence check depends on it.
    probe_zmq(f"tcp://{args.robot}:5563", "sub", timeout_s=2.0, required=True)

    info("Step 2: lab → robot via /api/nav/pose")
    try:
        resp = post_pose(args.backend, args.x, args.y, args.theta)
    except urllib.error.URLError as e:
        fail(f"backend POST failed: {e}")
    print(f"   response: {json.dumps(resp, indent=2)}")
    seed = resp.get("seed", {})
    if not seed.get("forwarded"):
        fail(f"backend did not forward to robot: {seed.get('error', 'unknown')}")
    if not seed.get("ok"):
        fail(f"robot rejected seed: {seed.get('reply', 'unknown')}")
    ok(f"robot bridge replied: {seed['reply']}")

    info(f"Step 3: wait ≤{args.converge_timeout}s for amcl_pose to reflect seed")
    final = wait_pose_near(args.backend, args.x, args.y, args.theta,
                           args.tol_xy, args.tol_theta, args.converge_timeout)
    if final is None:
        fail("no pose received from /api/nav/pose during wait window")
    if final.get("source") != "amcl":
        fail(f"final pose source={final.get('source')} (expected amcl); "
             f"raw={json.dumps(final)}")
    dx = abs(final["x"] - args.x)
    dy = abs(final["y"] - args.y)
    dth = abs(((final["theta"] - args.theta + 3.14159) % (2 * 3.14159)) - 3.14159)
    if dx > args.tol_xy or dy > args.tol_xy or dth > args.tol_theta:
        fail(f"AMCL did not converge: pose=({final['x']:.3f}, {final['y']:.3f}, "
             f"{final['theta']:.3f}); deltas=({dx:.3f}, {dy:.3f}, {dth:.3f})")
    ok(f"AMCL converged: pose=({final['x']:.3f}, {final['y']:.3f}, {final['theta']:.3f}) "
       f"deltas=({dx:.3f}, {dy:.3f}, {dth:.3f})")

    print("\n=== ROUND-TRIP OK ===")


if __name__ == "__main__":
    main()
