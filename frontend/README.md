# Frontend — Robot Task Dashboard

Next.js dashboard for visualizing and controlling the medication delivery robot workflow in real time.

## Stack

- **Next.js 15** (App Router, Turbopack)
- **React 19**
- **Tailwind CSS** + **shadcn/ui**
- **pnpm**

## Development

```bash
pnpm install
pnpm dev          # http://localhost:3000
pnpm build
pnpm lint         # ESLint + TypeScript
```

Set `NEXT_PUBLIC_API_URL` to point at the backend (defaults to `http://localhost:9999`).

## Structure

```
app/
├── page.tsx              # Main page — renders RobotDashboard
├── layout.tsx
└── globals.css

components/
├── robot-dashboard.tsx   # Top-level dashboard layout
├── connect-panel.tsx     # Robot selection + connection status + skills list
├── workflow-graph.tsx    # Live LangGraph visualization with active node highlighting
└── video-panel.tsx       # 4 camera streams (RGB + depth)

hooks/
└── use-workflow.ts       # All workflow state (fetch, execute, SSE streaming)

lib/
├── api.ts                # fetchWorkflow(), executeWorkflow(), executeWorkflowStream()
└── mock-data.ts          # Fallback data when backend is unavailable
```

## API Client

`lib/api.ts` connects to the backend:

| Function | Endpoint | Description |
|---|---|---|
| `fetchWorkflow()` | `GET /api/workflow` | Load graph structure |
| `executeWorkflow()` | `POST /api/workflow/execute` | One-shot execution |
| `executeWorkflowStream()` | `POST /api/workflow/execute/stream` | SSE streaming execution |

If the backend is unreachable, `fetchWorkflow()` falls back to mock data from `lib/mock-data.ts`.

## Notes

- `next.config.mjs` has `typescript: { ignoreBuildErrors: true }` — TypeScript errors don't block builds.
- No test framework is configured; verification is manual/integration.
