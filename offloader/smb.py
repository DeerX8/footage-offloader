"""SMB share mounting and management."""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("offloader.smb")


def _is_mounted(mount_point: str) -> bool:
    """Check if the SMB share is currently mounted."""
    try:
        result = subprocess.run(
            ["findmnt", "-n", mount_point],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def mount_smb(config: dict) -> dict:
    """Mount the SMB share using cifs-utils."""
    mount_point = config["smb_mount_point"]

    if _is_mounted(mount_point):
        return {"mounted": True, "message": "Already mounted"}

    host = config.get("smb_host", "")
    share = config.get("smb_share", "")
    username = config.get("smb_username", "")
    password = config.get("smb_password", "")
    domain = config.get("smb_domain", "WORKGROUP")

    if not host or not share:
        return {"mounted": False, "error": "SMB host and share not configured"}

    Path(mount_point).mkdir(parents=True, exist_ok=True)

    # Build the SMB URL
    smb_url = f"//{host}/{share}"

    # Build mount options
    opts = [
        f"username={username}" if username else "guest",
        f"password={password}" if password else "",
        f"domain={domain}",
        "iocharset=utf8",
        "file_mode=0775",
        "dir_mode=0775",
        "vers=3.0",
        "nofail",
    ]
    opts = [o for o in opts if o]  # Remove empty
    opts_str = ",".join(opts)

    try:
        result = subprocess.run(
            ["sudo", "mount", "-t", "cifs", smb_url, mount_point, "-o", opts_str],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info(f"SMB share mounted: {smb_url} -> {mount_point}")
            return {"mounted": True, "message": f"Mounted {smb_url}"}
        else:
            # Try with vers=2.1 as fallback
            opts_str_v2 = opts_str.replace("vers=3.0", "vers=2.1")
            result2 = subprocess.run(
                ["sudo", "mount", "-t", "cifs", smb_url, mount_point, "-o", opts_str_v2],
                capture_output=True, text=True, timeout=30,
            )
            if result2.returncode == 0:
                logger.info(f"SMB share mounted (v2.1): {smb_url} -> {mount_point}")
                return {"mounted": True, "message": f"Mounted {smb_url} (SMBv2.1)"}

            error = result.stderr.strip() or result2.stderr.strip()
            logger.error(f"SMB mount failed: {error}")
            return {"mounted": False, "error": f"Mount failed: {error}"}
    except Exception as e:
        logger.error(f"SMB mount exception: {e}")
        return {"mounted": False, "error": str(e)}


def unmount_smb(mount_point: str) -> dict:
    """Unmount the SMB share."""
    if not _is_mounted(mount_point):
        return {"status": "ok", "message": "Not mounted"}

    try:
        result = subprocess.run(
            ["sudo", "umount", mount_point],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return {"status": "ok", "message": "SMB unmounted"}
        return {"status": "error", "error": result.stderr.strip()}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def test_smb_connection(config: dict) -> dict:
    """Test if the SMB share is accessible."""
    host = config.get("smb_host", "")
    share = config.get("smb_share", "")
    username = config.get("smb_username", "")
    password = config.get("smb_password", "")

    if not host:
        return {"success": False, "error": "No SMB host configured"}

    try:
        # Use smbclient to test connection
        cmd = ["smbclient", f"//{host}/{share}", "-N", "-c", "dir"]
        if username:
            cmd = [
                "smbclient", f"//{host}/{share}",
                "-U", f"{username}%{password}" if password else username,
                "-c", "dir",
            ]

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return {"success": True, "message": "Connection successful"}
        return {"success": False, "error": result.stderr.strip()[:200]}
    except FileNotFoundError:
        return {"success": False, "error": "smbclient not installed"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def list_smb_subfolders(mount_point: str) -> list:
    """List first-level subdirectories in the mounted SMB share."""
    base = Path(mount_point)
    if not base.exists() or not base.is_dir():
        return []

    folders = []
    try:
        for entry in sorted(base.iterdir(), key=lambda e: e.name.lower()):
            if entry.is_dir() and not entry.name.startswith("."):
                folders.append(entry.name)
    except PermissionError:
        logger.warning("Permission denied listing SMB subfolders")
    return folders
