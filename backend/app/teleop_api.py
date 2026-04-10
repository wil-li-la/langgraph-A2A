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

            async def robot_to_browser():
                try:
                    async for msg in robot_ws:
                        if isinstance(msg, str):
                            await ws.send_text(msg)
                        elif isinstance(msg, bytes):
                            await ws.send_bytes(msg)
                except websockets.exceptions.ConnectionClosed:
                    pass

            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(browser_to_robot()),
                    asyncio.create_task(robot_to_browser()),
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
