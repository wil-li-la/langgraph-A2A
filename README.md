# LangGraph A2A — Monorepo

A **medication delivery robot** system built as a monorepo with a Python backend (LangGraph + A2A Protocol) and a Next.js dashboard frontend.

```
├── backend/     Python LangGraph A2A agent server
├── frontend/    Next.js Robot Task Dashboard
└── docker-compose.yml
```

## Quick Start

### Backend

```bash
cd backend
pip install -e .
python -m app --host localhost --port 9999
```

### Frontend

```bash
cd frontend
pnpm install
pnpm dev
# Open http://localhost:3000
```

The dashboard will automatically connect to the backend at `http://localhost:9999` and display the live LangGraph workflow. If the backend is unavailable, it falls back to mock data.

### Docker (Both Services)

```bash
# Copy and configure environment
cp backend/.env.example backend/.env

# Start both services
docker-compose up -d

# Backend: http://localhost:9999
# Frontend: http://localhost:3000
```

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/.well-known/agent-card.json` | GET | A2A agent metadata (primary) |
| `/.well-known/agent.json` | GET | A2A agent metadata (legacy alias) |
| `/` | POST | A2A JSON-RPC endpoint (`message/send`) |
| `/api/workflow` | GET | LangGraph graph structure (nodes + edges) |
| `/api/workflow/execute` | POST | Trigger medication delivery workflow |

## Documentation

- [Backend README](./backend/README.md) — Architecture, API details, environment config
- [Frontend Design System](./docs/ui-design-system.md) — Colors, typography, components

## License

This project is provided as-is for educational and development purposes.
