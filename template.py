"""Minimal stretch3-zmq client template.

Run on any machine on the same network as the robot, after the driver is up:
    ssh hello-robot@stretch-se3-3099.local
    cd Desktop/stretch3-zmq/ && uv run python -m stretch3_zmq.driver --config config.yaml

Then on this machine:
    pip install stretch3-zmq-core pyzmq msgpack
    ROBOT_IP=stretch-se3-3099.local python template.py
"""

from __future__ import annotations

import os
import struct
import time

import msgpack
import zmq

from stretch3_zmq.core.messages.command import BaseCommand
from stretch3_zmq.core.messages.status import Status
from stretch3_zmq.core.messages.twist_2d import Twist2D

ROBOT_IP = os.getenv("ROBOT_IP", "stretch-se3-3099.local")
PORT_STATUS = 5555   # PUB  (robot -> client) : Status @ 15 Hz
PORT_COMMAND = 5556  # SUB  (client -> robot) : BaseCommand / ManipulatorCommand
PORT_GOTO = 5557     # REP  (client <-> robot): blocking base move
PORT_TTS = 6101      # REP  (client <-> robot): text -> job_id


def _ts() -> bytes:
    return struct.pack("!Q", time.time_ns())


def read_status(ctx: zmq.Context) -> Status:
    sock = ctx.socket(zmq.SUB)
    sock.setsockopt(zmq.SUBSCRIBE, b"")
    sock.setsockopt(zmq.CONFLATE, 1)
    sock.connect(f"tcp://{ROBOT_IP}:{PORT_STATUS}")
    try:
        parts = sock.recv_multipart()  # [timestamp, payload]
        return Status.from_bytes(parts[1])
    finally:
        sock.close()


def drive_base(ctx: zmq.Context, linear: float, angular: float) -> None:
    """Open-loop base step via REQ/REP (blocks until robot finishes)."""
    sock = ctx.socket(zmq.REQ)
    sock.connect(f"tcp://{ROBOT_IP}:{PORT_GOTO}")
    try:
        sock.send(msgpack.packb({"linear": linear, "angular": angular}))
        reply = sock.recv_string()
        if reply != "ok":
            raise RuntimeError(f"goto failed: {reply}")
    finally:
        sock.close()


def publish_base_velocity(ctx: zmq.Context, vx: float, wz: float) -> None:
    """Continuous velocity control via PUB on the command topic."""
    sock = ctx.socket(zmq.PUB)
    sock.connect(f"tcp://{ROBOT_IP}:{PORT_COMMAND}")
    time.sleep(0.2)  # let SUB subscribe before first send
    try:
        cmd = BaseCommand(mode="velocity", twist=Twist2D(x=vx, y=0.0, theta=wz))
        sock.send_multipart([b"base", _ts(), cmd.to_bytes()])
    finally:
        sock.close()


def speak(ctx: zmq.Context, text: str) -> str:
    sock = ctx.socket(zmq.REQ)
    sock.connect(f"tcp://{ROBOT_IP}:{PORT_TTS}")
    try:
        sock.send_string(text)
        return sock.recv_string()  # job_id
    finally:
        sock.close()


if __name__ == "__main__":
    ctx = zmq.Context()
    try:
        s = read_status(ctx)
        print(f"pose=({s.odometry.pose.x:.2f}, {s.odometry.pose.y:.2f}, "
              f"{s.odometry.pose.theta:.2f})  runstop={s.runstop}")

        speak(ctx, "hello from a remote machine")
        drive_base(ctx, linear=0.0, angular=0.3)   # rotate ~0.3 rad
        drive_base(ctx, linear=0.1, angular=0.0)   # forward 10 cm
    finally:
        ctx.term()
