"""WebSocket relay for teleop: browser <-> backend <-> robot."""

import asyncio
import logging

import websockets
from starlette.websockets import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


async def teleop_websocket(ws: WebSocket):
    """Transparent WebSocket relay between browser and robot.

    The browser connects to /ws/teleop?robot=ws://robot-ip:8765.
    This endpoint opens a WebSocket to the robot and relays all
    messages (text + binary) bidirectionally.
    """
    robot_url = ws.query_params.get("robot")
    if not robot_url:
        await ws.close(code=1008, reason="Missing ?robot= query parameter")
        return

    await ws.accept()
    logger.info(f"Teleop: browser connected, relaying to {robot_url}")

    try:
        async with websockets.connect(robot_url) as robot_ws:
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

    except Exception as exc:
        logger.warning(f"Teleop: failed to connect to robot at {robot_url}: {exc}")
        try:
            await ws.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass
        logger.info("Teleop: session closed")
