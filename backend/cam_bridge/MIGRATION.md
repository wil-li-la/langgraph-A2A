# cam_bridge → mediamtx migration

`cam_bridge` (this dir) is the **MJPEG-source bridge**: it pulls JPEG from `web_video_server`, transcodes to H.264 with NVENC per camera, fans out to browsers. It works, but every viewer adds GPU encoder load and the underlying JPEG round-trip wastes bandwidth.

When [`wil-li-la/ED305_pylon_ros#2`](https://github.com/wil-li-la/ED305_pylon_ros/issues/2) ships, cameras will emit H.264 RTSP at the source. At that point, **all transcoding on this host goes away** and the bridge collapses into a pure remuxer — which is exactly what MediaMTX is. See `../mediamtx/` for the staged replacement.

## What changes on flip day

| Layer | Before (cam_bridge mode) | After (mediamtx mode) |
|---|---|---|
| Camera host | publishes raw `sensor_msgs/Image` → web_video_server → MJPEG over HTTP | publishes H.264 RTSP per cam @ `rtsp://192.168.1.13:8554/cam_<N>` |
| Dashboard host | runs `cam_bridge` (Python + ffmpeg per cam) | runs `mediamtx` (single Go binary, YAML config) |
| GPU usage on dashboard | 1 NVENC session per cam (8 sessions for 8 cams) | **0 NVENC sessions** — no transcoding |
| CPU on dashboard | mjpeg_cuvid + NVENC pipeline | negligible (byte forwarding) |
| Browser transport | `<video src="…/cam/<src>/<N>/h264">` (chunked fMP4 over HTTP/2) | WebRTC WHEP (sub-100 ms) or HLS-LL (~500 ms) |
| Latency (LAN, 1080p) | ~500 ms | ~80 ms (WebRTC), ~500 ms (HLS-LL) |
| Internal bandwidth | ~5 Mbps/cam (MJPEG) | ~2 Mbps/cam (H.264) |
| Browser per-origin cap | sidestepped by HTTP/2 (one origin) | sidestepped by HTTP/2 / WebRTC (one origin) |

## Frontend env-var diff

```diff
# frontend/.env.local
-NEXT_PUBLIC_CAM_BRIDGE_URL=https://localhost:9998
+NEXT_PUBLIC_MEDIAMTX_WEBRTC_URL=https://localhost:8889
+# NEXT_PUBLIC_MEDIAMTX_HLS_URL=https://localhost:8888    # fallback only
 NEXT_PUBLIC_PYLON_CAMS_RIGHT_URL=https://192.168.1.13:8443
```

`NEXT_PUBLIC_PYLON_CAMS_{RIGHT,LEFT}_URL` still control which sides are rendered — their values move from "where to pull MJPEG from" to "marker that this side is enabled in the bridge config", same as today.

## Component change

In `frontend/components/pylon-cameras-grid.tsx`, the `StreamTile` element switches from:

```tsx
<video src={`${BRIDGE_URL}/cam/right/${cam.id}/h264`} autoPlay muted playsInline />
```

…to either:

```tsx
// WebRTC via WHEP — ~50 lines of new client code
<WhepVideo url={`${MEDIAMTX_WEBRTC_URL}/cam_${cam.id}/whep`} />
```

…or:

```tsx
// HLS-LL via hls.js (npm install hls.js)
<HlsVideo url={`${MEDIAMTX_HLS_URL}/cam_${cam.id}/index.m3u8`} />
```

WebRTC is the better default for live monitoring; HLS-LL is a safety net for environments where WebRTC ports/protocols are blocked.

## Phased switchover (no big-bang)

The switchover doesn't have to be all-or-nothing. The dashboard can talk to both simultaneously during the cutover:

1. **Camera host ships issue #2.** RTSP source live at `rtsp://192.168.1.13:8554/cam_<N>`. Existing `web_video_server` keeps running unchanged.
2. **Start MediaMTX on the dashboard host.** It pulls from RTSP. `cam_bridge` keeps running too. Now there are two ways to view the same camera.
3. **Test MediaMTX endpoint in isolation.** Hit `https://localhost:8889/cam_3/whep` from a test page.
4. **Flip the env var** to point the dashboard at MediaMTX. Reload. Verify all tiles work.
5. **Stop and disable `cam_bridge`.** Leave the code in the repo as the documented MJPEG fallback.
6. **(Eventually)** `web_video_server` can be retired on the camera host, after any remaining ROS consumers migrate.

## What survives the migration

- `cam_bridge` codebase stays in the repo as the MJPEG fallback. Its `/snap/`, `/cam/<src>/<N>/mjpeg`, and `<img>`-compatible MJPEG endpoints remain useful for environments without WebRTC/H.264 (cheap embedded viewers, legacy tooling, debugging).
- The frontend's existing `<img>`-mode (when `NEXT_PUBLIC_CAM_BRIDGE_URL` is unset) keeps working against direct `web_video_server`.

## Until issue #2 lands

Don't enable MediaMTX in production yet — its RTSP sources will sit in retry-loop logs against a host that doesn't speak RTSP. It does no harm but it's noise. The `mediamtx.yml` config is checked in pre-staged; flip the switch when the upstream is ready.
