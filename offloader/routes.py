"""Flask routes for the Footage Offloader API and pages."""

import os
import json
import logging
import subprocess
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
)

from offloader.ssd import get_ssd_info, list_ssd_files, mount_ssd, unmount_ssd
from offloader.smb import mount_smb, unmount_smb, list_smb_subfolders, test_smb_connection
from offloader.copy_manager import CopyManager

bp = Blueprint("main", __name__)
logger = logging.getLogger("offloader.routes")


# ── Page routes ───────────────────────────────────────────────────────────────

@bp.route("/")
def index():
    return render_template("index.html")


@bp.route("/settings")
def settings_page():
    return render_template("settings.html")


# ── Config API ────────────────────────────────────────────────────────────────

@bp.route("/api/config", methods=["GET"])
def get_config():
    cfg = dict(current_app.config["OFFLOADER"])
    # Don't send password to frontend
    cfg["smb_password"] = "••••••••" if cfg.get("smb_password") else ""
    return jsonify(cfg)


@bp.route("/api/config", methods=["POST"])
def update_config():
    data = request.get_json()
    cfg = current_app.config["OFFLOADER"]

    for key in [
        "smb_host", "smb_share", "smb_username", "smb_domain",
        "discord_webhook_url", "discord_enabled",
    ]:
        if key in data:
            cfg[key] = data[key]

    # Only update password if it's not the masked placeholder
    if "smb_password" in data and data["smb_password"] != "••••••••":
        cfg["smb_password"] = data["smb_password"]

    save_fn = current_app.config["OFFLOADER_SAVE"]
    save_fn(cfg)
    return jsonify({"status": "ok"})


# ── SSD API ───────────────────────────────────────────────────────────────────

@bp.route("/api/ssd/info")
def ssd_info():
    cfg = current_app.config["OFFLOADER"]
    info = get_ssd_info(cfg["ssd_mount_point"])
    return jsonify(info)


@bp.route("/api/ssd/mount", methods=["POST"])
def ssd_mount():
    cfg = current_app.config["OFFLOADER"]
    result = mount_ssd(cfg["ssd_mount_point"])
    return jsonify(result)


@bp.route("/api/ssd/unmount", methods=["POST"])
def ssd_unmount():
    cfg = current_app.config["OFFLOADER"]
    result = unmount_ssd(cfg["ssd_mount_point"])
    return jsonify(result)


@bp.route("/api/ssd/files")
def ssd_files():
    cfg = current_app.config["OFFLOADER"]
    path = request.args.get("path", "")
    files = list_ssd_files(cfg["ssd_mount_point"], path)
    return jsonify(files)


# ── SMB API ───────────────────────────────────────────────────────────────────

@bp.route("/api/smb/test", methods=["POST"])
def smb_test():
    cfg = current_app.config["OFFLOADER"]
    result = test_smb_connection(cfg)
    return jsonify(result)


@bp.route("/api/discord/test", methods=["POST"])
def discord_test():
    cfg = current_app.config["OFFLOADER"]
    webhook_url = cfg.get("discord_webhook_url", "")
    if not webhook_url:
        return jsonify({"success": False, "error": "No webhook URL configured"})
    from offloader.discord_notify import test_discord_webhook
    result = test_discord_webhook(webhook_url)
    return jsonify(result)


@bp.route("/api/smb/mount", methods=["POST"])
def smb_mount():
    cfg = current_app.config["OFFLOADER"]
    result = mount_smb(cfg)
    return jsonify(result)


@bp.route("/api/smb/unmount", methods=["POST"])
def smb_unmount():
    cfg = current_app.config["OFFLOADER"]
    result = unmount_smb(cfg["smb_mount_point"])
    return jsonify(result)


@bp.route("/api/smb/subfolders")
def smb_subfolders():
    cfg = current_app.config["OFFLOADER"]
    # Ensure SMB is mounted
    mount_result = mount_smb(cfg)
    if not mount_result.get("mounted"):
        return jsonify({"error": "SMB not mounted", "subfolders": []})

    subfolders = list_smb_subfolders(cfg["smb_mount_point"])
    return jsonify({"subfolders": subfolders})


@bp.route("/api/smb/create-subfolder", methods=["POST"])
def smb_create_subfolder():
    cfg = current_app.config["OFFLOADER"]
    data = request.get_json()
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Folder name is required"}), 400

    # Sanitize
    name = name.replace("/", "_").replace("\\", "_").replace("..", "_")
    target = Path(cfg["smb_mount_point"]) / name
    try:
        target.mkdir(parents=True, exist_ok=True)
        return jsonify({"status": "ok", "name": name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Copy API ──────────────────────────────────────────────────────────────────

@bp.route("/api/copy/start", methods=["POST"])
def copy_start():
    data = request.get_json()
    files = data.get("files", [])
    subfolder = data.get("subfolder", "")

    if not files:
        return jsonify({"error": "No files selected"}), 400
    if not subfolder:
        return jsonify({"error": "No destination subfolder selected"}), 400

    cfg = current_app.config["OFFLOADER"]
    manager: CopyManager = current_app.config["COPY_MANAGER"]
    manager.update_config(cfg)

    # Ensure SMB is mounted
    mount_result = mount_smb(cfg)
    if not mount_result.get("mounted"):
        return jsonify({"error": "Cannot mount SMB share"}), 500

    result = manager.start_copy(files, subfolder)
    return jsonify(result)


@bp.route("/api/copy/status")
def copy_status():
    manager: CopyManager = current_app.config["COPY_MANAGER"]
    return jsonify(manager.get_status())


@bp.route("/api/copy/cancel", methods=["POST"])
def copy_cancel():
    manager: CopyManager = current_app.config["COPY_MANAGER"]
    manager.cancel()
    return jsonify({"status": "cancelling"})


@bp.route("/api/copy/reset", methods=["POST"])
def copy_reset():
    manager: CopyManager = current_app.config["COPY_MANAGER"]
    manager.reset()
    return jsonify({"status": "ok"})


# ── Speed Test API ────────────────────────────────────────────────────────────

@bp.route("/api/speedtest", methods=["POST"])
def speedtest():
    cfg = current_app.config["OFFLOADER"]

    # Mount SMB if not mounted
    mount_result = mount_smb(cfg)
    if not mount_result.get("mounted"):
        return jsonify({"error": "SMB not mounted"}), 500

    from offloader.speedtest import run_speedtest
    result = run_speedtest(cfg["smb_mount_point"])
    return jsonify(result)


# ── System API ────────────────────────────────────────────────────────────────

@bp.route("/api/system/info")
def system_info():
    """Basic system info: hostname, tailscale IP, uptime."""
    info = {}
    try:
        info["hostname"] = subprocess.check_output(
            ["hostname"], text=True
        ).strip()
    except Exception:
        info["hostname"] = "unknown"

    try:
        ts_output = subprocess.check_output(
            ["tailscale", "ip", "-4"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        info["tailscale_ip"] = ts_output
    except Exception:
        info["tailscale_ip"] = None

    try:
        with open("/proc/uptime", "r") as f:
            uptime_seconds = float(f.read().split()[0])
        hours = int(uptime_seconds // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        info["uptime"] = f"{hours}h {minutes}m"
    except Exception:
        info["uptime"] = "unknown"

    return jsonify(info)
