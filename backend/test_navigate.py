import sys
sys.path.insert(0, "./cure/src")

from cure.config import Config, update_config
from cure.skills.navigate import navigate_avoidance, navigate_skill

update_config(Config.from_yaml("./cure/test_config.yaml"))
print()
print("=== navigate_avoidance (Nav2 SLAM, requires goto service on robot) ===")
print("Navigating to point_a...")
navigate_avoidance("point_a")
print("Arrived at point_a.")

print("Navigating to point_b...")
navigate_avoidance("point_b")
print("Arrived at point_b.")

print("Navigating back to origin...")
navigate_avoidance("origin")
print("Arrived at origin.")

print("Done.")
