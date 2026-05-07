#!/usr/bin/env bash
# Kill orphan processes left behind by prior crashed/Ctrl-C'd launches of
# backend/nav_bridge/launch/nav.launch.py. Idempotent — safe to source and
# call multiple times.
#
# An "orphan" here means: parented to PID 1 (init / `sleep infinity` inside
# the container), with a cmdline that came from THIS workspace OR is a
# known-named ROS process from a stack that no longer has a live launch.
#
# Targets (precise — won't touch unrelated processes):
#   - python3 .../nav_bridge/{sensors,cmdvel}_bridge.py
#   - python3 .../nav_bridge/nav_service.py
#   - rosbridge_server/rosbridge_websocket
#   - nvblox_ros/lib/nvblox_ros/nvblox_node
#   - tf2_ros/static_transform_publisher with the frames our launch uses
#   - nav2_lifecycle_manager (lifecycle_manager_{map,navigation})
#   - nav2_map_server/map_server
#
# Then waits up to 5s for ports 9090, 5560, 5561 to free.
#
# Usage (source, don't exec):
#   source backend/nav_bridge/lib/cleanup_orphans.sh
#   nav_cleanup_orphans          # kill orphans + wait for ports
#   nav_cleanup_orphans --status # report only, do not kill

# Patterns that uniquely identify our processes. Bash regex (used with =~).
NAV_ORPHAN_PATTERNS=(
  "nav_bridge/sensors_bridge\.py"
  "nav_bridge/cmdvel_bridge\.py"
  "nav_bridge/nav_service\.py"
  "rosbridge_server/rosbridge_websocket"
  "nvblox_ros/lib/nvblox_ros/nvblox_node"
  "tf2_ros/static_transform_publisher .* base_link camera_depth_optical_frame"
  "tf2_ros/static_transform_publisher .* camera_depth_optical_frame camera_color_optical_frame"
  "nav2_lifecycle_manager/lifecycle_manager"
  "nav2_map_server/map_server"
)

NAV_PORTS=(9090 5560 5561)

# Internal: list pids whose cmdline matches a pattern AND whose ppid is 1
# (i.e. orphan reparented to init). Active children of a live `ros2 launch`
# have the launch process as ppid, so they are NOT matched here.
_nav_list_orphans() {
  local pattern pid ppid cmd
  while read -r pid ppid; do
    [[ "$ppid" == "1" ]] || continue
    cmd="$(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null || true)"
    [[ -z "$cmd" ]] && continue
    for pattern in "${NAV_ORPHAN_PATTERNS[@]}"; do
      if [[ "$cmd" =~ $pattern ]]; then
        echo "$pid|$cmd"
        break
      fi
    done
  done < <(ps -eo pid,ppid --no-headers 2>/dev/null)
}

_nav_port_holders() {
  local port out
  for port in "${NAV_PORTS[@]}"; do
    out="$(ss -tlnp 2>/dev/null | grep ":$port " || true)"
    if [[ -n "$out" ]]; then
      echo "$port: $out"
    fi
  done
}

nav_cleanup_orphans() {
  local mode="${1:-kill}"
  local found pid

  found="$(_nav_list_orphans)"

  if [[ -z "$found" ]]; then
    echo "[nav-cleanup] no orphans"
  else
    echo "[nav-cleanup] orphans found:"
    echo "$found" | sed 's/^/  /'
    if [[ "$mode" == "--status" ]]; then
      return 0
    fi
    while IFS='|' read -r pid _; do
      [[ -n "$pid" ]] && kill -TERM "$pid" 2>/dev/null || true
    done <<< "$found"
    sleep 1
    while IFS='|' read -r pid _; do
      [[ -n "$pid" ]] && kill -KILL "$pid" 2>/dev/null || true
    done <<< "$found"
  fi

  if [[ "$mode" == "--status" ]]; then
    echo "[nav-cleanup] port holders:"
    _nav_port_holders | sed 's/^/  /' || true
    return 0
  fi

  # Wait up to 5s for ports to free.
  local port deadline=$((SECONDS + 5))
  for port in "${NAV_PORTS[@]}"; do
    while ss -tlnp 2>/dev/null | grep -q ":$port "; do
      if (( SECONDS >= deadline )); then
        echo "[nav-cleanup] ERROR: port $port still bound after 5s" >&2
        _nav_port_holders >&2
        return 1
      fi
      sleep 0.2
    done
  done
  echo "[nav-cleanup] ports clear: ${NAV_PORTS[*]}"
}
