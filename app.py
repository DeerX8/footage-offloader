#!/usr/bin/env python3
"""Footage Offloader - Raspberry Pi SSD to NAS copy utility."""

import os
import sys
import json
import logging
from pathlib import Path

from flask import Flask
from offloader.routes import bp as main_bp
from offloader.copy_manager import CopyManager

CONFIG_PATH = os.environ.get("OFFLOADER_CONFIG", "/etc/footage-offloader/config.json")
DEFAULT_CONFIG = {
    "smb_host": "",
    "smb_share": "",
    "smb_username": "",
    "smb_password": "",
    "smb_domain": "WORKGROUP",
    "discord_webhook_url": "",
    "discord_enabled": False,
    "ssd_mount_point": "/mnt/ssd",
    "smb_mount_point": "/mnt/nas",
    "host": "0.0.0.0",
    "port": 5000,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/var/log/footage-offloader.log", mode="a"),
    ],
)
logger = logging.getLogger("offloader")


def load_config() -> dict:
    """Load configuration from JSON file, creating defaults if missing."""
    config_path = Path(CONFIG_PATH)
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                saved = json.load(f)
            merged = {**DEFAULT_CONFIG, **saved}
            return merged
        except Exception as e:
            logger.warning(f"Failed to load config: {e}, using defaults")
    return dict(DEFAULT_CONFIG)


def save_config(config: dict):
    """Persist configuration to JSON file."""
    config_path = Path(CONFIG_PATH)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    logger.info("Configuration saved")


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.urandom(24)

    config = load_config()
    app.config["OFFLOADER"] = config
    app.config["OFFLOADER_SAVE"] = save_config
    app.config["COPY_MANAGER"] = CopyManager(config)

    # Ensure mount points exist
    for mp in [config["ssd_mount_point"], config["smb_mount_point"]]:
        Path(mp).mkdir(parents=True, exist_ok=True)

    app.register_blueprint(main_bp)
    return app


if __name__ == "__main__":
    app = create_app()
    config = app.config["OFFLOADER"]
    logger.info(f"Starting Footage Offloader on {config['host']}:{config['port']}")
    app.run(host=config["host"], port=int(config["port"]), debug=False, threaded=True)
