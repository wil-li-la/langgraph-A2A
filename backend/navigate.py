from cure.skills.navigate import navigate_skill

from cure.config import update_config, Config

update_config(Config.from_yaml("./cure/config.yaml"))

navigate_skill("test")
