#!/usr/bin/env bash
# Launch the ROS2 → MJPEG-over-HTTP bridge for the ED305 room cameras.
#
# This is intentionally a separate process from the langgraph-A2A backend:
# ROS2 Humble ships its own Python 3.10 stack; the backend runs Python 3.12.
# Mixing them in one venv breaks rclpy. The bridge runs under the system
# Python that /opt/ros/humble provides.
#
# Prereqs (one-time, on this Ubuntu host):
#   bash /tmp/ED305_pylon_viewer/scripts/install_ros2.sh
#   # writes ROS sourcing + ROS_DOMAIN_ID=42 into ~/.bashrc
#
# Usage:
#   ./run_bridge.sh                   # defaults: 0.0.0.0:9997, both sides, 8 cams/side
#   ./run_bridge.sh --port 9997 --sides right --cams-per-side 8
set -eo pipefail   # NOTE: no -u — ROS setup.bash references unbound vars.

ROS_DISTRO="${ROS_DISTRO:-humble}"
ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-42}"
RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_fastrtps_cpp}"

if [[ ! -f "/opt/ros/${ROS_DISTRO}/setup.bash" ]]; then
    echo "[ERROR] /opt/ros/${ROS_DISTRO}/setup.bash not found." >&2
    echo "        Install ROS2 ${ROS_DISTRO} first (see install_ros2.sh in" >&2
    echo "        the ED305_pylon_viewer repo)." >&2
    exit 1
fi

# shellcheck disable=SC1090
source "/opt/ros/${ROS_DISTRO}/setup.bash"
export ROS_DOMAIN_ID
export RMW_IMPLEMENTATION

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/ros_mjpeg_bridge.py" "$@"
