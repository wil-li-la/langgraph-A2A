# Simplification plan

Produced 2026-06-01 from a docs-alignment pass. Goal: cut accreted/legacy paths
so the repo matches the current architecture (MediaMTX room cameras, on-robot
nav, two control planes). **No code was deleted in producing this** — each item
below is a proposal with a confidence gate. Execute top-down; verify gated items
before cutting.

Confidence legend: 🟢 safe (orphaned, evidence in-repo) · 🟡 gated (needs a live
check first) · 🔵 keep (intentional, documented here so it's not mistaken for cruft).

---

## ⭐ Perception consolidation — yolo_worker REPLACES the ROS chain (user decision 2026-06-01)

The 3-stage ROS YOLO pipeline existed to feed (a) the dashboard SSE and (b) Nav2
obstacle layers. **Nav2 is dead** (8b), so the dashboard is the only consumer —
which the single-process `yolo_worker.py` (direct ZMQ→GPU→`/api/detect/inject`→SSE)
serves directly. Collapse 3 stages → 1.

- **🟢 Cut (replaced):** `backend/nav_bridge/yolo_world_node.py`,
  `backend/nav_bridge/detection_bridge.py`, `backend/app/api/detect_zmq_consumer.py`
  (+ its wiring at `__main__.py:189`), and the 4 ROS detection windows in `start.sh`
  (`yolo_detect`, `detect_bridge`, `yolo_wrist`, `detect_bridge_wrist`).
- **🔵 Keep (now load-bearing):** `/api/detect/inject` (`detect_stream.py:223-257`) —
  it is `yolo_worker`'s ingress, NOT orphaned. (Reverses the earlier delete call.)
- **🔧 Fix before it ships as the feature:**
  - Repoint head capture `6011` → **6000** (arducam; 6011 deleted post-FUNMAP).
    Match 6000's actual wire format (topic-less single-part — verify against the
    live socket).
  - Add **wrist (6002)** capture so it covers both tiles the ROS path did.
  - Add 1–2 `yolo_worker` windows to `start.sh` to replace the 4 removed ROS windows.
- Still runs inside `isaac_ros_dev` (needs torch+CUDA+ultralytics) — that dep is
  unchanged; only the ROS topic hop is removed.
- **Head-capture repoint (folds in here).** `_capture_head_frame` + `take_photo` +
  the detect_tools "head" path in `app/tools/stretch_tools.py` read the dead
  `head_color`/6011 (single-part msgpack JPEG). Live head cam = **arducam 6000**,
  which uses a **different wire format** (multipart raw, per `yolo_world_node`).
  Repoint + rewrite the decode (not a port swap); verify against the live socket.
  `_DEFAULT_PORTS` dead entries annotated 2026-06-01 but kept (readers would
  KeyError). **Separately:** `cure.skills.grasp` (pip dep) hardcodes the dead
  `d435if` → `pick_up`/`pickup_med` fails live until cure is patched robot-side —
  out of this repo's scope, track in the cure repo.

---

## 🟢 Safe cuts — orphaned, evidence in-repo

1. **`scripts/verify_yolo_world_live.py`** — references the old `yolo_worker`
   behavior; re-point or drop once `yolo_worker` is repointed to 6000.
2. **`docs/streaming_docs/`** — untracked exact duplicate of the tracked root
   `streaming_docs/` (same 3 files). Delete the dup; keep one canonical copy.
3. **`.bak` junk** — `backend/cam_bridge/cert.pem.selfsigned.bak`,
   `key.pem.selfsigned.bak`, `backend/mediamtx/mediamtx.yml.bak`. Untracked.
   (Cert `.bak`s die with `cam_bridge/` cut anyway — item 7.)
4. **Backend-root scratch scripts** — `backend/navigate.py`,
   `backend/test_navigate.py`, `backend/test_navigate_forward.py`,
   `backend/test_nvblox_nav.py`. Tracked, but ad-hoc (no `tests/` dir, no test
   framework per CLAUDE.md). Move to `scripts/` or delete. Confirm none is a
   documented manual-verification entry point before deleting. ✅ DONE 2026-06-01.
5. **`scripts/_out/`** — 2.7M of verification screenshots (artifacts). ✅ gitignored
   2026-06-01.
6. **`template.py`** (root, untracked) — stray stretch3-zmq client snippet,
   referenced by nothing. Move to `scripts/` or delete; harmless either way.
   `backend/a2a_template_client.py` is an intentional A2A client example — keep.

---

8b. **Lab nav stack — CONFIRMED DEAD (user, 2026-06-01).** The contradiction below
   is resolved: on-robot FUNMAP fully replaced the lab stack. `nav.py` docstring
   was *correct*. **🟢 Cut:** `backend/nav_bridge/run_nav.sh`, `launch/nav.launch.py`,
   `sensors_bridge.py`, `cmdvel_bridge.py`, `nav_service.py`, the `config/`+`patches/`
   for nvblox/Nav2, and the `start.sh` **nav window** (`run_nav.sh` line).
   Combined with the ⭐ perception consolidation (which cuts `yolo_world_node.py` +
   `detection_bridge.py`), `backend/nav_bridge/` ends up holding **only**
   `yolo_worker.py`. → Rename the dir `backend/nav_bridge/` → `backend/perception/`
   (it's neither nav nor a bridge anymore).

---

## 🟡 Gated cuts — verify the precondition first

7. **Room-camera fallbacks → MediaMTX only** — ✅ EXECUTED 2026-06-01. Deleted
   `cam_bridge/` + `room_cameras/`, stripped h264/mjpeg modes from the grid,
   renamed `PylonCamerasGrid`→`RoomCamerasGrid` (file `room-cameras-grid.tsx`),
   removed the 4 dead env vars + 2 `start.sh` windows. Frontend typechecks clean.
   *(decision made; MediaMTX is source of truth)*.
   Canonical transport is MediaMTX (WebRTC LAN / HLS tunnel). Targets:
   - `backend/cam_bridge/` (Python+NVENC h264 bridge)
   - `backend/room_cameras/` (ROS2→MJPEG, port 9997)
   - `PylonCamerasGrid` modes `h264` (cam_bridge) and `mjpeg` (direct), plus the
     `RoomCamerasGrid` component (`app/cameras/page.tsx:19` fallback branch)
   - env vars `NEXT_PUBLIC_CAM_BRIDGE_URL`, `NEXT_PUBLIC_ROOM_CAMERAS_URL`,
     `NEXT_PUBLIC_PYLON_CAMS_{LEFT,RIGHT}_URL`

   **Both preconditions CLEARED 2026-06-01 (agent-verified) → now 🟢, no gate left:**
   - **(a) TLS cert decoupled — DONE.** `cert.pem`/`key.pem` copied into
     `backend/mediamtx/`, all 4 `mediamtx.yml` cert lines repointed, `run.sh` got
     `cd "${SCRIPT_DIR}"` (mediamtx resolves cert paths relative to CWD). Deleting
     `cam_bridge/` no longer breaks MediaMTX TLS.
   - **(b) Streaming verified — DONE.** All 13 room cams READY (h264 1920x1080@30);
     HLS `index.m3u8`→200, WHEP→204, `ffprobe rtsp://localhost:8554/right_cam_3`
     and `left_cam_7` both real h264 1080p30.
   - **New follow-ups:** commit (or gitignore+document) the untracked
     `backend/mediamtx/cert.pem`/`key.pem`; MediaMTX left running (pid was 3559587).

   **Optional rename (post-cut):** the surviving component is `PylonCamerasGrid`,
   but CONTEXT.md fixes the family name as **Room cameras**. Rename
   `PylonCamerasGrid` → `RoomCamerasGrid` (after deleting the old one) to match.

8. **Lab nav stack** — ⛔ SUPERSEDED by 8b above (user confirmed dead 2026-06-01).
   The contradiction below is kept only as the historical record of how it
   resolved.

   **Resolved contradiction (was: verify before cut):**
   - `stretch_server_goto_refactor.md` (2026-05-18): robot `goto` 5557 is *a proxy
     back to the lab `nav_service`* → lab stack = the planner.
   - `nav.py` docstring (2026-05-25): *"lab stack is gone, AMCL+map on robot."*
   - Memory (prior session): *"AMCL robot-side sidecar; nav_bridge legacy."*

   **Gate — one probe resolves it** (robot on, container up, stack launched):
   ```
   ros2 node list | grep -E "nav_service|amcl|nvblox"   # present ⇒ lab stack live
   # and on the robot: does goto run a local BasicNavigator, or REQ back to lab?
   ```
   If lab nodes are absent and goto plans on-robot → cut the lab stack and the
   `start.sh` nav window, fix the `nav.py` docstring (drop "gone" hedge → state
   fact). If present → lab stack is live; fix the `nav.py` docstring instead
   (it overclaims "gone"). **Do not cut on assumption.**

---

## 🆕 Surfaced by the lab-nav cut (2026-06-01) — decide

- **`/viz` rosbridge page now orphaned.** `frontend/app/viz/page.tsx` +
  `lib/ros-client.ts` + `hooks/use-ros-topic.ts` connect to `rosbridge_websocket`
  on :9090, which was launched **only** by the now-deleted `nav.launch.py`. With the
  lab stack gone, nothing serves 9090 → the page is dead. **Decision:** either cut
  the three files + the `NEXT_PUBLIC_ROSBRIDGE_WS_URL` env, or relaunch
  `rosbridge_websocket` standalone (e.g. add a window to `start.sh`) if `/viz` is
  still wanted. Not cut yet — out of the approved 1–4 batch.

## 🔵 Keep — intentional, not cruft

- **`backend/app/environment/`** — Phase 0 staging, intentionally isolated (nothing
  imports it). It is the **scaffolding for a planned DynaMem (Meta Robotics)
  integration** — dynamic spatio-semantic memory for open-vocab mobile
  manipulation — which has **not been started** (as of 2026-06-01). Keep; do NOT
  cut. Phase 1 (per its design spec) wires `EnvironmentStore` to replace
  `world_model.py` + `RobotGuard` state and adds an agent checkpointer.
- **`detect_tools.py` / `world_model.py` / `detect_stream.py`** — live VLM detection
  + scene-memory path for the Agent. Distinct from YOLO; keep.
- **`yolo_world_node.py` + `detection_bridge.py` + `detect_zmq_consumer.py`** — the
  live production YOLO pipeline (head + wrist), launched by `start.sh`. Keep.

---

## Doc drift to fix alongside (in CLAUDE.md)

- "Room cameras (ROS2 → MJPEG bridge)" section presents `room_cameras` as current —
  it's now a being-retired fallback; MediaMTX is canonical.
- "TODO: Robot-side goto service" section is **stale** — goto shipped 2026-05-18
  (`stretch_server_goto_refactor.md`); port 5557 exists, legacy velocity endpoint
  moved to 5559 as `goto_velocity`.

## 🐞 Bugs surfaced during the grill (not cuts — track/fix separately)

- **Identity check is a substring match.** `check_identity_node`
  (`workflows/medication_delivery.py:371`) does `if patient_name not in resp1`.
  A patient named `李明` is wrongly confirmed by `我是李明華` (substring, not exact).
  Fix: normalize + exact/token match. Deeper: the whole check is verbal
  self-attestation, not identity verification (see CONTEXT.md "Verbal name
  confirmation") — anyone who says the name passes. Real risk for a med-delivery
  robot; revisit if this leaves demo use.
- **Medication identity never checked.** `pickup_medication_node` sets
  `target_detected=True` unconditionally — no verification the picked item is the
  prescribed drug. Trusts the pharmacy location.
- **Head-frame capture broken** — see the ⭐ perception section (reads dead
  `head_color`/6011; live head cam is arducam 6000 with a different wire format).

## ADR candidates (offered, not yet written)

- *MediaMTX as sole room-camera transport* — hard-ish to reverse (deletes the
  NVENC bridge), surprising to a future reader (why no Python transcoder?), real
  trade-off (latency/GPU vs. a flag-day dependency on upstream RTSP). Qualifies.
