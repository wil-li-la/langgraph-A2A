#!/usr/bin/env python3
"""End-to-end verification of the lab nvblox+Nav2 stack via ZMQ.

Phase-1 harness: drives the robot to a single map-frame pose and reports
the terminal status returned by the lab `nav_service`. Self-contained —
does not import from `app/`, does not touch the medication_delivery
workflow.

Usage:
    python backend/test_nvblox_nav.py <x> <y> <theta_deg> \
        [--lab-host HOST] [--lab-port PORT] [--timeout SECONDS]

Examples:
    python backend/test_nvblox_nav.py 0.5 0.0 0
    python backend/test_nvblox_nav.py 1.5 1.0 90 --lab-host hcis-s28
"""
from __future__ import annotations

import argparse
import math
import sys
import time
import uuid

try:
    import msgpack
    import zmq
except ImportError as e:
    sys.stderr.write(
        f"missing dependency: {e.name}. Install with: pip install pyzmq msgpack\n"
    )
    sys.exit(2)


DEFAULT_PORT = 5560
DEFAULT_TIMEOUT_S = 60.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("x", type=float, help="map-frame x in metres")
    parser.add_argument("y", type=float, help="map-frame y in metres")
    parser.add_argument("theta_deg", type=float, help="map-frame heading in degrees")
    parser.add_argument("--lab-host", default="localhost",
                        help="lab nav_service hostname (default: localhost)")
    parser.add_argument("--lab-port", type=int, default=DEFAULT_PORT,
                        help=f"lab nav_service port (default: {DEFAULT_PORT})")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                        help=f"server-side nav timeout in seconds "
                             f"(default: {DEFAULT_TIMEOUT_S})")
    args = parser.parse_args(argv)

    target = [args.x, args.y, math.radians(args.theta_deg)]
    request = {
        "target": target,
        "timeout_s": args.timeout,
        "request_id": str(uuid.uuid4()),
    }

    addr = f"tcp://{args.lab_host}:{args.lab_port}"
    print(f"→ {addr}  navigate_to(x={args.x}, y={args.y}, "
          f"θ={args.theta_deg}°)  timeout={args.timeout}s")

    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.REQ)
    # Cap the client wait at server timeout + 5 s so a stuck server can't
    # hang this script forever.
    sock.setsockopt(zmq.RCVTIMEO, int((args.timeout + 5) * 1000))
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(addr)

    t0 = time.monotonic()
    try:
        sock.send(msgpack.packb(request, use_bin_type=True))
        reply_bytes = sock.recv()
    except zmq.error.Again:
        print(f"✗ TIMEOUT — no reply from {addr} after "
              f"{args.timeout + 5:.0f}s")
        return 2
    finally:
        sock.close()

    elapsed = time.monotonic() - t0
    reply = msgpack.unpackb(reply_bytes, raw=False)
    status = reply.get("status", "UNKNOWN")
    icon = "✓" if status == "OK" else "✗"
    print(
        f"{icon} status={status}  "
        f"reason={reply.get('reason', '')!r}  "
        f"final_pose={reply.get('final_pose')}  "
        f"client_elapsed={elapsed:.2f}s  "
        f"server_elapsed={reply.get('elapsed_s', 0):.2f}s"
    )
    return 0 if status == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
