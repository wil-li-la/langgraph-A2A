# YOLO-World streaming detection — ROS 2 stack

Real-time open-vocabulary detection that feeds the same dashboard overlay
the on-demand `ask_vlm()` tool uses. Designed to plug into the existing
`isaac_ros_dev` container next to `sensors_bridge` and `nav_service`.

## Architecture

```
[robot] stretch3-zmq driver
    │   (ZMQ PUB: color, depth, camera_info)
    ▼
[isaac_ros_dev container]
  sensors_bridge.py
    │   ROS topics: /camera/color/image_raw, /camera/color/camera_info
    ▼
  yolo_world_node.py  (NEW)
    │   /detections (vision_msgs/Detection2DArray @ ~30 Hz)
    ├── rosbag2 record (replay audits)
    ├── Nav2 obstacle layer (future)
    └── detection_bridge.py  (NEW)
            │   ZMQ PUB :5562 (msgpack)
            ▼
        [backend (lab laptop)]
          detect_zmq_consumer.py  (NEW, auto-started)
            │
            ▼
          publish_detection() → SSE → React overlay
```

The `ask_vlm()` tool (qwen2.5vl on RTX 4080, 2-8 s/call) stays around as
the open-vocab fallback — used by the agent when YOLO-World's bounded
vocabulary can't answer the question.

## Prereqs (inside `isaac_ros_dev`)

```bash
pip install ultralytics                # brings torch + YOLOWorld
sudo apt install ros-humble-vision-msgs ros-humble-cv-bridge

# (one-time, ~150MB) — Ultralytics will auto-download on first call too:
python3 -c "from ultralytics import YOLOWorld; YOLOWorld('yolov8s-worldv2.pt')"
```

## Running

```bash
# T1 — robot driver (on the robot, unchanged):
ssh stretch-se3-3099.local
cd Desktop/stretch3-zmq/ && uv run python -m stretch3_zmq.driver --config config.yaml

# T2 — full stack inside container, unchanged:
docker exec -it isaac_ros_dev /workspaces/langgraph-A2A/backend/nav_bridge/run_nav.sh

# T3 — YOLO-World detector (NEW):
docker exec -it isaac_ros_dev bash -lc '
  source /opt/ros/humble/setup.bash &&
  source /workspaces/isaac_ros-dev/install/setup.bash &&
  python3 /workspaces/langgraph-A2A/backend/nav_bridge/yolo_world_node.py \
    --image-topic /camera/color/image_raw \
    --classes "medicine bottle,patient,human,chair,door,table"
'

# T4 — bridge ROS detections → ZMQ for the backend (NEW):
docker exec -it isaac_ros_dev bash -lc '
  source /opt/ros/humble/setup.bash &&
  python3 /workspaces/langgraph-A2A/backend/nav_bridge/detection_bridge.py \
    --camera head --zmq-port 5562
'

# T5 — backend (on the laptop, host venv): set the ZMQ host env so the
# consumer thread spins up. Container's IP, or `host.docker.internal` if
# bridge runs on the container with port forwarded.
cd backend && source .venv/bin/activate && \
  DETECT_ZMQ_HOST=192.168.1.100 DETECT_ZMQ_PORT=5562 \
  python -m app --host localhost --port 9999
```

## Changing the vocabulary at runtime

```bash
ros2 param set /yolo_world classes \
  "['medicine bottle','red pill bottle','blister pack','person']"
```

The detector reloads the prompt list without a restart.

## Verification

```bash
# Topic rate (should be ~30 Hz on RTX 4080 with yolov8s-worldv2):
ros2 topic hz /detections

# Watch detections:
ros2 topic echo /detections | head -40

# Annotated image (for debugging):
ros2 run rqt_image_view rqt_image_view /detections/annotated

# Dashboard overlay — open http://localhost:3000 and click Connect.
# Detections that match the configured class list overlay automatically.
```

## When to use what

| Need | Use |
|---|---|
| "Where is the medicine bottle right now?" | YOLO-World stream (auto-overlay), call `recall_object` or query `/api/detect/latest` |
| "Is the patient holding the bottle correctly?" | `ask_vlm("is the bottle in the patient's hand?")` |
| "What's on the desk?" | `ask_vlm("describe what is on the desk")` |
| Real-time obstacle avoidance | `/detections` → Nav2 obstacle layer (future) |
| Incident replay | `rosbag2 record /detections /camera/color/image_raw` |
