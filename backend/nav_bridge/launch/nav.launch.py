"""Launch the lab nvblox/Nav2 stack: bridges + map_server + (eventually) nvblox + Nav2.

Phase 1a (bridges only — no Nav2 / nvblox yet):
    ros2 launch backend/nav_bridge/launch/nav.launch.py only_bridges:=true

Phase 1b (full stack — requires nav2-bringup, isaac_ros_nvblox, etc):
    ros2 launch backend/nav_bridge/launch/nav.launch.py
"""
from pathlib import Path

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    GroupAction,
    IncludeLaunchDescription,
    Shutdown,
)
from launch.conditions import IfCondition, UnlessCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description() -> LaunchDescription:
    pkg_root = Path(__file__).resolve().parent.parent
    bridges_dir = pkg_root
    config_dir = pkg_root / "config"
    map_yaml = pkg_root.parent / "maps" / "305" / "map.yaml"

    only_bridges = LaunchConfiguration("only_bridges")
    robot_host = LaunchConfiguration("robot_host")
    rosbridge_port = LaunchConfiguration("rosbridge_port")

    args = [
        DeclareLaunchArgument("only_bridges", default_value="false",
                              description="Skip nav2 + nvblox; just run bridges + nav_service"),
        DeclareLaunchArgument("robot_host", default_value="192.168.1.38",
                              description="Robot hostname/IP for ZMQ bridges"),
        DeclareLaunchArgument(
            "rosbridge_port", default_value="9090",
            description=(
                "WebSocket port for rosbridge_websocket. The dashboard's /nav "
                "page subscribes via roslib for live OccupancyGrid + Path "
                "overlays, and Lichtblick on /viz uses the same WS for 3D "
                "mesh. Cloudflare-tunneled in production to "
                "wss://stretch-fg.<domain>. Default 9090 (rosbridge canonical)."
            ),
        ),
    ]

    # Use ExecuteProcess (not launch_ros.Node) — Node expects an installed
    # ROS package executable; our bridges are raw scripts under nav_bridge/.
    #
    # Critical bridges get on_exit=Shutdown() so that if either dies, the
    # whole launch tears down — instead of staying half-up like it used to,
    # which is what allowed orphan publishers to accumulate across sessions.
    # cmdvel_bridge is intentionally non-critical (its absence only disables
    # motion commands; mesh integration still works without it).
    bridges = [
        ExecuteProcess(
            cmd=["python3", str(bridges_dir / "sensors_bridge.py"),
                 "--robot", robot_host],
            name="sensors_bridge", output="screen",
            on_exit=Shutdown(reason="sensors_bridge exited; tearing down stack"),
        ),
        ExecuteProcess(
            cmd=["python3", str(bridges_dir / "cmdvel_bridge.py"),
                 "--robot", robot_host],
            name="cmdvel_bridge", output="screen",
        ),
        ExecuteProcess(
            cmd=["python3", str(bridges_dir / "nav_service.py"),
                 "--robot", robot_host],
            name="nav_service", output="screen",
            on_exit=Shutdown(reason="nav_service exited; tearing down stack"),
        ),
    ]

    # D435if depth → /scan (sensor_msgs/LaserScan). AMCL needs a 2D scan;
    # the Stretch3 has no separate LiDAR, so we flatten a slice of the
    # head camera's depth image at robot-height.
    depth_to_scan = Node(
        package="depthimage_to_laserscan",
        executable="depthimage_to_laserscan_node",
        name="depthimage_to_laserscan",
        output="screen",
        parameters=[str(config_dir / "nav2_params.yaml")],
        remappings=[
            ("depth", "/camera/depth/image_rect"),
            ("depth_camera_info", "/camera/depth/camera_info"),
            ("scan", "/scan"),
        ],
        condition=UnlessCondition(only_bridges),
    )

    # Map server — load the static room-305 map for Nav2's global costmap.
    map_server = Node(
        package="nav2_map_server",
        executable="map_server",
        name="map_server",
        output="screen",
        parameters=[{"yaml_filename": str(map_yaml)}],
        condition=UnlessCondition(only_bridges),
    )
    map_server_lifecycle = Node(
        package="nav2_lifecycle_manager",
        executable="lifecycle_manager",
        name="lifecycle_manager_map",
        output="screen",
        parameters=[{
            "use_sim_time": False,
            "autostart": True,
            "node_names": ["map_server"],
        }],
        condition=UnlessCondition(only_bridges),
    )

    # AMCL — particle-filter localization against map.pgm, fed /scan from
    # depth_to_scan. Publishes map → odom. Owns that transform exclusively;
    # nav_service no longer publishes a placeholder.
    amcl = Node(
        package="nav2_amcl",
        executable="amcl",
        name="amcl",
        output="screen",
        parameters=[str(config_dir / "nav2_params.yaml")],
        condition=UnlessCondition(only_bridges),
    )
    amcl_lifecycle = Node(
        package="nav2_lifecycle_manager",
        executable="lifecycle_manager",
        name="lifecycle_manager_amcl",
        output="screen",
        parameters=[{
            "use_sim_time": False,
            "autostart": True,
            "node_names": ["amcl"],
        }],
        condition=UnlessCondition(only_bridges),
    )

    # Nav2 bringup — included only when only_bridges:=false. Requires
    # ros-humble-nav2-bringup. Tuned via config/nav2_params.yaml.
    nav2_bringup = GroupAction(
        actions=[
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource([
                    PathJoinSubstitution([
                        FindPackageShare("nav2_bringup"), "launch", "navigation_launch.py",
                    ]),
                ]),
                launch_arguments={
                    "use_sim_time": "false",
                    "params_file": str(config_dir / "nav2_params.yaml"),
                    "autostart": "true",
                }.items(),
            ),
        ],
        condition=UnlessCondition(only_bridges),
    )

    # AMCL (above) owns map → odom — published continuously based on
    # /scan + /map + odom. nav_service no longer broadcasts a placeholder
    # identity transform; set_initial_pose forwards to AMCL's /initialpose.

    # Static TF: base_link → camera_depth_optical_frame
    # Approximate D435i mount; refine after a one-time calibration.
    #
    # The rotation maps ROS body convention (REP-103: x-fwd, y-left, z-up)
    # to the optical-frame convention (REP-103: x-right, y-down, z-fwd).
    # Without it, nvblox projects depth rays along base_link +z (straight
    # up) instead of forward, the TSDF gets voxels allocated in a vertical
    # pillar above the camera, marching-cubes finds no surfaces, and
    # /nvblox_node/mesh publishes block_indices with empty vertices.
    # yaw=-π/2, pitch=0, roll=-π/2 is the canonical body→optical rotation.
    static_tf_camera = Node(
        package="tf2_ros", executable="static_transform_publisher",
        name="base_to_camera_depth",
        # x y z yaw pitch roll parent child  — head cam, ~1.4 m up, looking forward
        arguments=["0.05", "0.0", "1.40", "-1.5707963", "0", "-1.5707963",
                   "base_link", "camera_depth_optical_frame"],
    )
    static_tf_color = Node(
        package="tf2_ros", executable="static_transform_publisher",
        name="depth_to_color",
        arguments=["0.0", "0.0", "0.0", "0", "0", "0",
                   "camera_depth_optical_frame", "camera_color_optical_frame"],
    )

    # rosbridge_websocket — exposes all ROS2 topics over WebSocket as
    # JSON. Consumed by:
    #   - Dashboard /nav page (via frontend/lib/ros-client.ts → roslib)
    #     for live OccupancyGrid + Path overlays
    #   - Lichtblick on /viz, configured with the "Rosbridge (ROS 1 & 2)"
    #     data source pointed at this WS, for 3D mesh inspection
    # Replaces the old foxglove_bridge. Reason: ros-humble-foxglove-bridge
    # 3.x switched to Foxglove's commercial SDK protocol
    # (subprotocol "foxglove.sdk.v1") which the open-source
    # @foxglove/ws-protocol JS client cannot speak. rosbridge has been
    # the boring-default ROS↔WS bridge for ~10 years.
    rosbridge = Node(
        package="rosbridge_server", executable="rosbridge_websocket",
        name="rosbridge_websocket",
        parameters=[{
            "port": rosbridge_port,
            "address": "0.0.0.0",
            "send_action_goals_in_new_thread": True,
            # rosbridge defaults to max_message_size=1_000_000 (1 MB) and
            # silently drops larger messages. nvblox publishes "entire map"
            # dumps on every new subscriber — a 1088-block mesh is several
            # MB of JSON, well past 1 MB. Bumping to 100 MB.
            # NOTE: do NOT set this to 0 ("unlimited"). rosbridge uses the
            # value as a divisor in its fragmentation code for service-call
            # responses; 0 triggers `ZeroDivisionError` on every rosapi call
            # (e.g. /rosapi/topic_type, which the dashboard uses for
            # auto-typed subscribe).
            "max_message_size": 100_000_000,
        }],
        output="screen",
    )

    # rosapi_node — exposes /rosapi/* introspection services
    # (topic_type, get_topic_names, etc.). Required by roslib's
    # auto-typed subscribe API, which the dashboard /nav page uses to
    # discover OccupancyGrid / Path types at runtime instead of
    # hardcoding them. rosbridge_websocket on its own does NOT
    # provide these services.
    rosapi = Node(
        package="rosapi", executable="rosapi_node",
        name="rosapi",
        output="screen",
    )

    # nvblox_node — GPU 3D reconstruction. Consumes the depth + color
    # streams republished by sensors_bridge, builds a TSDF/ESDF, and
    # publishes the 2D slice that nvblox_nav2's costmap layer reads.
    # Requires running inside the isaac_ros_dev container (the nvblox
    # binary depends on container-built CUDA libs); the nav.launch.py
    # entry-point itself doesn't care, but on the host this Node will
    # fail to start with "executable not found".
    nvblox_node = Node(
        package="nvblox_ros", executable="nvblox_node",
        name="nvblox_node",
        output="screen",
        parameters=[
            str(config_dir / "nvblox.yaml"),
            {
                # Topic remaps to match sensors_bridge's outputs
                "depth_qos": "SENSOR_DATA",
                "color_qos": "SENSOR_DATA",
            },
        ],
        # nvblox 0.3+ uses multi-camera input names (`/camera_0/...`,
        # `/camera_1/...`); the older single-camera symbols (`depth/image`)
        # are NOT what it actually subscribes to. Verified empirically:
        # `ros2 node info /nvblox_node` lists `/camera_0/depth/image`,
        # `/camera_0/depth/camera_info`, etc. as its subscriptions.
        # We remap those onto sensors_bridge's `/camera/...` outputs.
        remappings=[
            ("/camera_0/depth/image", "/camera/depth/image_rect_raw"),
            ("/camera_0/depth/camera_info", "/camera/depth/camera_info"),
            ("/camera_0/color/image", "/camera/color/image_raw"),
            ("/camera_0/color/camera_info", "/camera/color/camera_info"),
        ],
        condition=UnlessCondition(only_bridges),
    )

    return LaunchDescription(args + bridges + [
        static_tf_camera, static_tf_color, rosbridge, rosapi, nvblox_node,
        depth_to_scan,
        map_server, map_server_lifecycle,
        amcl, amcl_lifecycle,
        nav2_bringup,
    ])
