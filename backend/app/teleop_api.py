"""WebSocket relay for teleop: browser <-> backend <-> robot."""

import asyncio
import logging
from urllib.parse import urlparse

import websockets
from starlette.websockets import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

TCP_PROBE_TIMEOUT_SEC = 3.0
WS_OPEN_TIMEOUT_SEC = 5.0


async def _send_error(ws: WebSocket, reason: str, message: str) -> None:
    try:
        await ws.send_json({"type": "error", "reason": reason, "message": message})
    except Exception:
        pass


async def teleop_websocket(ws: WebSocket):
    """Transparent WebSocket relay between browser and robot.

    The browser connects to /ws/teleop?robot=ws://robot-ip:8765.
    Before relaying we run a TCP probe so the frontend can distinguish:

      - public_unreachable: TCP timed out or host/network not reachable
      - robot_unreachable:  TCP refused, or WS handshake / first message failed
    """
    robot_url = ws.query_params.get("robot")
    if not robot_url:
        await ws.close(code=1008, reason="Missing ?robot= query parameter")
        return

    await ws.accept()
    logger.info(f"Teleop: browser connected, relaying to {robot_url}")

    parsed = urlparse(robot_url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    if not host:
        await _send_error(ws, "public_unreachable", f"Invalid robot URL: {robot_url}")
        await ws.close()
        return

    # 1. TCP probe — distinguishes network reachability from service availability.
    #    No SYN-ACK in time → router/network drop → public_unreachable.
    #    RST (ConnectionRefused) → host responded but port closed → robot_unreachable.
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=TCP_PROBE_TIMEOUT_SEC,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    except ConnectionRefusedError as exc:
        logger.warning(f"Teleop: TCP refused at {host}:{port}: {exc}")
        await _send_error(ws, "robot_unreachable", f"{host}:{port} refused connection")
        await ws.close()
        return
    except (asyncio.TimeoutError, OSError) as exc:
        logger.warning(f"Teleop: TCP unreachable at {host}:{port}: {exc}")
        await _send_error(ws, "public_unreachable", f"Cannot reach {host}:{port}: {exc}")
        await ws.close()
        return

    # 2. WS handshake. TCP succeeded, so failures here mean the listener is
    #    not the robot's WS server (or it's flapping).
    try:
        async with websockets.connect(
            robot_url, open_timeout=WS_OPEN_TIMEOUT_SEC
        ) as robot_ws:
            async def browser_to_robot():
                try:
                    while True:
                        msg = await ws.receive()
                        if "text" in msg:
                            await robot_ws.send(msg["text"])
                        elif "bytes" in msg:
                            await robot_ws.send(msg["bytes"])
                except WebSocketDisconnect:
                    pass

            # Per-camera latest-frame buffer. Frame protocol is
            # [1-byte camera_id][JPEG...]. If the browser/network is slower
            # than the robot's publish rate, old frames are overwritten and
            # only the newest frame per camera is sent — trades FPS for
            # latency so the operator sees fresh video.
            latest_frames: dict[int, bytes] = {}
            new_frame = asyncio.Event()

            async def robot_to_browser_reader():
                """Drain robot_ws as fast as possible; forward text, coalesce frames."""
                try:
                    async for msg in robot_ws:
                        if isinstance(msg, str):
                            # Status messages are small and rare — send directly
                            await ws.send_text(msg)
                        elif isinstance(msg, bytes) and len(msg) >= 1:
                            cam_id = msg[0]
                            latest_frames[cam_id] = msg
                            new_frame.set()
                except websockets.exceptions.ConnectionClosed:
                    pass

            async def robot_to_browser_writer():
                """Send the most recent frame per camera; old frames are dropped."""
                try:
                    while True:
                        await new_frame.wait()
                        new_frame.clear()
                        # Snapshot and clear so concurrent reader writes go to a fresh batch
                        batch = list(latest_frames.values())
                        latest_frames.clear()
                        for frame in batch:
                            await ws.send_bytes(frame)
                except (WebSocketDisconnect, websockets.exceptions.ConnectionClosed):
                    pass

            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(browser_to_robot()),
                    asyncio.create_task(robot_to_browser_reader()),
                    asyncio.create_task(robot_to_browser_writer()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

    except (
        websockets.exceptions.InvalidHandshake,
        websockets.exceptions.WebSocketException,
        asyncio.TimeoutError,
        OSError,
    ) as exc:
        logger.warning(f"Teleop: WS handshake to {robot_url} failed: {exc}")
        await _send_error(ws, "robot_unreachable", f"WebSocket handshake failed: {exc}")
    except Exception as exc:
        logger.warning(f"Teleop: unexpected error talking to {robot_url}: {exc}")
        await _send_error(ws, "robot_unreachable", str(exc))
    finally:
        try:
            await ws.close()
        except Exception:
            pass
        logger.info("Teleop: session closed")
