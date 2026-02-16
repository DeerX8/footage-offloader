"""SSD detection, mounting, and file listing utilities."""

import os
import logging
import subprocess
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger("offloader.ssd")

# File extensions we consider "real" content (video, photo, audio, documents)
MEDIA_EXTENSIONS = {
    # Video
    ".mp4", ".mov", ".avi", ".mkv", ".mxf", ".r3d", ".braw", ".ari",
    ".prores", ".dnxhd", ".mts", ".m2ts", ".wmv", ".flv", ".webm",
    ".mpg", ".mpeg", ".m4v", ".ts", ".vob", ".3gp",
    # Photo / Image
    ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".cr2", ".cr3", ".nef",
    ".arw", ".dng", ".raw", ".orf", ".rw2", ".pef", ".raf", ".heic",
    ".heif", ".bmp", ".gif", ".webp", ".psd", ".xcf", ".svg",
    # Audio
    ".wav", ".mp3", ".aac", ".flac", ".ogg", ".wma", ".aiff", ".m4a",
    # Documents / Project files
    ".pdf", ".xml", ".srt", ".edl", ".fcpxml", ".prproj", ".aep",
    ".drp", ".lut", ".cube", ".txt", ".csv", ".json", ".zip",
    ".tar", ".gz", ".7z", ".rar",
}

# Filenames/patterns to always skip
SKIP_NAMES = {
    ".ds_store", "thumbs.db", ".spotlight-v100", ".trashes",
    ".fseventsd", ".temporaryitems", "desktop.ini", "._.ds_store",
    "$recycle.bin", "system volume information",
}

SKIP_PREFIXES = (".", "._")


def _is_visible_file(name: str) -> bool:
    """Check if a filename is a real user file (not hidden/metadata)."""
    lower = name.lower()
    if lower in SKIP_NAMES:
        return False
    if any(name.startswith(p) for p in SKIP_PREFIXES):
        return False
    return True


def _is_visible_dir(name: str) -> bool:
    """Check if a directory name is visible and not system metadata."""
    lower = name.lower()
    if lower in SKIP_NAMES:
        return False
    if name.startswith("."):
        return False
    return True


def _find_usb_block_device() -> Optional[str]:
    """Find the first USB-connected block device (SSD)."""
    try:
        result = subprocess.run(
            ["lsblk", "-Jpo", "NAME,TYPE,TRAN,SIZE,MOUNTPOINT,FSTYPE,LABEL"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None

        import json
        data = json.loads(result.stdout)
        for device in data.get("blockdevices", []):
            if device.get("tran") == "usb":
                # Look for partitions first
                children = device.get("children", [])
                for child in children:
                    if child.get("type") == "part" and child.get("fstype"):
                        return child["name"]
                # If no partitions, use the device itself if it has a filesystem
                if device.get("fstype"):
                    return device["name"]
        return None
    except Exception as e:
        logger.error(f"Error finding USB device: {e}")
        return None


def get_ssd_info(mount_point: str) -> dict:
    """Get SSD mount status and disk info."""
    info = {
        "mounted": False,
        "device": None,
        "label": None,
        "filesystem": None,
        "total": 0,
        "used": 0,
        "free": 0,
        "mount_point": mount_point,
    }

    # Check if anything is mounted at mount_point
    try:
        result = subprocess.run(
            ["findmnt", "-J", mount_point],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            import json
            mnt_data = json.loads(result.stdout)
            fs_list = mnt_data.get("filesystems", [])
            if fs_list:
                fs = fs_list[0]
                info["mounted"] = True
                info["device"] = fs.get("source", "")
                info["filesystem"] = fs.get("fstype", "")

                usage = shutil.disk_usage(mount_point)
                info["total"] = usage.total
                info["used"] = usage.used
                info["free"] = usage.free
    except Exception as e:
        logger.warning(f"Error checking mount: {e}")

    # Try to get label
    if info["device"]:
        try:
            lbl = subprocess.run(
                ["lsblk", "-no", "LABEL", info["device"]],
                capture_output=True, text=True, timeout=5,
            )
            info["label"] = lbl.stdout.strip() or None
        except Exception:
            pass

    # If not mounted, check if a USB device exists
    if not info["mounted"]:
        dev = _find_usb_block_device()
        if dev:
            info["device"] = dev
            try:
                lbl = subprocess.run(
                    ["lsblk", "-no", "LABEL,FSTYPE", dev],
                    capture_output=True, text=True, timeout=5,
                )
                parts = lbl.stdout.strip().split()
                if len(parts) >= 1:
                    info["label"] = parts[0] if parts[0] else None
                if len(parts) >= 2:
                    info["filesystem"] = parts[1]
            except Exception:
                pass

    return info


def mount_ssd(mount_point: str) -> dict:
    """Auto-detect and mount USB SSD."""
    # Check if already mounted
    info = get_ssd_info(mount_point)
    if info["mounted"]:
        return {"mounted": True, "message": "Already mounted"}

    device = _find_usb_block_device()
    if not device:
        return {"mounted": False, "error": "No USB SSD detected"}

    Path(mount_point).mkdir(parents=True, exist_ok=True)

    try:
        # Try mounting with common options
        result = subprocess.run(
            ["sudo", "mount", "-o", "ro,noatime", device, mount_point],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info(f"Mounted {device} at {mount_point}")
            return {"mounted": True, "device": device}
        else:
            # Try without readonly for exfat/ntfs
            result2 = subprocess.run(
                ["sudo", "mount", "-o", "noatime", device, mount_point],
                capture_output=True, text=True, timeout=30,
            )
            if result2.returncode == 0:
                logger.info(f"Mounted {device} at {mount_point} (rw)")
                return {"mounted": True, "device": device}
            return {
                "mounted": False,
                "error": f"Mount failed: {result.stderr.strip()}"
            }
    except Exception as e:
        return {"mounted": False, "error": str(e)}


def unmount_ssd(mount_point: str) -> dict:
    """Unmount the SSD."""
    try:
        result = subprocess.run(
            ["sudo", "umount", mount_point],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return {"status": "ok", "message": "SSD unmounted"}
        return {"status": "error", "error": result.stderr.strip()}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def list_ssd_files(mount_point: str, relative_path: str = "") -> dict:
    """List files and directories at the given path, filtering metadata/hidden."""
    base = Path(mount_point)
    target = base / relative_path if relative_path else base

    if not target.exists():
        return {"error": "Path not found", "files": [], "directories": []}

    if not str(target.resolve()).startswith(str(base.resolve())):
        return {"error": "Access denied", "files": [], "directories": []}

    directories = []
    files = []

    try:
        for entry in sorted(target.iterdir(), key=lambda e: e.name.lower()):
            name = entry.name

            if entry.is_dir():
                if not _is_visible_dir(name):
                    continue
                # Count items inside
                try:
                    count = sum(
                        1 for e in entry.iterdir()
                        if (e.is_file() and _is_visible_file(e.name))
                        or (e.is_dir() and _is_visible_dir(e.name))
                    )
                except PermissionError:
                    count = 0

                directories.append({
                    "name": name,
                    "path": str(entry.relative_to(base)),
                    "items": count,
                })

            elif entry.is_file():
                if not _is_visible_file(name):
                    continue
                ext = entry.suffix.lower()
                try:
                    stat = entry.stat()
                    files.append({
                        "name": name,
                        "path": str(entry.relative_to(base)),
                        "size": stat.st_size,
                        "ext": ext,
                        "modified": stat.st_mtime,
                    })
                except (PermissionError, OSError):
                    continue
    except PermissionError:
        return {"error": "Permission denied", "files": [], "directories": []}

    return {
        "current_path": relative_path,
        "parent_path": str(Path(relative_path).parent) if relative_path else None,
        "directories": directories,
        "files": files,
    }
