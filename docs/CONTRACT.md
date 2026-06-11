# ZMQ wire contract

The robot ZMQ wire surface (ports, message shapes, `goto`, `set_initial_pose`/AMCL seed,
`command`, `servo`, `scan`, camera/TTS/ASR frames) is owned by the robot monorepo. This
backend is a **consumer** — it does NOT keep a copy (copies drifted; see below).

**Canonical, single source of truth:**

    https://github.com/wil-li-la/stretch-monorepo/blob/3dd29defa093fc500f337621423f1dfdc200a55b/contracts/protocols.md

Bump `3dd29defa093fc500f337621423f1dfdc200a55b` when the wire surface changes. That bump is the explicit,
reviewable step that keeps this backend aligned with the robot — replacing the old habit
of hand-copying `protocols.md` into both repos.

## Why the old docs are gone

Deleted from this repo (they were stale copies or described retired architecture):

- `steretch3_protocol/protocols.md` — stale subset of the canonical (missing
  `set_initial_pose`, wrong `scan` wording).
- `steretch3_protocol/spec-amcl-seed-port-5564.md` — superseded; `set_initial_pose:5564`
  shipped and is now a section of the canonical `protocols.md`.
- `lab-client-guide.md`, `nvblox-integration-guide.md` — the retired nvblox / lab-GPU-box
  nav stack (dead since 2026-06-01; FUNMAP replaced it). History lives in the archived
  `lnfu/*` origins.
- `stretch_server_goto_refactor.md` — robot-side design history; lives in the monorepo at
  `stretch3-zmq/docs/nav_skill refactor/`.

## Client install (after the monorepo is published)

`stretch3-zmq-core` (the client wire types this backend imports) moves from the old
`lnfu/stretch3-zmq` clone to the monorepo subdir. Update `backend/INSTALL.md`:

    pip install "git+https://github.com/wil-li-la/stretch-monorepo.git@3dd29defa093fc500f337621423f1dfdc200a55b#subdirectory=stretch3-zmq/packages/core"
