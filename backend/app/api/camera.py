import asyncio
import logging
import numpy as np
import cv2
import zmq.asyncio
from starlette.requests import Request
from starlette.responses import StreamingResponse

from stretch3_zmq.core.messages.protocol import decode_with_timestamp
from cure.config import get_config
from cure.constants import SERVER_IP

logger = logging.getLogger(__name__)

async def mjpeg_generator(port: int, topic: str = "rgb"):
    """Generic MJPEG generator that consumes ZMQ frames and yields JPEG chunks."""
    print(f"DEBUG: Starting mjpeg_generator for port {port}, topic {topic}", flush=True)
    context = zmq.asyncio.Context()
    socket = context.socket(zmq.SUB)
    socket.setsockopt(zmq.RCVHWM, 1)
    socket.setsockopt(zmq.SUBSCRIBE, topic.encode())
    socket.setsockopt(zmq.RCVTIMEO, 5000)
    
    address = f"tcp://{SERVER_IP}:{port}"
    print(f"DEBUG: Connecting to {address} for {topic} stream", flush=True)
    socket.connect(address)
    
    try:
        while True:
            try:
                parts = await socket.recv_multipart()
                if not parts or parts[0] != topic.encode():
                    continue
                    
                _, payload = decode_with_timestamp(parts[1:])
                
                # Robust decompression helper
                def decompress_if_needed(data):
                    try:
                        import blosc2
                        return bytes(blosc2.decompress(data))
                    except Exception:
                        return data

                frame = None
                if topic == "rgb":
                    raw = decompress_if_needed(payload)
                    # Support multiple resolutions
                    if len(raw) == 640 * 480 * 3:
                        frame = np.frombuffer(raw, dtype=np.uint8).reshape(480, 640, 3)
                    elif len(raw) == 1280 * 720 * 3:
                        frame = np.frombuffer(raw, dtype=np.uint8).reshape(720, 1280, 3)
                    else:
                        frame = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)

                elif topic == "depth":
                    raw = decompress_if_needed(payload)
                    if len(raw) == 640 * 480 * 2:
                        data = np.frombuffer(raw, dtype=np.uint16).reshape(480, 640)
                    elif len(raw) == 1280 * 720 * 2:
                        data = np.frombuffer(raw, dtype=np.uint16).reshape(720, 1280)
                    else:
                        data = None

                    if data is not None:
                        try:
                            # Normalize and colorize
                            depth_vis = cv2.normalize(data, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
                            frame = cv2.applyColorMap(depth_vis, cv2.COLORMAP_JET)
                        except Exception as e:
                            print(f"DEBUG: Colorization error: {e}", flush=True)
                            frame = None
                
                if frame is None:
                    if np.random.random() < 0.05:
                        print(f"DEBUG: Failed to decode {topic} frame from port {port}", flush=True)
                    continue
                
                ret, buffer = cv2.imencode('.jpg', frame)
                if not ret:
                    continue
                    
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
                )
                await asyncio.sleep(0.01)
                
            except zmq.Again:
                continue 
            except Exception as e:
                print(f"DEBUG: Error in {topic} stream generator (port {port}): {e}", flush=True)
                break
    finally:
        socket.close()
        context.term()

async def mix_mjpeg_generator(port: int):
    """MJPEG generator that blends RGB and Depth frames."""
    print(f"DEBUG: Starting mix_mjpeg_generator for port {port}", flush=True)
    context = zmq.asyncio.Context()
    socket = context.socket(zmq.SUB)
    socket.setsockopt(zmq.RCVHWM, 2)
    socket.setsockopt(zmq.SUBSCRIBE, b"rgb")
    socket.setsockopt(zmq.SUBSCRIBE, b"depth")
    socket.setsockopt(zmq.RCVTIMEO, 5000)
    
    address = f"tcp://{SERVER_IP}:{port}"
    socket.connect(address)
    
    rgb_frame = None
    depth_frame = None
    
    try:
        while True:
            try:
                parts = await socket.recv_multipart()
                topic = parts[0].decode()
                _, payload = decode_with_timestamp(parts[1:])
                
                def decompress_if_needed(data):
                    try:
                        import blosc2
                        return bytes(blosc2.decompress(data))
                    except Exception:
                        return data

                if topic == "rgb":
                    raw = decompress_if_needed(payload)
                    if len(raw) == 640 * 480 * 3:
                        rgb_frame = np.frombuffer(raw, dtype=np.uint8).reshape(480, 640, 3)
                    elif len(raw) == 1280 * 720 * 3:
                        rgb_frame = np.frombuffer(raw, dtype=np.uint8).reshape(720, 1280, 3)
                    else:
                        rgb_frame = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
                
                elif topic == "depth":
                    raw = decompress_if_needed(payload)
                    data = None
                    if len(raw) == 640 * 480 * 2:
                        data = np.frombuffer(raw, dtype=np.uint16).reshape(480, 640)
                    elif len(raw) == 1280 * 720 * 2:
                        data = np.frombuffer(raw, dtype=np.uint16).reshape(720, 1280)
                    
                    if data is not None:
                        depth_vis = cv2.normalize(data, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
                        depth_frame = cv2.applyColorMap(depth_vis, cv2.COLORMAP_JET)

                # If we have both, blend and yield
                if rgb_frame is not None and depth_frame is not None:
                    # Ensure same size for blending
                    if rgb_frame.shape[:2] != depth_frame.shape[:2]:
                        depth_frame = cv2.resize(depth_frame, (rgb_frame.shape[1], rgb_frame.shape[0]))
                    
                    # Blend 50/50
                    mixed = cv2.addWeighted(rgb_frame, 0.5, depth_frame, 0.5, 0)
                    
                    ret, buffer = cv2.imencode('.jpg', mixed)
                    if ret:
                        yield (
                            b"--frame\r\n"
                            b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
                        )
                    # Clear for next pair (or keep for sliding window - let's stay fresh)
                    # rgb_frame = None 
                    # depth_frame = None
                
                await asyncio.sleep(0.001)
                
            except zmq.Again:
                continue 
            except Exception as e:
                print(f"DEBUG: Error in mix generator (port {port}): {e}", flush=True)
                break
    finally:
        socket.close()
        context.term()

async def stream_d405_rgb(request: Request) -> StreamingResponse:
    """GET /api/stream/d405/rgb"""
    cfg = get_config()
    return StreamingResponse(mjpeg_generator(cfg.ports.d405, "rgb"), media_type="multipart/x-mixed-replace; boundary=frame")

async def stream_d405_depth(request: Request) -> StreamingResponse:
    """GET /api/stream/d405/depth"""
    cfg = get_config()
    return StreamingResponse(mjpeg_generator(cfg.ports.d405, "depth"), media_type="multipart/x-mixed-replace; boundary=frame")

async def stream_d405_mix(request: Request) -> StreamingResponse:
    """GET /api/stream/d405/mix"""
    cfg = get_config()
    return StreamingResponse(mix_mjpeg_generator(cfg.ports.d405), media_type="multipart/x-mixed-replace; boundary=frame")

async def stream_d435if_rgb(request: Request) -> StreamingResponse:
    """GET /api/stream/d435if/rgb"""
    cfg = get_config()
    return StreamingResponse(mjpeg_generator(cfg.ports.d435if, "rgb"), media_type="multipart/x-mixed-replace; boundary=frame")

async def stream_d435if_depth(request: Request) -> StreamingResponse:
    """GET /api/stream/d435if/depth"""
    cfg = get_config()
    return StreamingResponse(mjpeg_generator(cfg.ports.d435if, "depth"), media_type="multipart/x-mixed-replace; boundary=frame")

async def stream_d435if_mix(request: Request) -> StreamingResponse:
    """GET /api/stream/d435if/mix"""
    cfg = get_config()
    return StreamingResponse(mix_mjpeg_generator(cfg.ports.d435if), media_type="multipart/x-mixed-replace; boundary=frame")
