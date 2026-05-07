#!/usr/bin/env bash
# Diagnose the nvblox → rosbridge → /recon pipeline from the lab box.
#
# Walks each component boundary and prints OK / WARN / FAIL so you can
# tell *which* layer broke "no mesh on /recon":
#
#   [B] sensors_bridge ROS topics    — depth/color/info/odom publishing?
#   [C] nvblox_node existence        — node running + subscribed?
#   [D] depth integration            — depth at the rate nvblox expects?
#   [E] /nvblox_node/mesh            — published? at what rate?
#   [F] rosbridge introspection      — does rosbridge resolve the mesh
#                                      message type? (browser uses this
#                                      same call before subscribing)
#
# Usage:
#   ./check_mesh_pipeline.sh
#   ./check_mesh_pipeline.sh --ws-url ws://localhost:9090   # custom rosbridge
#   ./check_mesh_pipeline.sh --hz-window 5                  # longer rate sample
#
# Run on the lab box (the host running nav.launch.py), with ROS2 humble
# already sourced. The script will source /opt/ros/humble/setup.bash if
# rclpy isn't found.

set -uo pipefail

WS_URL="ws://localhost:9090"
HZ_WINDOW=3

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ws-url) WS_URL="$2"; shift 2;;
    --hz-window) HZ_WINDOW="$2"; shift 2;;
    -h|--help) sed -n '2,22p' "$0"; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 1;;
  esac
done

if ! command -v ros2 >/dev/null 2>&1; then
  ROS_SETUP="${ROS_SETUP:-/opt/ros/humble/setup.bash}"
  if [[ -f "$ROS_SETUP" ]]; then
    # shellcheck disable=SC1090
    source "$ROS_SETUP"
  else
    echo "FAIL: ros2 not in PATH and $ROS_SETUP not found" >&2
    exit 2
  fi
fi

PASS=0
FAIL=0
WARN=0

# ANSI colors (skip on dumb terms / non-tty)
if [[ -t 1 ]] && command -v tput >/dev/null 2>&1; then
  BOLD="$(tput bold)"; DIM="$(tput dim)"; RST="$(tput sgr0)"
  GREEN="$(tput setaf 2)"; YELLOW="$(tput setaf 3)"; RED="$(tput setaf 1)"; BLUE="$(tput setaf 4)"
else
  BOLD=""; DIM=""; RST=""; GREEN=""; YELLOW=""; RED=""; BLUE=""
fi

ok()   { echo "  ${GREEN}OK${RST}   $*"; PASS=$((PASS+1)); }
warn() { echo "  ${YELLOW}WARN${RST} $*"; WARN=$((WARN+1)); }
fail() { echo "  ${RED}FAIL${RST} $*"; FAIL=$((FAIL+1)); }
section() { echo; echo "${BOLD}${BLUE}── $* ──${RST}"; }

# ---- helpers ------------------------------------------------------------

# topic_exists <topic>  →  exit 0 if `ros2 topic list` includes it
topic_exists() {
  ros2 topic list 2>/dev/null | grep -Fxq "$1"
}

# topic_publishers <topic>  →  prints publisher count
topic_publishers() {
  ros2 topic info "$1" 2>/dev/null | awk -F': ' '/Publisher count/ {print $2}'
}

# topic_subscribers <topic>  →  prints subscriber count
topic_subscribers() {
  ros2 topic info "$1" 2>/dev/null | awk -F': ' '/Subscription count/ {print $2}'
}

# topic_type <topic>  →  prints msg type (e.g. nvblox_msgs/msg/Mesh)
topic_type() {
  ros2 topic info "$1" 2>/dev/null | awk -F': ' '/Type/ {print $2}'
}

# topic_hz <topic> <window_s>  →  prints average rate or "0"
topic_hz() {
  local topic="$1" win="$2"
  # `timeout` exits 124 on success — that's fine, we just want stdout.
  local out
  out="$(timeout "$win" ros2 topic hz "$topic" 2>&1 || true)"
  echo "$out" | awk '
    /average rate/ {print $3; found=1; exit}
    END {if (!found) print "0"}
  '
}

# ---- [B] sensors_bridge outputs ----------------------------------------

section "[A] ROS2 environment preflight"
domain_id="${ROS_DOMAIN_ID:-0}"
rmw="${RMW_IMPLEMENTATION:-(default)}"
echo "  ROS_DOMAIN_ID=$domain_id  RMW=$rmw"

# `ros2 node list` blocks for ~1s when nothing's there. If we see ONLY
# the built-in nodes (or none), nav.launch.py isn't visible to this shell.
node_list="$(ros2 node list 2>/dev/null || true)"
user_nodes="$(echo "$node_list" | grep -Ev '^(/_ros2cli_|$)' || true)"
if [[ -z "$user_nodes" ]]; then
  fail "no ROS2 nodes visible in this shell"
  echo "       ${DIM}likely cause: nav.launch.py isn't running, or this shell"
  echo "       has a different ROS_DOMAIN_ID / RMW. Start it with:"
  echo "         ros2 launch backend/nav_bridge/launch/nav.launch.py only_bridges:=true"
  echo "       (drop only_bridges:=true once nvblox + Nav2 are in place)${RST}"
  echo
  echo "  ${DIM}Continuing checks anyway so the rest of the layout is recorded.${RST}"
else
  ok "$(echo "$user_nodes" | wc -l) user node(s) visible:"
  echo "$user_nodes" | sed 's/^/         /'
fi

section "[A2] duplicate-publisher / TF-tree sanity"
expected_pubs=1
for t in /camera/depth/image_rect_raw /camera/color/image_raw \
         /camera/depth/camera_info /camera/color/camera_info /tf_static; do
  pubs="$(topic_publishers "$t")"
  pubs="${pubs:-0}"
  if [[ "$pubs" -eq "$expected_pubs" ]]; then
    ok "$t pubs=$pubs"
  elif [[ "$pubs" -gt "$expected_pubs" ]]; then
    fail "$t pubs=$pubs — orphan publishers from a prior crashed launch"
    echo "       fix: backend/nav_bridge/run_nav.sh (cleans up before launching)"
  else
    fail "$t pubs=$pubs — no one is publishing"
  fi
done

# Verify the map frame exists. nvblox needs map → camera_depth_optical_frame
# at every depth callback; if the map frame is missing, depth is silently
# dropped and the mesh stays empty.
if timeout 4 ros2 run tf2_ros tf2_echo map camera_depth_optical_frame 2>&1 \
     | grep -q "Translation"; then
  ok "TF map → camera_depth_optical_frame resolves"
else
  fail "TF map → camera_depth_optical_frame missing — nav_service likely not publishing map→odom"
  echo "       fix: backend/nav_bridge/run_nav.sh (will restart nav_service)"
fi

section "[B] sensors_bridge → ROS2 topics"
for t in /camera/depth/image_rect_raw /camera/color/image_raw \
         /camera/depth/camera_info /camera/color/camera_info /odom; do
  if topic_exists "$t"; then
    pubs="$(topic_publishers "$t")"
    if [[ "${pubs:-0}" -ge 1 ]]; then
      ok "$t (publishers=$pubs)"
    else
      fail "$t exists but has no publishers"
    fi
  else
    fail "$t MISSING — is sensors_bridge running?"
  fi
done

# tf chain — odom → base_link is what sensors_bridge publishes; nvblox
# walks this when integrating each depth frame.
if ros2 topic list 2>/dev/null | grep -Fxq /tf; then
  ok "/tf topic present"
else
  warn "/tf topic missing — TF chain incomplete, nvblox can't integrate"
fi

# ---- depth/color publishing rate ---------------------------------------

section "[B2] depth/color publish rate (sampling ${HZ_WINDOW}s)"
depth_hz="$(topic_hz /camera/depth/image_rect_raw "$HZ_WINDOW")"
color_hz="$(topic_hz /camera/color/image_raw "$HZ_WINDOW")"

if awk "BEGIN{exit !($depth_hz >= 5)}"; then
  ok "depth ${depth_hz} Hz"
elif awk "BEGIN{exit !($depth_hz > 0)}"; then
  warn "depth only ${depth_hz} Hz (<5 Hz; nvblox integrate_depth_rate is 10 Hz)"
else
  fail "depth 0 Hz — sensors_bridge isn't publishing"
fi
if awk "BEGIN{exit !($color_hz >= 2)}"; then
  ok "color ${color_hz} Hz"
elif awk "BEGIN{exit !($color_hz > 0)}"; then
  warn "color only ${color_hz} Hz"
else
  fail "color 0 Hz"
fi

# ---- [C] nvblox_node existence ----------------------------------------

section "[C] nvblox_node"
if ros2 node list 2>/dev/null | grep -Fxq /nvblox_node; then
  ok "/nvblox_node alive"
  # Subscriber check: does nvblox have a subscriber on the depth/info?
  d_subs="$(topic_subscribers /camera/depth/image_rect_raw)"
  i_subs="$(topic_subscribers /camera/depth/camera_info)"
  c_subs="$(topic_subscribers /camera/color/image_raw)"
  if [[ "${d_subs:-0}" -ge 1 ]]; then
    ok "depth has subscriber (count=$d_subs) — nvblox is listening"
  else
    fail "depth has 0 subscribers — nvblox isn't subscribed (check remappings)"
  fi
  if [[ "${i_subs:-0}" -ge 1 ]]; then
    ok "depth/camera_info has subscriber (count=$i_subs)"
  else
    fail "depth/camera_info has 0 subscribers — sync filter won't fire"
  fi
  if [[ "${c_subs:-0}" -ge 1 ]]; then
    ok "color has subscriber (count=$c_subs)"
  else
    warn "color has 0 subscribers — TSDF will integrate but mesh won't have RGB"
  fi
else
  fail "/nvblox_node not running — Nav2/nvblox stack not launched, or it crashed"
fi

# ---- [D] nvblox patch (issue #141) -------------------------------------

section "[D] nvblox depth-format patch"
# This is heuristic: there's no runtime way to ask nvblox what NITROS
# format it negotiated. Best we can do is check if the depth topic has
# a subscriber AND messages flow at a reasonable rate; if depth publishes
# at 30 Hz but mesh publishes nothing, the patch is the prime suspect.
mesh_hz_quick="$(topic_hz /nvblox_node/mesh "$HZ_WINDOW")"
if awk "BEGIN{exit !($depth_hz >= 5 && $mesh_hz_quick == 0)}"; then
  fail "depth=${depth_hz} Hz but mesh=0 Hz — likely the nitros_image_mono16 patch is NOT applied"
  echo "       see backend/nav_bridge/patches/nvblox-issue-141.patch"
elif awk "BEGIN{exit !($depth_hz == 0 && $mesh_hz_quick == 0)}"; then
  warn "both depth and mesh are 0 Hz — can't evaluate the patch yet (upstream not running)"
elif awk "BEGIN{exit !($mesh_hz_quick > 0)}"; then
  ok "depth/mesh ratio looks plausible (depth=${depth_hz} Hz, mesh=${mesh_hz_quick} Hz)"
else
  warn "depth=${depth_hz} Hz, mesh=${mesh_hz_quick} Hz — inconclusive"
fi

# ---- [E] /nvblox_node/mesh ---------------------------------------------

section "[E] /nvblox_node/mesh"
if topic_exists /nvblox_node/mesh; then
  ok "topic exists"
  mtype="$(topic_type /nvblox_node/mesh)"
  if [[ "$mtype" == "nvblox_msgs/msg/Mesh" ]]; then
    ok "type = $mtype"
  else
    warn "type = '$mtype' (expected nvblox_msgs/msg/Mesh)"
  fi
  pubs="$(topic_publishers /nvblox_node/mesh)"
  if [[ "${pubs:-0}" -ge 1 ]]; then
    ok "publishers=$pubs"
  else
    fail "publishers=0 — nvblox_node may have crashed"
  fi
  if awk "BEGIN{exit !($mesh_hz_quick > 0)}"; then
    ok "publishing at ${mesh_hz_quick} Hz"
  else
    fail "0 Hz — nothing being published. depth integration likely broken upstream"
  fi
else
  fail "/nvblox_node/mesh topic missing — nvblox didn't start, or crashed"
fi

# ---- [F] rosbridge introspection (the path the browser uses) -----------

section "[F] rosbridge can resolve /nvblox_node/mesh type"
if command -v python3 >/dev/null 2>&1; then
  python3 - <<PY
import json, socket, sys, time
try:
    from websocket import create_connection
except Exception:
    sys.stderr.write("(python websocket-client not installed; skipping rosbridge check)\n")
    sys.stderr.write("install: pip install websocket-client\n")
    sys.exit(2)

url = "${WS_URL}"
try:
    ws = create_connection(url, timeout=4)
except Exception as e:
    print(f"FAIL: cannot connect to {url}: {e}")
    sys.exit(3)

# rosbridge JSON-RPC: call_service /rosapi/topic_type
req = {
    "op": "call_service",
    "service": "/rosapi/topic_type",
    "args": {"topic": "/nvblox_node/mesh"},
    "id": "diag-1",
}
ws.send(json.dumps(req))
ws.settimeout(4)
try:
    resp = ws.recv()
except Exception as e:
    print(f"FAIL: no reply from /rosapi/topic_type: {e}")
    sys.exit(4)
data = json.loads(resp)
ws.close()

t = (data.get("values") or {}).get("type", "")
if t == "nvblox_msgs/Mesh" or t == "nvblox_msgs/msg/Mesh":
    print(f"OK   rosbridge resolved type: {t}")
elif not t:
    print("FAIL rosbridge returned EMPTY type — either:")
    print("     1. nvblox_msgs IDL not sourced in the rosbridge env")
    print("        → source the workspace where nvblox_msgs is built BEFORE")
    print("          launching rosbridge_websocket")
    print("     2. /nvblox_node/mesh has no current publisher")
    sys.exit(5)
else:
    print(f"WARN rosbridge resolved unexpected type: {t}")
PY
  rc=$?
  case $rc in
    0) ok "rosbridge introspection passed";;
    2) warn "skipped (websocket-client not installed)";;
    *) fail "rosbridge introspection failed (rc=$rc)";;
  esac
else
  warn "python3 not found — skipping rosbridge introspection"
fi

# ---- summary -----------------------------------------------------------

echo
echo "${BOLD}── summary ──${RST}"
echo "  ${GREEN}pass=$PASS${RST}  ${YELLOW}warn=$WARN${RST}  ${RED}fail=$FAIL${RST}"
echo
echo "${DIM}If [F] passes but the browser still shows no mesh, open /recon"
echo "and read the on-screen debug overlay — it pinpoints whether the WS"
echo "connection, message arrivals, or rendering is the issue.${RST}"

[[ $FAIL -eq 0 ]]
