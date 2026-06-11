# Camera Streaming Service Documentation

This document explains the architecture and implementation of the live video streaming service used in the Robot Dashboard.

## Overview

The streaming service provides a real-time video feed from the robot's gripper camera (Intel RealSense D405) to the web interface. It uses **MJPEG (Motion JPEG)** over HTTP, which is natively supported by browser `<img>` tags and provides low-latency visualization without complex client-side decoders.

The dashboard's "Head" tile is **not** served by this MJPEG bridge; it comes from the robot's teleop WebSocket (`/ws/teleop`, cam_id=1) which streams JPEG-encoded frames from the head camera (Arducam OV9782 on ZMQ port 6000 in the post-FUNMAP driver). The legacy d435if MJPEG routes were removed on 2026-05-27 — port 6001 is silent in the current driver and the Intel RealSense D435if at the head was replaced by the Arducam.

## Architecture

### 1. Data Source (ZMQ)
The robot's hardware drivers publish camera frames over **ZeroMQ (ZMQ)** using a PUB/SUB pattern.
- **Protocol**: Multipart messages containing a topic, a timestamp, and raw/compressed payload bytes.
- **Port**: `6002` for the Gripper camera (D405).
- **Topics**: `rgb` and `depth`.

### 2. Backend Processing (`camera_api.py`)
The backend act as a bridge, consuming ZMQ messages and re-streaming them via HTTP.
- **Asynchronous Engine**: Uses `zmq.asyncio` to handle high-frequency frame intake without blocking the event loop.
- **Decompression**: Automatically detects and handles `blosc2` compression used by the driver to save bandwidth.
- **Resolution Auto-Detect**: Supports multiple resolutions (640x480, 1280x720) dynamically based on the received payload size.
- **Depth Colorization**: Since raw depth data (16-bit) isn't viewable in standard browsers, the service normalizes the values and applies the **JET colormap** (Blue = Far, Red = Near).

### 3. Mix Mode (Visual Fusion)
The `mix_mjpeg_generator` allows for a "Mixed Reality" view:
- It subscribes to both `rgb` and `depth` topics on a single socket.
- It synchronizes the latest RGB and Depth frames.
- It blends them using `cv2.addWeighted` (50/50 ratio) so users can see spatial information overlaid on the real-world view.

## API Endpoints

The service exposes the following routes in a flatter structure to ensure reliable mounting:

| Endpoint | Description |
|----------|-------------|
| `/api/stream/d405/rgb` | Raw RGB feed from the Gripper camera |
| `/api/stream/d405/depth` | Colorized depth map from the Gripper camera |
| `/api/stream/d405/mix` | Overlay of RGB and Depth for the Gripper camera |

## Frontend Integration (`video-panel.tsx`)

The dashboard `VideoPanel` component manages the stream display:
- **Mode Toggle**: A triple-state selector (RGB | DEPTH | MIX) updates the component's state.
- **URL Binding**: The `src` attribute of the `<img>` tag is dynamically mapped to the corresponding API endpoint.
- **Clean Restarts**: Uses a React `key` (e.g., `key={"d405-" + streamMode}`) to force the browser to disconnect and reconnect to the new MJPEG stream immediately when the mode is switched.

## Troubleshooting

- **404 Not Found**: Ensure the backend server has been restarted after any changes to `workflow_api.py` or `camera_api.py`.
- **No Stream Display**: Verify that the robot's ZMQ driver is running on the specified ports and that `SERVER_IP` in the config points to the correct robot IP.
- **Latency**: Ensure the dashboard and robot are on a stable network; high MJPEG frame rates can be bandwidth-intensive.
