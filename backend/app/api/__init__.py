"""HTTP / SSE / WebSocket / A2A endpoint adapters.

Each module here exposes a list of Starlette `Route` (or a websocket handler,
or an A2A `AgentExecutor`) that `app.__main__` mounts on the running server.
Modules in this folder hold transport concerns only — business logic lives in
`app.workflows`, `app.agents`, `app.tools`, etc.
"""
