#!/usr/bin/env bash
# Launch the three lab-side bridges in one shell. Mirrors the
# room_cameras/run_bridge.sh pattern — sources ROS2 itself, no fresh
# login needed. Phase 1a: just bridges + nav_service (no Nav2/nvblox).
#
# Usage:
#   ./run_bridges.sh                          # robot @ 192.168.1.38
#   ./run_bridges.sh --robot 10.0.0.5         # custom robot host
#   ./run_bridges.sh --only sensors           # just sensors_bridge
#
# Stop with Ctrl-C; SIGINT propagates to all children.

set -euo pipefail

ROBOT_HOST="192.168.1.38"
ONLY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --robot) ROBOT_HOST="$2"; shift 2;;
    --only)  ONLY="$2"; shift 2;;
    -h|--help)
      sed -n '2,12p' "$0"; exit 0;;
    *)
      echo "unknown arg: $1" >&2; exit 1;;
  esac
done

# Source ROS2 humble. Override ROS_SETUP=/path/to/setup.bash if needed.
ROS_SETUP="${ROS_SETUP:-/opt/ros/humble/setup.bash}"
if [[ ! -f "$ROS_SETUP" ]]; then
  echo "ROS2 setup not found at $ROS_SETUP" >&2
  echo "Install ros-humble-desktop or set ROS_SETUP=/path/to/setup.bash" >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$ROS_SETUP"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Set ROS_DOMAIN_ID to isolate from the room_cameras bridge (which uses 42).
# nvblox / Nav2 nodes started later must use the same domain.
: "${ROS_DOMAIN_ID:=37}"
export ROS_DOMAIN_ID

trap 'kill 0' SIGINT SIGTERM

run() {
  local label="$1" cmd="$2"
  echo "[run_bridges] starting $label"
  # shellcheck disable=SC2086
  $cmd 2>&1 | sed -u "s/^/[$label] /" &
}

if [[ -z "$ONLY" || "$ONLY" == "sensors" ]]; then
  run sensors "python3 $SCRIPT_DIR/sensors_bridge.py --robot $ROBOT_HOST"
fi
if [[ -z "$ONLY" || "$ONLY" == "cmdvel" ]]; then
  run cmdvel  "python3 $SCRIPT_DIR/cmdvel_bridge.py --robot $ROBOT_HOST"
fi
if [[ -z "$ONLY" || "$ONLY" == "nav" ]]; then
  run nav     "python3 $SCRIPT_DIR/nav_service.py"
fi

echo "[run_bridges] all started; ROS_DOMAIN_ID=$ROS_DOMAIN_ID; Ctrl-C to stop"
wait
