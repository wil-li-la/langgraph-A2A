# Room camera architecture (May 2026 →)

The ED305 lab's overhead Basler cameras flow into the dashboard via an industrial-standard pipeline: **source-side H.264 encoding → RTSP → MediaMTX → WebRTC to browser**, with **zero transcoding on the dashboard host**. ROS is preserved as a parallel branch for legacy consumers but is no longer on the hot path.

## Pipeline

```
[ camera host: hcislab-camera-right, 192.168.1.13 ]
                                                            
   8× Basler ace2 (GigE, 1920×1080 BGR8 ~30 Hz)
            │
            ▼  pypylon InstantCamera grab  (one grabber per cam)
            │
            ├─── cv_bridge → rclpy → /cam_<N>/image_raw    ◄─── ROS branch (legacy)
            │                       (sensor_msgs/Image,         · web_video_server :8080 still works
            │                       RELIABLE, depth=5)         · ad-hoc ROS consumers (rqt, rosbag)
            │
            └─── Gst.Buffer.new_wrapped → appsrc → x264enc → ◄─── RTSP branch (hot path)
                 rtph264pay → GstRtspServer @ :8554/cam_<N>
                 (x264 tune=zerolatency veryfast, ~2 Mbps,
                  CPU-only — this host has no NVIDIA GPU,
                  set_shared(True) → one encoder per cam
                  regardless of viewer count)


[ dashboard host: lab laptop, RTX 4080 SUPER ]

   MediaMTX v1.9.3 (Go single binary, see backend/mediamtx/)
            │
            ├── pulls rtsp://192.168.1.13:8554/cam_<N>  ×8
            │   (one persistent RTSP-over-TCP connection per cam)
            │
            ├── RTSP fan-out @ :8554/cam_<N>          ← future inference consumers
            ├── HLS-LL @ :8888/cam_<N>/index.m3u8     ← browser fallback
            └── WebRTC WHEP @ :8889/cam_<N>/whep      ← browser primary (used today)
                (TLS via reused cam_bridge/cert.pem, ALPN h2 for the HTTP side)

   No transcoding here. MediaMTX repackages the same H.264 NAL units
   into different transport containers. Zero NVENC sessions.


[ browser ]

   /cameras (Next.js)
     ├── components/pylon-cameras-grid.tsx, MODE=webrtc
     ├── one <video> per cam
     └── lib/whep-client.ts: POST SDP offer to /whep, set srcObject from ontrack
   Chrome's H.264 decoder (typically NVDEC hw-accelerated on this GPU).
```

## Resource use today (8 cams, 1 viewer)

| Host | CPU | GPU encode (NVENC) | GPU decode (NVDEC) | Network out |
|---|---|---|---|---|
| Camera host (`hcislab-camera-right`) | ~8 cores @ x264 zerolatency veryfast for 8× 1080p30 (one shared encoder per cam) | 0 — host has no NVIDIA GPU | n/a | 8 × ~2 Mbps = ~16 Mbps |
| Dashboard host (laptop, RTX 4080 SUPER) | negligible — MediaMTX is byte-forwarding | **0** (no transcoding) | 8× hw-accel decode in Chrome | 0 (sends to local browser) |

## Latency

| Hop | ~ms |
|---|---|
| Camera capture → pypylon grab | 33 (one frame @ 30 fps) |
| x264 zerolatency encode | 10–20 |
| RTSP over LAN to MediaMTX | <5 |
| MediaMTX repackage to WebRTC | <5 |
| WebRTC jitter buffer + decode | 50–100 |
| **End-to-end glass-to-glass** | **~100–150 ms** |

For comparison, the prior MJPEG-via-cam_bridge architecture sat at ~500–800 ms with significantly higher CPU+GPU load on the dashboard side.

## ROS layer: status

The ROS publisher is kept in parallel with the RTSP server — both fed from the same single `pypylon` grab callback in `pylon_multi_cam`. The dashboard never goes through ROS anymore; ROS continues to serve:

- `rqt_image_view`, `rosbag record`, ad-hoc OpenCV scripts
- `web_video_server :8080` (legacy MJPEG-over-HTTP)
- Any future ROS-based pipelines (Nav2 obstacle annotations, perception, etc.)

When the last ROS consumer migrates (or is rewritten to consume RTSP directly via `rtspsrc`), the ROS publish branch can be deleted from `pylon_multi_cam`'s grab callback — single-grabber design makes that a one-line change.

## Frontend mode precedence

`frontend/components/pylon-cameras-grid.tsx` picks rendering mode by env-var precedence. Set the relevant env to choose:

| Mode | Env var | Element | Latency | When to use |
|---|---|---|---|---|
| `webrtc` (current) | `NEXT_PUBLIC_MEDIAMTX_WEBRTC_URL` | `<video srcObject=…>` via WHEP | ~150 ms | Default — best for everything |
| `h264` (legacy bridge) | `NEXT_PUBLIC_CAM_BRIDGE_URL` | `<video src=…fmp4…>` | ~500 ms | Fallback if MediaMTX or RTSP source is down |
| `mjpeg` (direct) | `NEXT_PUBLIC_PYLON_CAMS_RIGHT_URL` only | `<img src=…mjpeg…>` | ~200 ms | Last resort — cheap embedded viewers, no WebRTC available |

`NEXT_PUBLIC_PYLON_CAMS_{RIGHT,LEFT}_URL` always drive **which sides** are rendered; their values are also the MJPEG-direct URL when nothing else is configured.

## Legacy components (kept as fallback, not on hot path)

- `backend/cam_bridge/` — Python Starlette + hypercorn service. Pulls MJPEG from `web_video_server`, transcodes H.264 via NVENC, serves over HTTP/2 with shared per-cam transcoder pool. Documented in `backend/cam_bridge/README.md` + `MIGRATION.md`. **Disable in production**; keep code for fallback scenarios.
- `backend/room_cameras/` — ROS2-rclpy → MJPEG bridge for the older `chen1328/ED305_pylon_viewer` publisher. Only relevant if that publisher ever comes back; superseded by `wil-li-la/ED305_pylon_ros`.

## Camera-host repo cross-references

- [`wil-li-la/ED305_pylon_ros#1`](https://github.com/wil-li-la/ED305_pylon_ros/issues/1) — `web_video_server` wedge bug. Mitigated by the new RTSP path (we no longer hit `/snapshot`). Issue still open: external watchdog to auto-restart `pylon-cameras` on wedge.
- [`wil-li-la/ED305_pylon_ros#2`](https://github.com/wil-li-la/ED305_pylon_ros/issues/2) — RTSP H.264 source. Shipped in commit `ad30615` (May 2026, unified single-grabber design).

## Adding the left-side cameras (when that host comes online)

1. **Camera-side**: deploy the same `pylon_multi_cam` setup on the left host. RTSP server publishes the 8 left-side cams at `rtsp://<left-host-ip>:8554/cam_<N>`.
2. **Dashboard `backend/mediamtx/mediamtx.yml`**: add a second block of paths. Naming convention: prefix with side to keep WHEP URLs unique.
   ```yaml
   paths:
     # ... existing right-side cam_3, cam_6 etc. (keep as-is)
     left_cam_3:
       source: rtsp://<left-host>:8554/cam_3
       sourceOnDemand: no
     left_cam_6:
       source: rtsp://<left-host>:8554/cam_6
       sourceOnDemand: no
     # ... etc
   ```
   Restart MediaMTX (`pkill mediamtx; ./run.sh`). Sources discovered automatically on retry.
3. **Frontend `frontend/.env.local`**: set `NEXT_PUBLIC_PYLON_CAMS_LEFT_URL=https://<left-host-ip>:8443` (or any non-empty value — it just flags the side as enabled). Add the left side to the `MediaMTX path` naming logic in `pylon-cameras-grid.tsx` (the `cam_${id}/whep` path becomes `left_cam_${id}/whep` for the left side — about a 3-line tweak).
4. Hard-refresh `/cameras` — 16 tiles.

## Production deployment

For 24/7 use:

- **MediaMTX as systemd unit** — template in `backend/mediamtx/README.md`. Auto-restart on failure.
- **Cloudflare Tunnel** for external access. Tunnel terminates TLS, MediaMTX speaks plain HTTP behind it. Set `tls: false` in `mediamtx.yml`, `webrtcEncryption: no`, `hlsEncryption: no`. The tunnel gives browsers HTTP/2 with a real cert.
- **Frontend env on Cloudflare Pages**: `NEXT_PUBLIC_MEDIAMTX_WEBRTC_URL=https://cams.your-domain.com:8889`.

## Future cleanups (not blocking)

- **NVENC on the camera host** would cut its CPU use by ~80% — needs an NVIDIA GPU there, OR move encoding to a separate host between cameras and dashboard (defeats the no-transcoding-on-dashboard win, so probably not worth it).
- **H.265** for ~30% bandwidth reduction. Two-line GStreamer change on the camera host (`x264enc` → `x265enc`, `rtph264pay` → `rtph265pay`); MediaMTX supports HEVC in WebRTC for Chrome 107+.
- **Audio track** (silent AAC, ~1 kbps) — some browsers behave better with `<video>` when audio is present. Optional.
- **Inference path**: when ML lands on these cameras, consume the same RTSP source via GStreamer `rtspsrc ! rtph264depay ! nvh264dec ! nvvideoconvert` — frames land directly in CUDA memory. Zero re-encode, separate consumer from the dashboard.

## Why this design (vs. what was rejected)

| Rejected approach | Reason |
|---|---|
| MJPEG over HTTP (the original `web_video_server` + `cam_bridge` path) | Concurrency-limited (~6 cams per browser origin without HTTP/2), wedge bug under load, JPEG-on-demand burns server resources per request, ~5 Mbps/cam bandwidth bloat. |
| `cam_bridge` only (JPEG → NVENC transcoding) | Worked at small scale, but per-viewer NVENC sessions multiply. Adding a 16th cam pushes consumer-GPU session caps. CPU-side JPEG decode was the real bottleneck (~33% CPU per stream). |
| WebRTC via custom SFU | Sub-100 ms achievable but custom signaling/ICE/TURN plumbing is engineering overhead for what is essentially a monitoring dashboard. MediaMTX gives us 90% of the win with a pre-built YAML config. |
| GStreamer Python service (instead of MediaMTX) | More custom code to own and debug. MediaMTX is production-tested in security-camera deployments and exposes the exact endpoints we need (RTSP/HLS/WebRTC) out of the box. |

The pattern shipped is the same one used by NVIDIA DeepStream, Frigate, Genetec, and Milestone: source-side encoding, RTSP transport, edge re-mux for delivery, no transcoding mid-pipeline. Works the same for browser viewing today as for inference tomorrow.
