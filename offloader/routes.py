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
        "transfer_mode",
        "smb_host", "smb_share", "smb_username", "smb_domain",
        "ssh_host", "ssh_user", "ssh_port", "ssh_key_path", "ssh_remote_path",
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


@bp.route("/api/ssh/test", methods=["POST"])
def ssh_test():
    """Test SSH connectivity for rsync mode."""
    cfg = current_app.config["OFFLOADER"]
    host = cfg.get("ssh_host", "")
    user = cfg.get("ssh_user", "")
    port = cfg.get("ssh_port", 22)
    key_path = cfg.get("ssh_key_path", "")
    remote_path = cfg.get("ssh_remote_path", "")

    if not host or not user:
        return jsonify({"success": False, "error": "SSH host and user required"})

    ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=accept-new",
               "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
               "-p", str(port)]
    if key_path:
        ssh_cmd += ["-i", key_path]
    ssh_cmd += [f"{user}@{host}", "echo ok"]

    try:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            # Also check remote path exists
            if remote_path:
                check_cmd = ssh_cmd[:-1] + [f"test -d {remote_path} && echo exists"]
                check = subprocess.run(check_cmd, capture_output=True, text=True, timeout=10)
                if "exists" in check.stdout:
                    return jsonify({"success": True, "message": f"Connected — remote path OK"})
                return jsonify({"success": True, "message": "Connected — remote path not found (will be created)"})
            return jsonify({"success": True, "message": "SSH connection successful"})
        error = result.stderr.strip()[:200]
        return jsonify({"success": False, "error": error or "SSH connection failed"})
    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "error": "Connection timed out"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@bp.route("/api/ssh/keygen", methods=["POST"])
def ssh_keygen():
    """Generate SSH key pair if none exists."""
    key_path = current_app.config["OFFLOADER"].get("ssh_key_path", "/root/.ssh/id_rsa")
    if Path(key_path).exists():
        # Read public key
        pub_path = key_path + ".pub"
        pub_key = ""
        if Path(pub_path).exists():
            pub_key = Path(pub_path).read_text().strip()
        return jsonify({"exists": True, "public_key": pub_key})

    try:
        Path(key_path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        result = subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", key_path, "-N", "", "-C", "footage-offloader"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            pub_key = Path(key_path + ".pub").read_text().strip()
            return jsonify({"exists": True, "generated": True, "public_key": pub_key})
        return jsonify({"exists": False, "error": result.stderr.strip()})
    except Exception as e:
        return jsonify({"exists": False, "error": str(e)})


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
    mode = cfg.get("transfer_mode", "smb")

    if mode == "rsync":
        # List remote subfolders via SSH
        host = cfg.get("ssh_host", "")
        user = cfg.get("ssh_user", "")
        port = cfg.get("ssh_port", 22)
        key_path = cfg.get("ssh_key_path", "")
        remote_path = cfg.get("ssh_remote_path", "")
        if not host or not user or not remote_path:
            return jsonify({"error": "SSH not configured", "subfolders": []})

        ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=accept-new",
                   "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
                   "-p", str(port)]
        if key_path:
            ssh_cmd += ["-i", key_path]
        ssh_cmd += [f"{user}@{host}",
                    f"mkdir -p {remote_path} && ls -1d {remote_path}/*/ 2>/dev/null | xargs -I{{}} basename {{}}"]
        try:
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=15)
            folders = [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]
            return jsonify({"subfolders": sorted(folders)})
        except Exception as e:
            return jsonify({"error": str(e), "subfolders": []})
    else:
        # SMB mode
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

    name = name.replace("/", "_").replace("\\", "_").replace("..", "_")
    mode = cfg.get("transfer_mode", "smb")

    if mode == "rsync":
        host = cfg.get("ssh_host", "")
        user = cfg.get("ssh_user", "")
        port = cfg.get("ssh_port", 22)
        key_path = cfg.get("ssh_key_path", "")
        remote_path = cfg.get("ssh_remote_path", "")
        ssh_cmd = ["ssh", "-o", "BatchMode=yes", "-p", str(port)]
        if key_path:
            ssh_cmd += ["-i", key_path]
        ssh_cmd += [f"{user}@{host}", f"mkdir -p '{remote_path}/{name}'"]
        try:
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                return jsonify({"status": "ok", "name": name})
            return jsonify({"error": result.stderr.strip()}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    else:
        target = Path(cfg["smb_mount_point"]) / name
        try:
            target.mkdir(parents=True, exist_ok=True)
            return jsonify({"status": "ok", "name": name})
        except Exception as e:
            return jsonify({"error": str(e)}), 500


@bp.route("/api/smb/check-existing", methods=["POST"])
def smb_check_existing():
    """Check which files already exist at the destination (by name + size match)."""
    cfg = current_app.config["OFFLOADER"]
    data = request.get_json()
    subfolder = data.get("subfolder", "")
    files = data.get("files", [])

    if not subfolder or not files:
        return jsonify({"existing": []})

    mode = cfg.get("transfer_mode", "smb")
    ssd_mount = cfg["ssd_mount_point"]

    if mode == "rsync":
        # Check via SSH: get list of filename:size pairs at remote dest
        host = cfg.get("ssh_host", "")
        user = cfg.get("ssh_user", "")
        port = cfg.get("ssh_port", 22)
        key_path = cfg.get("ssh_key_path", "")
        remote_path = cfg.get("ssh_remote_path", "")
        remote_dir = f"{remote_path}/{subfolder}"

        ssh_cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                   "-p", str(port)]
        if key_path:
            ssh_cmd += ["-i", key_path]
        # Get filename and size for all files in remote dir (non-recursive top-level)
        ssh_cmd += [f"{user}@{host}",
                    f"find '{remote_dir}' -maxdepth 2 -type f -printf '%f\\t%s\\n' 2>/dev/null"]
        try:
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30)
            remote_files = {}
            for line in result.stdout.strip().split('\n'):
                if '\t' in line:
                    name, size = line.rsplit('\t', 1)
                    try:
                        remote_files[name.strip()] = int(size.strip())
                    except ValueError:
                        pass

            existing = []
            for rel_path in files:
                fname = Path(rel_path).name
                src = Path(ssd_mount) / rel_path
                if fname in remote_files and src.exists():
                    try:
                        if remote_files[fname] == src.stat().st_size:
                            existing.append(rel_path)
                    except OSError:
                        pass
            return jsonify({"existing": existing})
        except Exception:
            return jsonify({"existing": []})
    else:
        # SMB mode
        from offloader.smb import mount_smb
        mount_smb(cfg)
        dest_dir = Path(cfg["smb_mount_point"]) / subfolder
        existing = []
        if dest_dir.exists():
            for rel_path in files:
                dest_file = dest_dir / Path(rel_path).name
                src_file = Path(ssd_mount) / rel_path
                if dest_file.exists():
                    try:
                        if dest_file.stat().st_size == src_file.stat().st_size:
                            existing.append(rel_path)
                    except OSError:
                        pass
        return jsonify({"existing": existing})


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

    mode = cfg.get("transfer_mode", "smb")

    if mode == "rsync":
        # Validate SSH config
        if not cfg.get("ssh_host") or not cfg.get("ssh_user") or not cfg.get("ssh_remote_path"):
            return jsonify({"error": "SSH not configured — check Settings"}), 500
        result = manager.start_copy(files, subfolder)
    else:
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
