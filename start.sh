#!/usr/bin/env bash
# Lab daily run — backend + nav stack + frontend in a single tmux session.
#
# The Stretch3 driver runs on the robot itself and is NOT covered by this
# script; SSH and start it before running this. See CLAUDE.md for the
# canonical three-terminal flow this script automates.
#
# Usage:
#   ./start.sh            spawn session if missing, then attach
#   ./start.sh --status   list session windows, do not attach
#   ./start.sh --kill     tear down the session
#
# Attach later:    tmux attach -t stretch-lab
# Switch windows:  Ctrl-B then 0 / 1 / 2  (or n / p)
# Detach:          Ctrl-B then d
#
# A window that exits stays open with "[exited]" — so a crashed
# nav_service or backend is visible instead of silently vanishing.
# Re-launch with Ctrl-B then : respawn-window -k, or kill-window
# (Ctrl-B then &) and re-run this script.

set -euo pipefail

SESSION="stretch-lab"
CONTAINER="isaac_ros_dev"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

command -v tmux >/dev/null 2>&1 || {
  echo "error: tmux not installed. install with: sudo apt install tmux" >&2
  exit 1
}

case "${1:-}" in
  --kill)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      tmux kill-session -t "$SESSION"
      echo "killed session: $SESSION"
    else
      echo "no session named $SESSION"
    fi
    exit 0
    ;;
  --status)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      tmux list-windows -t "$SESSION" \
        -F '#{window_index}: #{window_name} (#{?window_active,active,}) #{pane_dead_status}'
    else
      echo "no session named $SESSION"
    fi
    exit 0
    ;;
  "")
    ;;
  *)
    echo "unknown flag: $1" >&2
    echo "usage: $0 [--status|--kill]" >&2
    exit 2
    ;;
esac

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "session $SESSION already running — attaching"
  exec tmux attach -t "$SESSION"
fi

# T2 needs the isaac_ros_dev container already up (nvblox needs CUDA libs
# from inside it). Don't auto-start it — that container has setup the user
# manages separately.
if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "error: docker container '$CONTAINER' is not running." >&2
  echo "       start it before running this script (it owns the nav stack)." >&2
  exit 1
fi

cat <<'EOF'
Reminder: this script starts lab-box services only. The Stretch3 driver
runs on the robot — SSH and start it first:

  ssh stretch-se3-3099.local -l hello-robot
  cd Desktop/stretch3-zmq/
  uv run python -m stretch3_zmq.driver --config config.yaml

For AMCL auto-seed to fire, the driver must be FRESHLY booted (wheel
odom ≈ origin). If it's been running and the robot has moved, restart it.

EOF

# T1 — backend A2A + dashboard REST/SSE
tmux new-session -d -s "$SESSION" -n backend -c "$REPO_ROOT/backend" \
  'source .venv/bin/activate && exec python -m app'

# Keep dead panes visible so crashes don't vanish silently. -g sets it for
# the whole session including windows we create below.
tmux set-option -t "$SESSION" -g remain-on-exit on

# T2 — full nav stack (AMCL + nav_service + bridges + nvblox) inside the
# isaac_ros_dev container. run_nav.sh cleans up orphans, sources ROS, and
# execs ros2 launch nav.launch.py.
tmux new-window -t "$SESSION" -n nav -c "$REPO_ROOT" \
  "exec docker exec -it $CONTAINER /workspaces/langgraph-A2A/backend/nav_bridge/run_nav.sh"

# T3 — MediaMTX (RTSP→WebRTC/HLS fan-out for room cams, current arch).
tmux new-window -t "$SESSION" -n mediamtx -c "$REPO_ROOT/backend/mediamtx" \
  'exec ./run.sh'

# T4 — cam_bridge (legacy Python MJPEG bridge; kept for fallback).
tmux new-window -t "$SESSION" -n cam_bridge -c "$REPO_ROOT/backend/cam_bridge" \
  'exec ./run.sh'

# T5 — room_cameras ROS2→MJPEG bridge (ED305 overhead cams, port 9997).
# Runs under system Python 3.10 because rclpy ships with ROS2 Humble.
tmux new-window -t "$SESSION" -n room_cams -c "$REPO_ROOT/backend/room_cameras" \
  'exec ./run_bridge.sh'

# T6 — frontend dev server (Turbo, http://localhost:3000)
tmux new-window -t "$SESSION" -n frontend -c "$REPO_ROOT/frontend" \
  'exec pnpm dev'

tmux select-window -t "$SESSION:backend"

echo "session $SESSION started (windows: backend, nav, mediamtx, cam_bridge, room_cams, frontend)"
echo "attach with:  tmux attach -t $SESSION"

exec tmux attach -t "$SESSION"
