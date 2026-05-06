# LangGraph A2A — Monorepo

A **medication delivery robot** system built as a monorepo with a Python backend (LangGraph + A2A Protocol) and a Next.js dashboard frontend.

```
├── backend/     Python LangGraph A2A agent server (Python 3.12)
└── frontend/    Next.js Robot Task Dashboard (Node 20 + pnpm) — static export, hosted on Cloudflare Pages
```

## Deployment topology

- **Frontend** — hosted 24/7 on **Cloudflare Pages** as a static export (`output: 'export'` in `next.config.mjs`). Auto-builds on every push to `main`. Has zero server-side features, so no laptop uptime is required to serve the dashboard.
- **Backend** — runs on the lab laptop, port `9999`. Exposed publicly via a **Cloudflare Tunnel** at `stretch-api.<your-domain>`. The Pages-built dashboard hits this URL via `NEXT_PUBLIC_API_URL` (baked into the build).
- **Robot connection** — optional add-on. The dashboard's Robot IP input pre-fills with the lab robot's LAN IP (`192.168.1.38`) but the user still has to click Connect — workflow display itself does not require a robot.

## Local development (backend only)

```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e .
python -m app --host localhost --port 9999

# Test workflow directly (bypasses A2A)
python -m app.healthcare.medication_delivery 張小明 阿斯匹靈
```

Required env vars — copy from `.env.example` and fill in:

```
model_source=google          # or openai
GOOGLE_API_KEY=your_key      # if model_source=google
OPENAI_API_KEY=your_key      # if model_source=openai
ROBOT_IP=<robot-lan-ip>      # passed to cure skills
PUBLIC_URL=https://stretch-api.your-domain.com   # advertised in AgentCard
```

See [backend/INSTALL.md](./backend/INSTALL.md) for full install instructions (including the `cure` and `stretch3-zmq` private dependencies).

For local frontend development against this backend, see [frontend/README.md](./frontend/README.md). The deployed Pages build does **not** depend on running `pnpm dev` locally.

## Cloudflare Tunnel setup (backend exposure)

Required so the production Pages dashboard (and any iPad/phone) can reach the backend over HTTPS — and so iOS Safari's `webkitSpeechRecognition` / `getUserMedia` work (both gated behind a secure context).

Requires a Cloudflare account + a domain managed on Cloudflare.

### One-time setup

```bash
# 1. Install cloudflared
brew install cloudflared
cloudflared tunnel login               # opens browser; select your domain

# 2. Create a tunnel
cloudflared tunnel create robot-dev-mac
# → writes ~/.cloudflared/<tunnel-id>.json

# 3. Route the API hostname to this tunnel
cloudflared tunnel route dns robot-dev-mac stretch-api.your-domain.com
```

Create `~/.cloudflared/config.yml`:

```yaml
tunnel: <your-tunnel-id>
credentials-file: /Users/<you>/.cloudflared/<your-tunnel-id>.json
edge-ip-version: "4"                   # force IPv4 if your IPv6 is flaky

ingress:
  - hostname: stretch-api.your-domain.com
    service: http://localhost:9999
    originRequest:
      connectTimeout: 30s
      tcpKeepAlive: 30s
      noHappyEyeballs: true
  - service: http_status:404
```

The dashboard hostname (e.g. `stretch-dashboard.your-domain.com`) is **not** in this config — Cloudflare Pages serves it directly. Set the Pages project's environment variables:

```
NEXT_PUBLIC_API_URL=https://stretch-api.your-domain.com
```

`NEXT_PUBLIC_ROBOT_HOST` is optional — if unset, the dashboard pre-fills the Robot IP input with the lab default (`192.168.1.38`). Set it explicitly only to override that default for a different deployment.

### Daily run (two terminals on the laptop)

```bash
# Terminal 1 — tunnel
cloudflared tunnel run robot-dev-mac

# Terminal 2 — backend
cd backend && source .venv/bin/activate && python -m app --host localhost --port 9999
```

That's it. The frontend is already live on Pages.

> **Note:** Cloudflare's free plan has a ~100 s idle-connection timeout. Our SSE workflow stream emits events frequently enough that this rarely matters; if a long-running node goes silent for >100 s the stream will drop and the paused/resume flow can recover it.

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/.well-known/agent-card.json` | GET | A2A agent metadata (primary) |
| `/agent.json` | GET | A2A agent metadata (legacy alias) |
| `/` | POST | A2A JSON-RPC (`message/send`) |
| `/api/workflow` | GET | LangGraph graph structure (nodes + edges) |
| `/api/workflow/execute` | POST | One-shot workflow execution (blocking) |
| `/api/workflow/execute/stream` | POST | SSE streaming execution — accepts optional `start_from: <node_id>` |
| `/api/workflow/stop` | POST | Request graceful stop; pauses after current node finishes |
| `/api/workflow/resume` | POST | Resume a paused workflow from a specified node |
| `/api/workflow/input` | POST | Submit browser-captured voice/text input to a waiting `check_identity` node |
| `/api/workflow/reset` | POST | Return robot to origin and clear paused sessions |
| `/ws/teleop?robot=ws://...` | WS | Transparent WebSocket relay to the robot (status + camera frames) |

## Robot Hardware

The backend communicates with a Hello Robot Stretch 3 over ZeroMQ via the `cure` skills library. The driver must be running on the robot before any hardware workflow executes:

```bash
ssh stretch-se3-3099.local -l hello-robot
cd Desktop/stretch3-zmq/
uv run python -m stretch3_zmq.driver --config config.yaml
```

> **Note:** The Nav2 goto service (port 5557) is not yet implemented in the driver — see [CLAUDE.md](./CLAUDE.md) for the TODO spec.

## Documentation

- [Backend README](./backend/README.md) — Architecture, API details, LangGraph workflow
- [Backend INSTALL](./backend/INSTALL.md) — Full install instructions including private dependencies
- [Frontend README](./frontend/README.md) — Dashboard components, API client

## License

This project is provided as-is for educational and development purposes.
