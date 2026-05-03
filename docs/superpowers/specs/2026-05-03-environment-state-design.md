# Environment State — Single Source of Truth for the Robot's World

**Date:** 2026-05-03
**Goal:** Define one in-process store that owns the canonical runtime view of the robot's environment — kinematic state, perception cache, world catalog, per-task counters — and standard read/write conventions for both the scripted workflow and the LLM-driven agent. Isolated from the rest of the codebase for now; designed so perception sources (camera, ASR) and persistence (checkpointing, long-term memory) can plug in later without rewrite.

## Motivation

Runtime state today is fragmented:

- `app/safety/guard.py` holds per-task `location` / `holding` / `calls_made` in a contextvar.
- `app/tools/world_model.py` hardcodes the location and graspable-object catalogs.
- `app/mock_data.py` (about to be deleted) hardcoded the patient/medication catalog.
- `AgentState` TypedDict in `app/workflows/medication_delivery.py` carries another copy of `current_location`, `target_detected`, `identity_verified`, etc., scoped to a single LangGraph run.

Four owners, no single answer to "where is the robot right now and what does it see". The fragmentation is about to bite three concrete use cases:

1. **Camera identity match.** A camera will eventually publish "face_detected: person_X" events. The agent needs to ask: *is the person in front of me the one I'm delivering to?* Today there is no place for the camera service to write and the agent to read.
2. **Cross-task memory.** Resuming an interrupted delivery, or remembering "this patient prefers English", needs persistence beyond a single LangGraph run.
3. **Concurrent observability.** The dashboard polls `/api/agent/info` while a task runs. Today it sees only the snapshot the guard chose to expose; it cannot see perception or world state.

Removing `mock_data.py` is a forcing function: the scripted workflow's `MockDatabase.get_patient(name)` validation goes away, and we need to decide what (if anything) replaces the allow-list.

## Architecture — Three Layers

Hot, mostly-read state belongs in one place; cold, write-once-per-task in another; long-term across-tasks in a third. Map them to standard agentic-workflow primitives so we can swap backends later without changing call sites.

```
┌────────────────────────────────────────────────────────────────────┐
│                       LAYER 3 — Episodic Memory                    │
│              (LangGraph Checkpointer + Store, per task)            │
│   resumable runs · conversation history · "this patient said X"    │
│        backend: MemorySaver  →  SqliteSaver  →  PostgresSaver      │
└────────────────────────────────────────────────────────────────────┘
                              ▲   ▲
                              │   │  task start / end snapshots
                              │   │
┌────────────────────────────────────────────────────────────────────┐
│                    LAYER 2 — Environment Store                     │
│             (in-process singleton, lock-protected)                 │
│   ● kinematic: location, holding, battery_pct, joint_state         │
│   ● perception_cache: latest events with TTL                       │
│   ● task_context: id, started_at, calls_used, budget, target       │
│   ● world_catalog (handle to Layer 1)                              │
│         backend: in-memory  →  Redis (if multi-process)            │
└────────────────────────────────────────────────────────────────────┘
       ▲                    ▲                       ▲
       │ writes             │ writes                │ reads
       │                    │                       │
┌──────────────┐    ┌────────────────┐    ┌──────────────────────┐
│  Perception  │    │  Tools / Nodes │    │  Agent / Workflow    │
│  (camera,    │    │  (navigate,    │    │  (decision making,   │
│   ASR, IMU)  │    │   grasp, …)    │    │   safety guard)      │
└──────────────┘    └────────────────┘    └──────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│                     LAYER 1 — World Catalog                        │
│                    (config-loaded, read-only)                      │
│   locations · graspable object classes · known people (optional)   │
│       backend: YAML/JSON file  →  REST API  →  database            │
└────────────────────────────────────────────────────────────────────┘
```

### Why three layers, not one

| Concern | Why it can't share a layer |
|---|---|
| Kinematic state changes every second; episodic memory grows once per task. Different write rates → different backends. |
| World catalog is mostly read-only and shared across all tasks; episodic memory is per-task. |
| LangGraph's `Checkpointer` API is built around thread/run identity, not "the robot is here right now". Forcing kinematic state through it makes simple reads awkward. |
| Persistence requirements differ: lose kinematic state on restart (re-derive from sensors); never lose episodic memory. |

### Mapping to standard primitives

| Layer | Standard primitive | Library / pattern |
|---|---|---|
| 3. Episodic memory | LangGraph `BaseCheckpointSaver` (thread-scoped) + `BaseStore` (cross-thread) | `langgraph.checkpoint.{memory,sqlite,postgres}`, `langgraph.store.{memory,postgres}` |
| 2. Environment store | "Blackboard architecture" — central shared state, multiple agents read/write | Custom in-process; Redis if we need multi-process visibility |
| 1. World catalog | Knowledge base / ontology | YAML config today; could grow into a domain DB later |

This is the conventional split in robotics + agentic systems: a fast, lossy world model (Layer 2) is fed by perception, consulted by the agent, and snapshots into durable episodic memory (Layer 3) at task boundaries. The world catalog (Layer 1) is the schema the world model is consistent with.

## Data Model

All shapes Pydantic v2 models, defined in `app/environment/types.py`.

### Layer 2 — kinematic + perception

```python
class RobotKinematicState(BaseModel):
    location: str = "charging_dock"        # friendly name from world catalog
    holding: str | None = None             # object name or None
    battery_pct: float | None = None
    joint_positions: dict[str, float] = {}  # optional, populated by driver
    last_updated: datetime

class PerceptionEvent(BaseModel):
    kind: Literal["face_detected", "object_seen", "speech_heard", "obstacle"]
    payload: dict[str, Any]                # shape varies by kind
    confidence: float
    source: str                            # "camera_d435if", "deepgram", "lidar", …
    timestamp: datetime

    # Convenience for face_detected:
    # payload = {"person_id": "face_001", "name": "張小明" | None, "bbox": [...]}

class PerceptionCache(BaseModel):
    """Latest event per (kind, source). Older events evicted by TTL."""
    events: dict[tuple[str, str], PerceptionEvent]
    ttl_seconds: float = 5.0
```

### Layer 2 — task context

```python
class TaskContext(BaseModel):
    task_id: str                           # uuid; same id used by checkpointer
    started_at: datetime
    target: dict[str, Any] = {}            # task-specific, e.g. {"patient": "張小明", "medicine": "阿斯匹靈"}
    tool_calls_used: int = 0
    tool_call_budget: int = 30
    status: Literal["running", "paused", "done", "failed"] = "running"
```

`TaskContext.target` replaces the medication-specific `patient_name` / `medication_name` fields in `AgentState` — the env store doesn't care what the task is *about*, only that there is one. The scripted workflow continues to copy `target.patient` etc. into its TypedDict for backward compatibility.

### Layer 1 — world catalog

```python
class Location(BaseModel):
    friendly_name: str                     # "pharmacy"
    cure_target: str                       # "medicine"
    description: str = ""
    # future: x/y coords, room id, semantic tags

class GraspableClass(BaseModel):
    friendly_name: str                     # "medicine"
    cure_target: str
    aliases: list[str] = []

class KnownPerson(BaseModel):
    person_id: str                         # opaque id, e.g. "face_001"
    display_name: str = ""                 # "張小明" — optional, for greeting
    # future: face embedding, room assignment, allergy notes

class WorldCatalog(BaseModel):
    locations: dict[str, Location]
    graspable: dict[str, GraspableClass]
    people: dict[str, KnownPerson] = {}    # optional; empty by default
```

The catalog is loaded once at server start from `app/environment/world.yaml` (new file). Hot-reload deferred to a later spec.

### Composite snapshot

```python
class EnvironmentSnapshot(BaseModel):
    """Atomic copy returned by EnvironmentStore.snapshot(). Safe to serialize."""
    robot: RobotKinematicState
    perception: PerceptionCache
    task: TaskContext | None
    catalog_version: str                   # so callers detect catalog reloads
```

## Layer 2 API — `app/environment/store.py`

Single in-process singleton, lock-protected, no async (the lock is brief and contention is minimal). All mutations go through typed methods so the store can validate and broadcast events.

```python
class EnvironmentStore:
    # ---- read (lock-free, atomic) -------------------------------------
    def snapshot(self) -> EnvironmentSnapshot: ...
    def robot(self) -> RobotKinematicState: ...
    def perception(self, kind: str | None = None) -> list[PerceptionEvent]: ...
    def task(self) -> TaskContext | None: ...
    def catalog(self) -> WorldCatalog: ...

    # ---- write (acquires the lock) ------------------------------------
    def update_robot(self, **fields) -> None: ...        # location, holding, battery
    def record_perception(self, event: PerceptionEvent) -> None: ...
    def begin_task(self, target: dict, budget: int = 30) -> TaskContext: ...
    def end_task(self, status: Literal["done", "failed"]) -> None: ...
    def tick_tool_call(self) -> tuple[bool, str]: ...    # increments + returns budget check

    # ---- subscriptions (event bus, opt-in) ----------------------------
    def subscribe(self, callback: Callable[[StoreEvent], None]) -> Subscription: ...
```

Singleton accessor in `app/environment/__init__.py`:

```python
def get_environment() -> EnvironmentStore: ...
```

## Component Changes (when wired — *not part of this spec*)

This section names the integration points so the implementing PR has a checklist. The current spec ships only `app/environment/` in isolation; nothing else changes.

### `app/safety/guard.py`

`RobotGuard` becomes a thin policy-only object. State that lives there today (`location`, `holding`, `calls_made`, `budget`) moves to `EnvironmentStore.robot` + `EnvironmentStore.task`. The contextvar pattern stays — guards still scope precondition policy per task — but the guard reads/writes through `get_environment()` instead of holding fields itself.

`may_pick_up()`, `may_hand_over()`, `tick()` become free functions or static methods that take an `EnvironmentSnapshot` and return `(allowed, reason)`.

### `app/tools/world_model.py`

Module deletes. `KNOWN_LOCATIONS` and `KNOWN_GRASPABLE_OBJECTS` re-derive from `get_environment().catalog()`. The `what_can_i_see()` tool reads perception + catalog from the store.

### `app/tools/cure_tools.py`

Each tool's success path calls `EnvironmentStore.update_robot(...)` (e.g. `navigate_to` writes the new location) and `tick_tool_call()` for budget. The DRY_RUN path does the same — the store doesn't know whether the underlying skill ran or was stubbed.

### New tool: `app/tools/perception_tools.py`

Wraps perception reads in agent-callable tools:

```python
@tool
def who_is_in_front_of_me() -> str:
    """Return the most recent face detection, or 'no detection' if older than TTL."""

@tool
def what_objects_do_i_see() -> str: ...
```

These read the perception cache; they do not invoke the camera (perception sources push events asynchronously).

### `app/agents/delivery_agent.py`

`DeliveryAgent.execute()` calls `store.begin_task(target=...)` at start and `store.end_task(...)` on completion. The system prompt grows two sentences explaining `who_is_in_front_of_me`.

A `Checkpointer` (Layer 3) is wired into `create_react_agent(..., checkpointer=...)` so runs become resumable. Default `MemorySaver`; configurable via `CHECKPOINTER=sqlite` env.

### `app/workflows/medication_delivery.py`

Each node calls `store.update_robot(location=...)` after navigation and `store.update_robot(holding=...)` after grasp/handover. The TypedDict still carries the same fields for now; the store is the source of truth, the TypedDict is a working copy that gets reconciled on each node boundary. Removable in a follow-up once all readers consume the store directly.

### `mock_data.py` (deletion)

`MockDatabase`, `MockRobotActions` deleted. `MockNLU` keeps existing in `app/mock_data.py` (or moves to `app/llm/nlu.py`) but its allowed-list constructor argument now points at `get_environment().catalog().people` if non-empty, else allows any string. Default behavior with empty `people` catalog: any patient name is accepted, matching the relaxed validation the user explicitly requested.

## Camera Identity-Match — End-to-End Flow

The motivating use case, traced through the layers:

```
1. Camera service (future, runs as a worker thread or separate process)
   detects a face, publishes:

       store.record_perception(PerceptionEvent(
           kind="face_detected",
           payload={"person_id": "face_001", "name": None, "bbox": [...]},
           confidence=0.93,
           source="camera_d435if",
           timestamp=now(),
       ))

2. Agent (in the middle of a delivery task) decides to verify identity.
   Calls the perception tool:

       result = who_is_in_front_of_me()
       # → "Detected face_001 (no name, confidence 0.93, 1.2s ago)"

3. Agent compares against task target:

       target = store.task().target  # {"patient": "張小明", ...}
       # The agent's reasoning: "I'm delivering to 張小明 but the
       # camera detected face_001 with no name. I need to ask."

4. Agent calls speak() to ask, listen() for reply, then either:
       (a) hand_over() if the verbal answer matches the target
       (b) refuses and asks staff for help

5. On success, agent writes the verified link back into perception
   metadata so the next tool call cheap-reads it:

       store.record_perception(PerceptionEvent(
           kind="face_detected",
           payload={"person_id": "face_001", "name": "張小明",
                    "verified_at": now()},
           ...
       ))
```

**No part of step 1 ships in this spec.** The store is the API the camera service will write to when it's added; until then `who_is_in_front_of_me()` returns "no detection".

## Persistence Path (Layer 3 details)

LangGraph's `Checkpointer` API takes a thread_id (we use the task_id). `BaseStore` is keyed by namespace (we use `(robot_id, "facts")`). Both have in-memory, SQLite, and Postgres implementations shipped by `langgraph.checkpoint.*` and `langgraph.store.*`.

| Need | Primitive | Implementation now → later |
|---|---|---|
| Resume a paused delivery after server restart | `Checkpointer` | `MemorySaver` → `SqliteSaver` (~10 lines) |
| Remember "patient X had a fall last week" across tasks | `BaseStore` | `InMemoryStore` → `PostgresStore` |
| Audit log: every action the agent took, queryable | event bus (`store.subscribe`) → append-only log table | Skip for now; design later |
| Semantic memory ("find patients who refused medicine") | vector store (`Chroma`, `Qdrant`) | Out of scope until there's a real query |

For demo / single-server use, `MemorySaver` + `InMemoryStore` covers everything except resumption-across-restart, which is solved by swapping one constructor.

## Concurrency

Single-process today. The store uses a single `threading.RLock` for all mutations. Reads return atomic Pydantic copies, no shared references — safe to mutate from a different thread, no copy-on-write semantics needed.

If we later split into multiple worker processes, the store fronts a Redis backend behind the same API. The lock semantics translate to `WATCH`/`MULTI`/`EXEC` or a lightweight Lua script.

## Alternatives Considered

| Alternative | Rejected because |
|---|---|
| Keep `RobotGuard` as the authority, just add perception fields to it | Guards are per-task contextvars; perception flows in continuously and needs a non-task-scoped home. Forcing perception through the guard ties two unrelated lifetimes. |
| Use LangGraph `BaseStore` for everything including kinematic state | `BaseStore` is keyed by `(namespace, key)` and intended for cross-thread durable memory. Sub-second-rate kinematic updates aren't its model; the API gets awkward. |
| Stand up Redis from day one | Adds an operational dependency for a single-process server demo. Defer until we actually need cross-process visibility. |
| Event sourcing — store events, derive state on demand | Compelling for audit; expensive for hot reads. Reconsider when the audit requirement becomes real. |
| Bolt all of this into `AgentState` (the LangGraph TypedDict) | TypedDict is per-run state; doesn't survive across runs and isn't visible to the dashboard outside an active SSE stream. |

## Out of Scope (for this spec)

- The camera service itself (perception source). This spec describes the API the camera will write to, not the camera.
- ASR-as-perception (speech becomes a `PerceptionEvent kind="speech_heard"` later — design parallel).
- World catalog hot-reload during a running task.
- Multi-robot coordination (each robot would own a separate `EnvironmentStore` instance keyed by robot_id; cross-robot is later).
- `PostgresStore` / `SqliteSaver` deployment specifics — they're one-line swaps when the time comes.
- Deletion of `mock_data.py` — separate task; this spec only describes how the env store fills the gap.
- UI surface for inspecting the store (`/api/environment` endpoint, dashboard panel) — separate spec when the store has real data.

## Success Criteria

This spec succeeds when, in the implementing PR:

1. `app/environment/{__init__.py, types.py, store.py, world.yaml}` exist and pass standalone import + a unit test that exercises `record_perception`, `update_robot`, `begin_task`, `tick_tool_call`, `snapshot`.
2. `get_environment()` returns the same singleton across calls; the singleton survives a noop `python -c "from app.environment import get_environment; get_environment()"`.
3. The camera identity-match flow above is exercisable end-to-end via a hand-fed `record_perception(...)` call (no real camera) and the proposed `who_is_in_front_of_me()` tool returns the synthesized event.
4. `RobotGuard` and the rest of the codebase are *unchanged*. The store is wired into nothing yet — it ships isolated, ready to be linked by the next PR.
5. Server boot time, `/api/workflow` introspection, and the existing scripted + agent paths are byte-identical (the store sits idle until something writes to it).
