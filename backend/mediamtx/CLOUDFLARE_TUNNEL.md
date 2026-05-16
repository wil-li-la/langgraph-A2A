# Exposing MediaMTX via Cloudflare Tunnel

How to make the camera dashboard viewable at a public hostname (e.g. `https://stretch-dashboard.xiangen014.work`) without opening firewall ports.

## Architecture

```
[ browser, internet ]
      │
      │ HTTPS / HTTP/2
      ▼
[ Cloudflare edge — public TLS cert ]
      │
      │ encrypted tunnel
      ▼
[ cloudflared on lab laptop ]
      │
      │ https://localhost:8888  (noTLSVerify — self-signed)
      ▼
[ MediaMTX HLS endpoint ]
      │
      │ rtsp://192.168.1.13:8554/cam_<N>  (LAN, server-to-server)
      ▼
[ camera host RTSP server ]
```

WebRTC is intentionally **not** tunneled — it negotiates UDP via ICE, and Cloudflare Tunnel only proxies HTTP. HLS-LL gives ~200-400 ms latency through the tunnel which is plenty for monitoring.

## DNS + tunnel route

Assuming you already have a tunnel named e.g. `stretch-lab` for the backend at `stretch-api.xiangen014.work`:

```bash
# Add a DNS record routing the new hostname to the existing tunnel
cloudflared tunnel route dns stretch-lab stretch-cams.xiangen014.work
```

Append to the tunnel's `~/.cloudflared/config.yml`:

```yaml
ingress:
  # ... existing rules above ...

  - hostname: stretch-cams.xiangen014.work
    service: https://localhost:8888
    originRequest:
      # MediaMTX serves a self-signed cert internally; skip verification
      # since this hop is local-only.
      noTLSVerify: true
      # HLS-LL relies on chunked transfer + keep-alive; tunnel defaults
      # to 30s which can cut long-polled requests. 0 = unlimited.
      keepAliveTimeout: 0s

  # If you also want raw RTSP fan-out reachable externally (rare —
  # WebRTC and HLS cover all browser cases):
  #
  # - hostname: stretch-cams-rtsp.xiangen014.work
  #   service: rtsp://localhost:8554
  #   (note: requires cloudflared TCP-mode tunneling, separate setup)

  - service: http_status:404
```

Restart `cloudflared`:

```bash
sudo systemctl restart cloudflared
# or, if running interactively
cloudflared tunnel run stretch-lab
```

Verify externally:

```bash
curl -sI https://stretch-cams.xiangen014.work/cam_3/index.m3u8 | head -3
# expect: HTTP/2 200, content-type: application/vnd.apple.mpegurl
```

## Cloudflare Pages env (production frontend build)

In the Cloudflare Pages project for `stretch-dashboard.xiangen014.work`:

| Key | Value |
|---|---|
| `NEXT_PUBLIC_API_URL` | `https://stretch-api.xiangen014.work` |
| `NEXT_PUBLIC_MEDIAMTX_HLS_URL` | `https://stretch-cams.xiangen014.work` |
| `NEXT_PUBLIC_PYLON_CAMS_RIGHT_URL` | `https://stretch-cams.xiangen014.work` (any non-empty value — flags right side as enabled) |
| `NEXT_PUBLIC_ROBOT_HOST` | `192.168.1.38` |

**Leave `NEXT_PUBLIC_MEDIAMTX_WEBRTC_URL` empty** so the frontend picks HLS, not WebRTC.

After saving, trigger a new build (or push any commit to `main` to auto-build). The dashboard at `https://stretch-dashboard.xiangen014.work/cameras` should show all 8 tiles as `<video>` elements playing HLS, with the source label reading `hls: https://stretch-cams.xiangen014.work`.

## Resource use through the tunnel

- 8 cams × ~2 Mbps H.264 = ~16 Mbps egress from lab to Cloudflare edge per viewer.
- Cloudflare to viewer: same 16 Mbps over their internet connection.
- No transcoding anywhere — MediaMTX repackages H.264 NAL units into HLS fragments, cloudflared forwards bytes.

## Latency budget through the tunnel

| Hop | ~ms |
|---|---|
| Camera capture + x264 encode | 50 |
| LAN RTSP to MediaMTX | <5 |
| MediaMTX HLS-LL fragment buffer | 100 (one `hlsPartDuration`) |
| Cloudflare edge round-trip | 50-150 (depends on viewer location) |
| Browser HLS jitter buffer | 100-300 |
| **End-to-end glass-to-glass** | **~300-600 ms** |

For comparison, LAN WebRTC sits at ~100-150 ms. Tradeoff: HLS works through the tunnel without any extra infra (TURN, public IPs, etc.).

## Future: WebRTC over Cloudflare

If you need sub-200 ms latency externally, you'd need to:

1. Open UDP `:8189` on the lab router/firewall (port forward to the dashboard host)
2. Set `webrtcAdditionalHosts: [<public ip or ddns hostname>]` in `mediamtx.yml`
3. Add a Cloudflare DNS record (A or AAAA, not proxied) for the WebRTC hostname
4. Frontend uses `NEXT_PUBLIC_MEDIAMTX_WEBRTC_URL` instead of HLS

Cloudflare Realtime (paid product) is the no-firewall alternative but adds ongoing cost. For monitoring use, HLS through the existing tunnel is the right answer.

## Troubleshooting

```bash
# tunnel up?
curl -sI https://stretch-cams.xiangen014.work/cam_3/index.m3u8

# MediaMTX local healthy?
curl -k -sI https://localhost:8888/cam_3/index.m3u8

# RTSP source healthy?
ffprobe -rtsp_transport tcp rtsp://192.168.1.13:8554/cam_3 2>&1 | head -10

# Tunnel logs
sudo journalctl -u cloudflared -f
```

If MediaMTX returns 200 locally but the tunnel returns 502: cloudflared can't reach `https://localhost:8888`. Most likely cause: missing `noTLSVerify: true` (the self-signed cert fails verification). Less common: the MediaMTX `hlsAllowOrigin` not letting through (we set `"*"` in `mediamtx.yml`, so this is rare).

If HLS plays locally but stalls through the tunnel: bump `keepAliveTimeout` higher or set to `0s`. HLS-LL's blocking-playlist GETs can sit open for up to a part duration.
