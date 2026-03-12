"""Interactive ZMQ client for testing Stretch3-ZMQ Driver services.

Supports robot control (command/status), speech (TTS/ASR), and camera
streaming (Arducam, D435if, D405) with a 5-second receive timeout so
camera windows close cleanly even if the server stops sending frames.
"""

import argparse
import time

import blosc2
import cv2
import msgpack
import numpy as np
import zmq

from stretch3_zmq.core.messages.command import BaseCommand, ManipulatorCommand
from stretch3_zmq.core.messages.protocol import decode_with_timestamp, encode_with_timestamp
from stretch3_zmq.core.messages.status import Status
from stretch3_zmq.core.messages.twist_2d import Twist2D

CAMERA_RECV_TIMEOUT_MS = 5000


# ---------------------------------------------------------------------------
# Camera helpers
# ---------------------------------------------------------------------------


def _decompress(data: bytes) -> bytes:
    """Decompress blosc2 data, or return as-is if not compressed."""
    try:
        return bytes(blosc2.decompress(data))
    except Exception:
        return data


def _show_rgbd_frame(
    window_name: str,
    color_raw: bytes,
    depth_raw: bytes,
    rotate_code: int | None = None,
) -> None:
    """Decode raw RGB-D buffers, colorize depth, and display side-by-side."""
    color = np.frombuffer(_decompress(color_raw), np.uint8).reshape(480, 640, 3)
    depth = np.frombuffer(_decompress(depth_raw), np.uint16).reshape(480, 640)

    try:
        depth_vis = cv2.normalize(depth, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
    except Exception:
        depth_vis = np.zeros_like(depth, dtype=np.uint8)

    depth_cmap = cv2.applyColorMap(depth_vis, cv2.COLORMAP_JET)

    if rotate_code is not None:
        color = cv2.rotate(color, rotate_code)
        depth_cmap = cv2.rotate(depth_cmap, rotate_code)

    cv2.imshow(window_name, np.hstack((color, depth_cmap)))


def _stream_arducam(socket: zmq.Socket) -> None:
    """Stream Arducam: 1280x720, rotated 90 CW + vertical flip."""
    print("Opening Arducam (press 'q' to quit)...")
    try:
        while True:
            try:
                parts = socket.recv_multipart()
                _, raw = decode_with_timestamp(parts)
            except zmq.Again:
                print("\nArducam: no frame received (timeout).")
                break
            frame = np.frombuffer(_decompress(raw), np.uint8).reshape(720, 1280, 3)
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
            frame = cv2.flip(frame, 0)
            cv2.imshow("Arducam", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        cv2.destroyAllWindows()


def _stream_rgbd(
    name: str,
    socket: zmq.Socket,
    rotate_code: int | None = None,
) -> None:
    """Stream an RGB-D camera on a single socket, routing by topic (rgb/depth)."""
    print(f"Opening {name} (press 'q' to quit)...")
    color_raw: bytes | None = None
    depth_raw: bytes | None = None
    try:
        while True:
            try:
                parts = socket.recv_multipart()
            except zmq.Again:
                print(f"\n{name}: no frame received (timeout).")
                break
            topic = parts[0]
            _, payload = decode_with_timestamp(parts[1:])
            if topic == b"rgb":
                color_raw = payload
            elif topic == b"depth":
                depth_raw = payload
            if color_raw is not None and depth_raw is not None:
                _show_rgbd_frame(name, color_raw, depth_raw, rotate_code)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    finally:
        cv2.destroyAllWindows()


# ---------------------------------------------------------------------------
# Socket setup
# ---------------------------------------------------------------------------


def _connect_sub(
    context: zmq.Context,
    address: str,
    *,
    timeout_ms: int | None = None,
) -> zmq.Socket:
    """Create a SUB socket with HWM=1 and an optional receive timeout."""
    sock = context.socket(zmq.SUB)
    sock.setsockopt(zmq.RCVHWM, 1)
    if timeout_ms is not None:
        sock.setsockopt(zmq.RCVTIMEO, timeout_ms)
    sock.connect(address)
    sock.setsockopt_string(zmq.SUBSCRIBE, "")
    return sock


# ---------------------------------------------------------------------------
# REPL command handlers
# ---------------------------------------------------------------------------


def _handle_tts(tts_socket: zmq.Socket, text: str) -> None:
    if text:
        tts_socket.send_string(text)
        job_id = tts_socket.recv_string()
        print(f"Sent (job_id={job_id})\n")
    else:
        print("No text provided\n")


def _handle_asr(asr_socket: zmq.Socket) -> None:
    print("Listening... Speak now!")
    asr_socket.send_string("listen")
    transcript = asr_socket.recv_string()
    print(f"Transcript: {transcript}\n" if transcript else "No speech detected\n")


def _handle_command(cmd_socket: zmq.Socket, args_str: str) -> None:
    try:
        if args_str:
            positions = tuple(float(p) for p in args_str.split())
        else:
            print("No positions provided. Using default dummy positions.")
            positions = (0.0, 0.5, 0.2, 0.0, 0.0, 0.0, 0.0, 0.0, 100.0)

        parts = [
            b"manipulator",
            *encode_with_timestamp(ManipulatorCommand(joint_positions=positions).to_bytes()),
        ]
        cmd_socket.send_multipart(parts)
        print(f"Sent command: {positions}\n")
    except ValueError as e:
        print(f"Invalid input: {e}")
        print("Usage: command <p0> <p1> ... <p9>")
        print("Example: command 0.1 0.0 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9\n")
    except Exception as e:
        print(f"Error sending command: {e}\n")


def _handle_base_command(cmd_socket: zmq.Socket, args_str: str) -> None:
    try:
        tokens = args_str.split()
        if len(tokens) < 1:
            print("Usage: base <x> [y] [theta] [velocity|position]")
            print("Example: base 0.3 0.0 0.0 velocity\n")
            return

        mode = "velocity"
        if tokens[-1] in ("velocity", "position"):
            mode = tokens.pop()

        floats = [float(t) for t in tokens]
        x = floats[0] if len(floats) > 0 else 0.0
        y = floats[1] if len(floats) > 1 else 0.0
        theta = floats[2] if len(floats) > 2 else 0.0

        command = BaseCommand(mode=mode, twist=Twist2D(linear=x, angular=theta))
        parts = [b"base", *encode_with_timestamp(command.to_bytes())]
        cmd_socket.send_multipart(parts)
        print(f"Sent base command: x={x} y={y} theta={theta} mode={mode}\n")
    except ValueError as e:
        print(f"Invalid input: {e}")
        print("Usage: base <x> [y] [theta] [velocity|position]\n")
    except Exception as e:
        print(f"Error sending base command: {e}\n")


def _handle_goto(goto_socket: zmq.Socket, args_str: str) -> None:
    tokens = args_str.split()
    if len(tokens) != 2 or tokens[0] not in ("linear", "angular"):
        print("Usage: goto linear <m>  |  goto angular <rad>\n")
        return
    try:
        value = float(tokens[1])
    except ValueError:
        print(f"Invalid value: {tokens[1]!r}\n")
        return

    axis = tokens[0]
    twist = {
        "linear": value if axis == "linear" else 0.0,
        "angular": value if axis == "angular" else 0.0,
    }
    goto_socket.send(msgpack.packb(twist))
    reply = goto_socket.recv_string()
    print(f"Reply: {reply}\n")


def _handle_status(status_socket: zmq.Socket) -> None:
    print("Receiving status... (Ctrl+C to stop)")
    try:
        while True:
            parts = status_socket.recv_multipart()
            timestamp_ns, payload = decode_with_timestamp(parts)
            status = Status.from_bytes(payload)

            # Calculate message age for latency monitoring
            age_ms = (time.time_ns() - timestamp_ns) / 1_000_000
            pose = status.odometry.pose
            odom_str = f"{pose.x:.2f}, {pose.y:.2f}, {pose.theta:.2f}"
            print(
                f"\r[Status] odom=[{odom_str}] runstop={status.runstop} age={age_ms:.1f}ms",
                end="",
                flush=True,
            )
    except KeyboardInterrupt:
        print("\nStopped receiving status.\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

HELP_TEXT = """\
Commands:
  tts <text>                        Send text to TTS
  asr                               Start listening for speech
  command <p0>...<p9>               Send manipulator command (10 joint positions)
  base <x> [y] [theta] [mode]       Send base command (mode: velocity|position)
  goto linear <m>                   Blocking base translate (metres)
  goto angular <rad>                Blocking base rotate (radians)
  status                            Subscribe to robot status (Ctrl+C to stop)
  camera [camera_name]              Stream camera feed (press 'q' to stop)
                                    Options: arducam, d435if (default), d405
  quit                              Exit
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Stretch3-ZMQ Client")
    parser.add_argument("server_ip", help="Server IP address")
    parser.add_argument("--status-port", type=int, default=5555, help="Status port")
    parser.add_argument("--command-port", type=int, default=5556, help="Command port")
    parser.add_argument("--goto-port", type=int, default=5557, help="Goto port")
    parser.add_argument("--tts-port", type=int, default=6101, help="TTS port")
    parser.add_argument("--asr-port", type=int, default=6103, help="ASR port")
    parser.add_argument("--arducam-port", type=int, default=6000, help="Arducam port")
    parser.add_argument("--d435if-port", type=int, default=6001, help="D435if port")
    parser.add_argument("--d405-port", type=int, default=6002, help="D405 port")
    args = parser.parse_args()

    server_ip: str = args.server_ip
    context = zmq.Context()

    try:
        # Control & speech sockets
        tts_socket = context.socket(zmq.REQ)
        tts_socket.connect(f"tcp://{server_ip}:{args.tts_port}")

        asr_socket = context.socket(zmq.REQ)
        asr_socket.connect(f"tcp://{server_ip}:{args.asr_port}")

        goto_socket = context.socket(zmq.REQ)
        goto_socket.connect(f"tcp://{server_ip}:{args.goto_port}")

        cmd_socket = context.socket(zmq.PUB)
        cmd_socket.connect(f"tcp://{server_ip}:{args.command_port}")

        status_socket = context.socket(zmq.SUB)
        status_socket.connect(f"tcp://{server_ip}:{args.status_port}")
        status_socket.setsockopt_string(zmq.SUBSCRIBE, "")

        # Camera sockets (with receive timeout for safe shutdown)
        def cam_sub(port: int) -> zmq.Socket:
            return _connect_sub(
                context,
                f"tcp://{server_ip}:{port}",
                timeout_ms=CAMERA_RECV_TIMEOUT_MS,
            )

        arducam_socket = cam_sub(args.arducam_port)
        d435if_socket = cam_sub(args.d435if_port)
        d405_socket = cam_sub(args.d405_port)

        print(f"Connected to {server_ip}")
        print(HELP_TEXT)

        while True:
            try:
                user_input = input("> ").strip()
            except KeyboardInterrupt:
                print("\nShutting down...")
                break

            if not user_input:
                continue

            cmd = user_input.lower()

            if cmd == "quit":
                break
            elif cmd.startswith("tts "):
                _handle_tts(tts_socket, user_input[4:].strip())
            elif cmd == "asr":
                _handle_asr(asr_socket)
            elif cmd.startswith("command"):
                _handle_command(cmd_socket, user_input[7:].strip())
            elif cmd.startswith("base"):
                _handle_base_command(cmd_socket, user_input[4:].strip())
            elif cmd.startswith("goto"):
                _handle_goto(goto_socket, user_input[4:].strip())
            elif cmd == "status":
                _handle_status(status_socket)
            elif cmd == "camera" or cmd.startswith("camera "):
                # Parse camera name, default to d435if
                parts = user_input.split(maxsplit=1)
                camera_name = parts[1].strip().lower() if len(parts) > 1 else "d435if"

                if camera_name == "arducam":
                    _stream_arducam(arducam_socket)
                elif camera_name == "d435if":
                    _stream_rgbd("D435if", d435if_socket, cv2.ROTATE_90_CLOCKWISE)
                elif camera_name == "d405":
                    _stream_rgbd("D405", d405_socket)
                else:
                    print(f"Unknown camera: {camera_name}. Options: arducam, d435if, d405\n")
            else:
                print("Unknown command. Type a command from the list above.\n")

    finally:
        context.term()


if __name__ == "__main__":
    main()
