#!/usr/bin/env bash
# One-command, restart-safe launch of the full nav stack.
#
# Run INSIDE the isaac_ros_dev container (it needs the workspace install
# tree at /workspaces/isaac_ros-dev/install). From the host:
#
#   docker exec -it isaac_ros_dev /workspaces/langgraph-A2A/backend/nav_bridge/run_nav.sh
#
# Or for bridges-only:
#
#   docker exec -it isaac_ros_dev /workspaces/langgraph-A2A/backend/nav_bridge/run_nav.sh --only-bridges
#
# Or to inspect (no side effects):
#
#   docker exec -it isaac_ros_dev /workspaces/langgraph-A2A/backend/nav_bridge/run_nav.sh --status
#
# Steps:
#   1. Kill any orphan processes from prior crashed/Ctrl-C'd launches
#   2. Wait for ports 9090, 5560, 5561 to free
#   3. Source ROS humble + the workspace install + ROS_DOMAIN_ID
#   4. Verify the running nvblox binary contains the issue-#141 patch
#   5. exec ros2 launch (replaces this shell so SIGINT propagates cleanly)

set -uo pipefail

ONLY_BRIDGES="false"
STATUS_ONLY="false"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --only-bridges) ONLY_BRIDGES="true"; shift;;
    --status)       STATUS_ONLY="true"; shift;;
    -h|--help)      sed -n '2,21p' "$0"; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 1;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1+2: cleanup orphans, wait for ports
# shellcheck source=lib/cleanup_orphans.sh
source "$SCRIPT_DIR/lib/cleanup_orphans.sh"

if [[ "$STATUS_ONLY" == "true" ]]; then
  nav_cleanup_orphans --status
  exit 0
fi

nav_cleanup_orphans || {
  echo "[run_nav] cleanup failed; refusing to launch into a dirty state" >&2
  exit 1
}

# 3: source env
ROS_SETUP="${ROS_SETUP:-/opt/ros/humble/setup.bash}"
WS_SETUP="${WS_SETUP:-/workspaces/isaac_ros-dev/install/setup.bash}"
[[ -f "$ROS_SETUP" ]] || { echo "[run_nav] missing $ROS_SETUP" >&2; exit 2; }
[[ -f "$WS_SETUP" ]]  || { echo "[run_nav] missing $WS_SETUP — nvblox install not built?" >&2; exit 2; }
# shellcheck disable=SC1090
source "$ROS_SETUP"
# shellcheck disable=SC1090
source "$WS_SETUP"
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-37}"
echo "[run_nav] env: ROS_DOMAIN_ID=$ROS_DOMAIN_ID  RMW=${RMW_IMPLEMENTATION:-default}"

# 4: verify nvblox patch is in the binary (skipped for bridges-only path)
if [[ "$ONLY_BRIDGES" == "false" ]]; then
  NVBLOX_BIN="$(realpath /workspaces/isaac_ros-dev/install/nvblox_ros/lib/nvblox_ros/nvblox_node 2>/dev/null || true)"
  if [[ -z "$NVBLOX_BIN" || ! -x "$NVBLOX_BIN" ]]; then
    echo "[run_nav] ERROR: nvblox_node binary not found — workspace not built" >&2
    exit 3
  fi
  if ! strings "$NVBLOX_BIN" | grep -q nitros_image_mono16; then
    echo "[run_nav] ERROR: nvblox_node binary lacks the issue-#141 patch (nitros_image_mono16 not found)" >&2
    echo "          Apply backend/nav_bridge/patches/nvblox-issue-141.patch and rebuild." >&2
    exit 4
  fi
  echo "[run_nav] nvblox patch verified in binary"
fi

# 5: exec the launch (replaces this shell so SIGINT goes straight to ros2 launch)
LAUNCH_FILE="$SCRIPT_DIR/launch/nav.launch.py"
LAUNCH_ARGS=()
[[ "$ONLY_BRIDGES" == "true" ]] && LAUNCH_ARGS+=("only_bridges:=true")

echo "[run_nav] exec ros2 launch $LAUNCH_FILE ${LAUNCH_ARGS[*]}"
exec ros2 launch "$LAUNCH_FILE" "${LAUNCH_ARGS[@]}"
