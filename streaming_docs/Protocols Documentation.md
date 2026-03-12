# stretch3-zmq Protocol Reference

All messages use a **multipart** ZeroMQ envelope. The encoding helpers live in
[`packages/core/src/stretch3_zmq/core/messages/protocol.py`](../packages/core/src/stretch3_zmq/core/messages/protocol.py).

### Timestamp frame

Every message includes a timestamp as its first (or second, for topic-prefixed messages) frame:

| Field     | Size    | Encoding                                 |
|-----------|---------|------------------------------------------|
| timestamp | 8 bytes | `struct.pack("!Q", time.time_ns())` — unsigned 64-bit big-endian, nanoseconds since Unix epoch |

### Payload frame

Structured messages (robot status and commands) are serialized with
[msgpack](https://msgpack.org/) from the Pydantic model's `model_dump()` output.

Camera frames are sent as raw `numpy` array bytes (`ndarray.tobytes()`).

TTS input and ASR messages are plain UTF-8 strings (no msgpack).

---

## Port Map

| Service    | Port | Pattern | Description                                     |
|------------|------|---------|-------------------------------------------------|
| status     | 5555 | PUB     | Robot state published at `status_rate_hz`       |
| command    | 5556 | SUB     | Manipulator and base motion commands            |
| goto       | 5557 | REP     | Blocking base position move (linear or angular) |
| arducam    | 6000 | PUB     | Arducam OV9782 RGB frames                       |
| d435if     | 6001 | PUB     | RealSense D435i RGB + depth frames              |
| d405       | 6002 | PUB     | RealSense D405 RGB + depth frames               |
| tts        | 6101 | REP     | Text-to-speech input (returns job_id)           |
| tts_status | 6102 | PUB     | TTS job status updates                          |
| asr        | 6103 | REP     | Automatic speech recognition (request/reply)    |

Ports can be overridden in `config.yaml` under the `ports:` key.

---

## Services

### status — Robot State Publisher

- **Address:** `tcp://*:5555`
- **Pattern:** PUB
- **Rate:** `config.service.status_rate_hz` (default: 50 Hz)
- **Model:** [`Status`](../packages/core/src/stretch3_zmq/core/messages/status.py)

Continuously publishes the full robot state. No subscription topic is required; subscribers
connect and receive all messages.

**Multipart frames:**

```
[0] timestamp  — 8 bytes, nanoseconds since epoch
[1] payload    — msgpack-encoded Status
```

**`Status` schema:**

```
Status
├── is_charging: bool
├── is_low_voltage: bool
├── runstop: bool
├── odometry: Odometry
│   ├── pose: Pose2D  { x: float (m), y: float (m), theta: float (rad) }
│   └── twist: Twist2D { linear: float (m/s), angular: float (rad/s) }
├── imu: IMU
│   ├── orientation: Orientation { roll: float (rad), pitch: float (rad), yaw: float (rad) }
│   ├── acceleration: Vector3D { x: float, y: float, z: float }
│   └── gyro: Vector3D { x: float, y: float, z: float }
├── joint_positions: tuple[float, ...]  — one value per joint (see JointName order)
├── joint_velocities: tuple[float, ...]
└── joint_efforts: tuple[float, ...]
```

Joint tuple index order follows `JointName`
([`constants.py`](../packages/core/src/stretch3_zmq/core/constants.py)):

| Index | Joint            |
|-------|------------------|
| 0     | `base_translate` |
| 1     | `base_rotate`    |
| 2     | `lift`           |
| 3     | `arm`            |
| 4     | `head_pan`       |
| 5     | `head_tilt`      |
| 6     | `wrist_yaw`      |
| 7     | `wrist_pitch`    |
| 8     | `wrist_roll`     |
| 9     | `gripper`        |

---

### command — Motion Command Subscriber

- **Address:** `tcp://*:5556`
- **Pattern:** SUB (server-side; clients use PUB)
- **Topics:** `manipulator`, `base`
- **Models:** [`ManipulatorCommand`, `BaseCommand`](../packages/core/src/stretch3_zmq/core/messages/command.py)

Receives robot motion commands. Each message must be topic-prefixed so the driver can dispatch
to the correct handler.

**Multipart frames:**

```
[0] topic      — b"manipulator" or b"base"
[1] timestamp  — 8 bytes, nanoseconds since epoch
[2] payload    — msgpack-encoded command
```

**`ManipulatorCommand` schema** (topic `manipulator`):

```
ManipulatorCommand
└── joint_positions: tuple[float, ...]  — exactly 10 values, one per JointName
```

**`BaseCommand` schema** (topic `base`):

```
BaseCommand
├── mode: "velocity" | "position"  (default: "velocity")
└── twist: Twist2D { linear: float (m/s), angular: float (rad/s) }
```

---

### goto — Blocking Base Move

- **Address:** `tcp://*:5557`
- **Pattern:** REP (synchronous request/reply)
- **Model:** [`Twist2D`](../packages/core/src/stretch3_zmq/core/messages/twist_2d.py)

Commands a single blocking base move. Exactly one of `linear` or `angular` may be non-zero per
request; providing both non-zero values is an error. The server blocks until the motion
completes, then replies.

**Request:** single-frame msgpack-encoded `Twist2D`

```
send(msgpack.packb({"linear": float, "angular": float}))
```

| Field     | Unit  | Description                                              |
|-----------|-------|----------------------------------------------------------|
| `linear`  | m     | Distance to translate forward (negative = backward)      |
| `angular` | rad   | Angle to rotate in place (positive = counter-clockwise)  |

**Reply:** single-frame plain string

```
recv_string() → "ok"              on success
             → "error: <message>" on failure (e.g. both fields non-zero)
```

---

### arducam — Arducam RGB Publisher

- **Address:** `tcp://*:6000`
- **Pattern:** PUB
- **Topics:** none (topic-less)

Publishes RGB frames from the Arducam OV9782 USB camera (default: 1280×720 @ 30 fps,
`/dev/video4`).

**Multipart frames:**

```
[0] timestamp  — 8 bytes, nanoseconds since epoch
[1] payload    — raw RGB frame bytes (numpy ndarray.tobytes())
```

---

### d435if — RealSense D435i Publisher

- **Address:** `tcp://*:6001`
- **Pattern:** PUB
- **Topics:** `rgb`, `depth`

Publishes color and depth frames from a RealSense D435i camera (default: 640×480 @ 30 fps).
Subscribers should filter by topic.

**Multipart frames:**

```
[0] topic      — b"rgb" or b"depth"
[1] timestamp  — 8 bytes, nanoseconds since epoch
[2] payload    — raw frame bytes (numpy ndarray.tobytes())
```

---

### d405 — RealSense D405 Publisher

- **Address:** `tcp://*:6002`
- **Pattern:** PUB
- **Topics:** `rgb`, `depth`

Publishes color and depth frames from a RealSense D405 camera (default: 640×480 @ 15 fps).
Same message format as d435if.

**Multipart frames:**

```
[0] topic      — b"rgb" or b"depth"
[1] timestamp  — 8 bytes, nanoseconds since epoch
[2] payload    — raw frame bytes (numpy ndarray.tobytes())
```

---

### tts — Text-to-Speech Input

- **Address:** `tcp://*:6101`
- **Pattern:** REP (synchronous request/reply)

Receives plain UTF-8 text strings and synthesizes speech using the configured TTS provider
(`fish_audio` or `elevenlabs`). Audio is played directly on the robot's speaker. Empty or
whitespace-only strings are silently ignored.

The server replies immediately with a `job_id` (nanosecond timestamp string) before starting
synthesis, so the client can track progress on the `tts_status` port.

**Request:** single-frame plain string

```
send_string(text)
```

**Reply:** single-frame plain string

```
recv_string() → job_id: str   (nanosecond timestamp, e.g. "1740000000000000000")
```

Status updates for each job are published on the separate `tts_status` port (see below).

---

### tts_status — TTS Job Status Publisher

- **Address:** `tcp://*:6102`
- **Pattern:** PUB
- **Topics:** none (topic-less)

Publishes lifecycle events for each TTS job. Clients that need to know when speech has
finished (e.g., to sequence actions) should subscribe here.

**Multipart frames:**

```
[0] job_id  — nanosecond timestamp of when the job was received, encoded as ASCII string
              (e.g., b"1740000000000000000")
[1] status  — b"started" | b"done" | b"error"
```

---

### asr — Automatic Speech Recognition

- **Address:** `tcp://*:6103`
- **Pattern:** REP (synchronous request/reply)
- **Timeout:** `config.asr.timeout_seconds` (default: 10 s)

Transcribes speech from the robot's microphone. The client sends any string to trigger
recording; the server blocks until transcription is complete (or the timeout expires) and
replies with the transcribed text.

**Request:** single-frame plain string (content ignored)

```
send_string(trigger)   → any non-empty string
```

**Reply:** single-frame plain string

```
recv_string() → transcribed_text: str   (empty string on error or timeout)
```
