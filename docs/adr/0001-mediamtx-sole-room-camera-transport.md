# MediaMTX is the sole transport for room cameras

**Status:** accepted (2026-06-01)

We serve the 16 ED305 room cameras to the dashboard exclusively through MediaMTX
(a single Go binary) re-muxing per-camera H264 RTSP into WebRTC (WHEP, ~80 ms on
LAN) and HLS-LL (~400 ms, Cloudflare-Tunnel-friendly). We deleted the prior
fallbacks — `cam_bridge/` (Python + per-camera NVENC transcode), `room_cameras/`
(ROS2→MJPEG), and the direct `web_video_server` MJPEG path — because once the
cameras emit H264 RTSP at the source there is nothing left to transcode, so every
one of those layers was pure GPU/CPU cost and bespoke glue for zero benefit.

## Consequences

- MediaMTX needs its own TLS cert; we decoupled it from the borrowed
  `cam_bridge/cert.pem` (now `backend/mediamtx/cert.pem`, gitignored, mkcert-issued)
  so the bridge could be removed.
- WebRTC ICE is pinned to interface `eno2` (`webrtcIPsFromInterfacesList`) to avoid
  MediaMTX advertising docker-bridge candidates — without this, random tiles go
  black on a host that has `docker0`/`br-*` interfaces.
- Off-LAN/tunnel WebRTC would require STUN in `webrtcICEServers2` (empty by design
  for LAN-only); HLS already traverses the tunnel as plain HTTP.
- There is no longer a no-RTSP fallback: if the upstream camera hosts stop emitting
  RTSP, the dashboard tiles go dead rather than degrading to MJPEG. Accepted given
  RTSP-at-source is the standing architecture.
