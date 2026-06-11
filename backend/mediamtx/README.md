# MediaMTX — RTSP fan-out for the lab cameras

Pulls H.264 RTSP from each camera host (single source of compression), re-publishes the **same H.264 bytes** to browsers as **WebRTC** (sub-100 ms latency) and **HLS-LL** (broader compatibility). No transcoding here.

This is the dashboard-side half of the industrial multi-camera pipeline. The camera-side half lives in [`wil-li-la/ED305_pylon_ros#2`](https://github.com/wil-li-la/ED305_pylon_ros/issues/2) — until that issue ships, MediaMTX has nothing upstream to pull from. The config is staged for the switchover.

## Why MediaMTX (not a Python service)

`cam_bridge` does the right thing for an MJPEG upstream but it has to JPEG-decode and re-encode H.264 for every camera, which caps throughput at the GPU/CPU encoder budget regardless of viewer count. Once cameras emit H.264 at the source, the dashboard's only remaining job is **remux + transport**, which is exactly what MediaMTX is. It's a single Go binary, YAML-configured, production-tested in security-camera deployments, native WebRTC/HLS/RTSP/RTMP/SRT in and out. No code on our side to maintain.

If you need to do anything custom (analytics, recording, motion detection, watermarks), MediaMTX has a Python hook API. We can build *on top of* it instead of *replacing it*.

## Install + run

```bash
cd backend/mediamtx
./install.sh                          # downloads pinned binary
./run.sh                              # foreground, ^C to stop
```

Endpoints once the camera host's RTSP server is up:

| Use | URL pattern |
|---|---|
| Browser WebRTC (preferred) | `https://<this-host>:8889/cam_<N>/whep` |
| Browser HLS (fallback) | `https://<this-host>:8888/cam_<N>/index.m3u8` |
| Inference / GStreamer pull | `rtsp://<this-host>:8554/cam_<N>` |

### TLS cert (mkcert)

Cert + key live at `../cam_bridge/cert.pem,key.pem` and are issued by [mkcert](https://github.com/FiloSottile/mkcert)'s local root CA so every browser, `curl`, and Node process on this machine trusts them with no per-user click-through.

Bootstrap once per dev machine:

```bash
sudo apt install -y mkcert libnss3-tools
mkcert -install                                # adds root CA to system + Chrome/Firefox NSS db (restart browser)
cd backend/cam_bridge
mkcert -cert-file cert.pem -key-file key.pem \
  localhost 127.0.0.1 192.168.1.47 192.168.1.100 hcis-s28 hcis-s28.local ::1
pkill mediamtx && cd ../mediamtx && ./run.sh   # pick up new cert
```

SAN list must include every hostname/IP a viewer might use (LAN access from phones/tablets needs the LAN IP, not just `localhost`). Re-run `mkcert -cert-file …` to add names later; no need to re-install the root CA.

Cert expires in ~2.25 years (mkcert default). On expiry, re-run the same `mkcert -cert-file …` command + restart MediaMTX.

## Wait state — what to expect before the host repo's issue lands

You'll see retry-loop logs like:

```
ERR [path cam_3] [RTSP source] not connected: dial tcp 192.168.1.13:8554: connect: connection refused
```

That's expected. MediaMTX itself stays healthy and serves the API; paths just have no source data yet. Once issue #2 lands and `rtsp://192.168.1.13:8554/cam_<N>` is live, MediaMTX will connect on its own retry tick — no restart needed.

## Operations

```bash
# probe the WebRTC WHEP endpoint
curl -k -X OPTIONS https://localhost:8889/cam_3/whep

# pull RTSP locally to verify the fan-out
ffprobe -rtsp_transport tcp rtsp://localhost:8554/cam_3

# show every active path
curl -s http://localhost:9997/v3/paths/list   # only if you enable api: yes in mediamtx.yml
```

## Frontend wiring (deferred)

When the host-side issue lands and MediaMTX is producing live streams, set:

```env
NEXT_PUBLIC_MEDIAMTX_WEBRTC_URL=https://localhost:8889
# or, if WebRTC is unavailable in the target environment:
NEXT_PUBLIC_MEDIAMTX_HLS_URL=https://localhost:8888
```

Then swap `<video src="…h264">` (current cam_bridge mode) for either:
- WebRTC: a small WHEP client (~50 lines of JS using the standard `RTCPeerConnection` + a POST to the WHEP endpoint).
- HLS-LL: `hls.js` library or Safari's native HLS support.

See `backend/cam_bridge/MIGRATION.md` for the exact frontend switch.

## Production deployment

For 24/7 use, drop in a systemd unit (or run inside the existing systemd stack on the lab laptop):

```ini
[Unit]
Description=MediaMTX RTSP fan-out
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/home/helin/Documents/github-xiang/langgraph-A2A/backend/mediamtx/run.sh
Restart=on-failure
RestartSec=2
User=helin
WorkingDirectory=/home/helin/Documents/github-xiang/langgraph-A2A/backend/mediamtx

[Install]
WantedBy=multi-user.target
```
