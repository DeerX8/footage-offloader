"""Background copy manager with progress tracking and Discord notifications."""

import os
import time
import logging
import shutil
import threading
from pathlib import Path
from typing import Optional

from offloader.discord_notify import send_discord_notification

logger = logging.getLogger("offloader.copy_manager")


class CopyManager:
    """Manages background file copy operations with progress tracking."""

    def __init__(self, config: dict):
        self.config = config
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._status = {
            "active": False,
            "progress": 0,
            "current_file": "",
            "current_file_index": 0,
            "total_files": 0,
            "bytes_copied": 0,
            "bytes_total": 0,
            "speed_bps": 0,
            "eta_seconds": 0,
            "error": None,
            "cancelled": False,
            "completed": False,
            "started_at": None,
            "finished_at": None,
            "subfolder": "",
            "files_completed": [],
            "files_failed": [],
        }
        self._notified_thresholds = set()

    def update_config(self, config: dict):
        self.config = config

    def get_status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def start_copy(self, files: list, subfolder: str) -> dict:
        """Start a background copy job."""
        with self._lock:
            if self._status["active"]:
                return {"error": "A copy operation is already running"}

        ssd_mount = self.config["ssd_mount_point"]
        smb_mount = self.config["smb_mount_point"]
        dest_dir = Path(smb_mount) / subfolder

        # Validate files and compute total size
        file_paths = []
        total_size = 0
        for rel_path in files:
            full_path = Path(ssd_mount) / rel_path
            if not full_path.exists():
                return {"error": f"File not found: {rel_path}"}
            if not str(full_path.resolve()).startswith(str(Path(ssd_mount).resolve())):
                return {"error": f"Access denied: {rel_path}"}
            size = full_path.stat().st_size
            file_paths.append((rel_path, full_path, size))
            total_size += size

        if not file_paths:
            return {"error": "No valid files to copy"}

        # Create destination
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return {"error": f"Cannot create destination folder: {e}"}

        # Reset state
        self._cancel_event.clear()
        self._notified_thresholds = set()
        with self._lock:
            self._status = {
                "active": True,
                "progress": 0,
                "current_file": "",
                "current_file_index": 0,
                "total_files": len(file_paths),
                "bytes_copied": 0,
                "bytes_total": total_size,
                "speed_bps": 0,
                "eta_seconds": 0,
                "error": None,
                "cancelled": False,
                "completed": False,
                "started_at": time.time(),
                "finished_at": None,
                "subfolder": subfolder,
                "files_completed": [],
                "files_failed": [],
            }

        # Send start notification
        self._notify_discord(
            "start",
            f"📦 **Copy started**\n"
            f"Files: {len(file_paths)}\n"
            f"Total size: {self._fmt_size(total_size)}\n"
            f"Destination: `{subfolder}`"
        )

        # Start background thread
        self._thread = threading.Thread(
            target=self._copy_worker,
            args=(file_paths, dest_dir, total_size),
            daemon=True,
        )
        self._thread.start()

        return {
            "status": "started",
            "total_files": len(file_paths),
            "total_size": total_size,
        }

    def cancel(self):
        """Signal the copy thread to cancel."""
        self._cancel_event.set()
        with self._lock:
            self._status["cancelled"] = True

    def reset(self):
        """Reset status after user dismisses completion overlay."""
        with self._lock:
            if not self._status["active"]:
                self._status = {
                    "active": False,
                    "progress": 0,
                    "current_file": "",
                    "current_file_index": 0,
                    "total_files": 0,
                    "bytes_copied": 0,
                    "bytes_total": 0,
                    "speed_bps": 0,
                    "eta_seconds": 0,
                    "error": None,
                    "cancelled": False,
                    "completed": False,
                    "started_at": None,
                    "finished_at": None,
                    "subfolder": "",
                    "files_completed": [],
                    "files_failed": [],
                }

    def _copy_worker(self, file_paths: list, dest_dir: Path, total_size: int):
        """Worker thread that performs the actual file copying."""
        bytes_copied_global = 0
        start_time = time.time()
        chunk_size = 4 * 1024 * 1024  # 4MB chunks

        for idx, (rel_path, src_path, file_size) in enumerate(file_paths):
            if self._cancel_event.is_set():
                break

            with self._lock:
                self._status["current_file"] = rel_path
                self._status["current_file_index"] = idx + 1

            # Preserve relative directory structure
            rel_parent = Path(rel_path).parent
            file_dest_dir = dest_dir / rel_parent if str(rel_parent) != "." else dest_dir
            file_dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = file_dest_dir / src_path.name

            try:
                bytes_copied_file = 0
                with open(src_path, "rb") as fsrc, open(dest_path, "wb") as fdst:
                    while True:
                        if self._cancel_event.is_set():
                            break
                        chunk = fsrc.read(chunk_size)
                        if not chunk:
                            break
                        fdst.write(chunk)
                        bytes_copied_file += len(chunk)
                        bytes_copied_global += len(chunk)

                        # Update progress
                        elapsed = time.time() - start_time
                        speed = bytes_copied_global / elapsed if elapsed > 0 else 0
                        remaining = total_size - bytes_copied_global
                        eta = remaining / speed if speed > 0 else 0
                        progress = (bytes_copied_global / total_size * 100) if total_size > 0 else 0

                        with self._lock:
                            self._status["bytes_copied"] = bytes_copied_global
                            self._status["speed_bps"] = speed
                            self._status["eta_seconds"] = eta
                            self._status["progress"] = min(progress, 100)

                        # Check notification thresholds
                        self._check_thresholds(progress)

                if self._cancel_event.is_set():
                    # Remove partial file
                    try:
                        dest_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    break

                # Verify file size
                if dest_path.stat().st_size == file_size:
                    with self._lock:
                        self._status["files_completed"].append(rel_path)
                    logger.info(f"Copied: {rel_path}")
                else:
                    raise IOError(f"Size mismatch after copy: {rel_path}")

            except Exception as e:
                logger.error(f"Error copying {rel_path}: {e}")
                with self._lock:
                    self._status["files_failed"].append(
                        {"file": rel_path, "error": str(e)}
                    )
                # Remove failed partial file
                try:
                    dest_path.unlink(missing_ok=True)
                except Exception:
                    pass

        # Finalize
        elapsed = time.time() - start_time
        with self._lock:
            self._status["active"] = False
            self._status["finished_at"] = time.time()

            if self._cancel_event.is_set():
                self._status["cancelled"] = True
                completed = len(self._status["files_completed"])
                total = self._status["total_files"]
                self._notify_discord(
                    "cancelled",
                    f"⛔ **Copy cancelled**\n"
                    f"Completed: {completed}/{total} files\n"
                    f"Time elapsed: {self._fmt_time(elapsed)}"
                )
            else:
                self._status["completed"] = True
                self._status["progress"] = 100
                failed = len(self._status["files_failed"])
                completed = len(self._status["files_completed"])

                if failed > 0:
                    self._notify_discord(
                        "complete_with_errors",
                        f"⚠️ **Copy completed with errors**\n"
                        f"✅ Completed: {completed}/{self._status['total_files']}\n"
                        f"❌ Failed: {failed}\n"
                        f"Total size: {self._fmt_size(bytes_copied_global)}\n"
                        f"Time: {self._fmt_time(elapsed)}\n"
                        f"Avg speed: {self._fmt_size(bytes_copied_global / elapsed if elapsed > 0 else 0)}/s"
                    )
                else:
                    self._notify_discord(
                        "complete",
                        f"✅ **Copy completed successfully**\n"
                        f"Files: {completed}\n"
                        f"Total size: {self._fmt_size(bytes_copied_global)}\n"
                        f"Time: {self._fmt_time(elapsed)}\n"
                        f"Avg speed: {self._fmt_size(bytes_copied_global / elapsed if elapsed > 0 else 0)}/s"
                    )

    def _check_thresholds(self, progress: float):
        """Send Discord notifications at 25/50/75/100% thresholds."""
        thresholds = [25, 50, 75]
        for t in thresholds:
            if progress >= t and t not in self._notified_thresholds:
                self._notified_thresholds.add(t)
                with self._lock:
                    status = dict(self._status)
                elapsed = time.time() - (status.get("started_at") or time.time())
                self._notify_discord(
                    f"progress_{t}",
                    f"📊 **Copy progress: {t}%**\n"
                    f"Files: {status['current_file_index']}/{status['total_files']}\n"
                    f"Copied: {self._fmt_size(status['bytes_copied'])} / {self._fmt_size(status['bytes_total'])}\n"
                    f"Speed: {self._fmt_size(status['speed_bps'])}/s\n"
                    f"ETA: {self._fmt_time(status['eta_seconds'])}\n"
                    f"Elapsed: {self._fmt_time(elapsed)}"
                )

    def _notify_discord(self, event_type: str, message: str):
        """Send a Discord notification if enabled."""
        if not self.config.get("discord_enabled"):
            return
        webhook_url = self.config.get("discord_webhook_url", "")
        if not webhook_url:
            return
        try:
            send_discord_notification(webhook_url, message)
        except Exception as e:
            logger.warning(f"Discord notification failed: {e}")

    @staticmethod
    def _fmt_size(size_bytes) -> str:
        """Format bytes to human readable string."""
        size = float(size_bytes)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if abs(size) < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"

    @staticmethod
    def _fmt_time(seconds) -> str:
        """Format seconds to human readable duration."""
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            m, s = divmod(seconds, 60)
            return f"{m}m {s}s"
        h, remainder = divmod(seconds, 3600)
        m, s = divmod(remainder, 60)
        return f"{h}h {m}m {s}s"
