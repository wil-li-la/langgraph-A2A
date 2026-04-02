# LangGraph A2A — Monorepo

A **medication delivery robot** system built as a monorepo with a Python backend (LangGraph + A2A Protocol) and a Next.js dashboard frontend.

```
├── backend/     Python LangGraph A2A agent server (Python 3.12)
├── frontend/    Next.js Robot Task Dashboard (Node 20 + pnpm)
└── docker-compose.yml
```

## Quick Start

### Backend

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
```

See [backend/INSTALL.md](./backend/INSTALL.md) for full install instructions (including the `cure` and `stretch3-zmq` private dependencies).

### Frontend

```bash
cd frontend
pnpm install
pnpm dev
# Open http://localhost:3000
```

The dashboard automatically connects to the backend at `http://localhost:9999` and displays the live LangGraph workflow. Falls back to mock data if the backend is unavailable.

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/.well-known/agent-card.json` | GET | A2A agent metadata (primary) |
| `/agent.json` | GET | A2A agent metadata (legacy alias) |
| `/` | POST | A2A JSON-RPC (`message/send`) |
| `/api/workflow` | GET | LangGraph graph structure (nodes + edges) |
| `/api/workflow/execute` | POST | Trigger medication delivery workflow |
| `/api/workflow/execute/stream` | POST | SSE streaming execution (node_start, node_end, log, done, error) |

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
