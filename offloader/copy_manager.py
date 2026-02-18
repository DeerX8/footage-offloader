"""Background copy manager with progress tracking and Discord notifications.

Supports two copy backends:
  - SMB: Python file I/O over mounted CIFS share (best for LAN)
  - rsync: rsync over SSH (best for WAN / mobile / Tailscale)
"""

import os
import re
import time
import logging
import signal
import subprocess
import threading
from pathlib import Path
from typing import Optional

from offloader.discord_notify import send_discord_notification

logger = logging.getLogger("offloader.copy_manager")

_EMPTY_STATUS = {
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


class CopyManager:
    """Manages background file copy operations with progress tracking."""

    def __init__(self, config: dict):
        self.config = config
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._rsync_proc: Optional[subprocess.Popen] = None
        self._status = dict(_EMPTY_STATUS)
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
        mode = self.config.get("transfer_mode", "smb")

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

        # For SMB mode, verify destination
        if mode == "smb":
            dest_dir = Path(self.config["smb_mount_point"]) / subfolder
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                return {"error": f"Cannot create destination folder: {e}"}

        # Reset state
        self._cancel_event.clear()
        self._rsync_proc = None
        self._notified_thresholds = set()
        with self._lock:
            self._status = {
                **_EMPTY_STATUS,
                "active": True,
                "total_files": len(file_paths),
                "bytes_total": total_size,
                "started_at": time.time(),
                "subfolder": subfolder,
                "files_completed": [],
                "files_failed": [],
            }

        # Send start notification
        self._notify_discord(
            "start",
            f"📦 **Copy started** ({mode.upper()})\n"
            f"Files: {len(file_paths)}\n"
            f"Total size: {self._fmt_size(total_size)}\n"
            f"Destination: `{subfolder}`"
        )

        # Start background thread
        if mode == "rsync":
            worker = self._rsync_worker
            args = (file_paths, subfolder, total_size)
        else:
            worker = self._smb_worker
            dest_dir = Path(self.config["smb_mount_point"]) / subfolder
            args = (file_paths, dest_dir, total_size)

        self._thread = threading.Thread(target=worker, args=args, daemon=True)
        self._thread.start()

        return {
            "status": "started",
            "total_files": len(file_paths),
            "total_size": total_size,
            "mode": mode,
        }

    def cancel(self):
        """Signal the copy thread to cancel."""
        self._cancel_event.set()
        with self._lock:
            self._status["cancelled"] = True
        # Kill rsync process if running
        proc = self._rsync_proc
        if proc and proc.poll() is None:
            try:
                proc.send_signal(signal.SIGTERM)
            except Exception:
                pass

    def reset(self):
        """Reset status after user dismisses completion overlay."""
        with self._lock:
            if not self._status["active"]:
                self._status = dict(_EMPTY_STATUS)

    # ── rsync worker ──────────────────────────────────────────────────────────

    MAX_RETRIES = 50          # Keep retrying for a long time (mobile drops)
    RETRY_BASE_DELAY = 5      # Start with 5s wait
    RETRY_MAX_DELAY = 60      # Cap at 60s between retries

    def _rsync_worker(self, file_paths: list, subfolder: str, total_size: int):
        """Copy files using rsync over SSH — optimal for WAN/mobile.

        Copies files one-by-one (flat) to the remote destination:
          - No parent directory structure from SSD is recreated
          - Each file's completion is tracked individually
          - --checksum: verifies every file byte-for-byte after transfer
          - --partial: keeps incomplete files so resume works after drops
          - Auto-retry per file with exponential backoff
        """
        start_time = time.time()
        cfg = self.config
        host = cfg["ssh_host"]
        user = cfg["ssh_user"]
        port = cfg.get("ssh_port", 22)
        key_path = cfg.get("ssh_key_path", "")
        remote_base = cfg["ssh_remote_path"].rstrip("/")
        remote_dest = f"{remote_base}/{subfolder}/"

        # SSH command with keepalive to detect dead connections faster
        ssh_cmd = (
            f"ssh -o StrictHostKeyChecking=accept-new"
            f" -o ConnectTimeout=15"
            f" -o ServerAliveInterval=10"
            f" -o ServerAliveCountMax=3"
            f" -p {port}"
        )
        if key_path:
            ssh_cmd += f" -i {key_path}"

        logger.info(f"rsync -> {user}@{host}:{remote_dest} ({len(file_paths)} files, {self._fmt_size(total_size)})")

        # Ensure remote dir exists
        self._ensure_remote_dir(cfg, remote_dest)

        # ── Per-file copy loop ────────────────────────────────────────
        bytes_completed_prev = 0  # Total bytes of all previously finished files

        for file_idx, (rel_path, src_path, file_size) in enumerate(file_paths):
            if self._cancel_event.is_set():
                break

            with self._lock:
                self._status["current_file"] = rel_path
                self._status["current_file_index"] = file_idx + 1

            # Build rsync command for this single file (flat copy — just the file)
            rsync_cmd = [
                "rsync", "-t",         # preserve times only (no -a to avoid dir structure)
                "--whole-file",
                "--checksum",
                "--partial",
                "--info=progress2",
                "--timeout=120",
                "-e", ssh_cmd,
                str(src_path),
                f"{user}@{host}:{remote_dest}",
            ]

            # ── Retry loop for this file ──────────────────────────────
            attempt = 0
            rc = -1
            stderr = ""

            while attempt <= self.MAX_RETRIES:
                if self._cancel_event.is_set():
                    break

                if attempt > 0:
                    delay = min(self.RETRY_BASE_DELAY * (2 ** min(attempt - 1, 4)),
                                self.RETRY_MAX_DELAY)
                    logger.warning(f"rsync retry #{attempt} for {rel_path} in {delay}s...")
                    with self._lock:
                        self._status["current_file"] = f"Connection lost — retry #{attempt} in {delay}s..."

                    for _ in range(int(delay)):
                        if self._cancel_event.is_set():
                            break
                        time.sleep(1)

                    if self._cancel_event.is_set():
                        break

                    with self._lock:
                        self._status["current_file"] = f"Reconnecting (attempt #{attempt + 1})..."

                    if attempt == 1 or attempt % 5 == 0:
                        self._notify_discord(
                            "retry",
                            f"🔄 **Connection lost — retrying** (attempt #{attempt + 1})\n"
                            f"File: `{Path(rel_path).name}`\n"
                            f"Progress: {self._status.get('progress', 0):.0f}%\n"
                            f"rsync will resume where it left off"
                        )

                try:
                    self._rsync_proc = subprocess.Popen(
                        rsync_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        bufsize=1,
                    )

                    progress_re = re.compile(
                        r'([\d,]+)\s+(\d+)%\s+([\d.]+\w+/s)\s+([\d:]+)'
                    )

                    for line in self._rsync_proc.stdout:
                        if self._cancel_event.is_set():
                            break
                        line = line.strip()
                        m = progress_re.search(line)
                        if not m:
                            continue

                        file_bytes_done = int(m.group(1).replace(",", ""))
                        speed_bps = self._parse_speed(m.group(3))
                        eta_file = self._parse_eta(m.group(4))

                        total_bytes_done = bytes_completed_prev + file_bytes_done
                        overall_pct = (total_bytes_done / total_size * 100) if total_size > 0 else 0

                        # Estimate total ETA from current speed
                        remaining_total = total_size - total_bytes_done
                        eta_total = remaining_total / speed_bps if speed_bps > 0 else eta_file

                        with self._lock:
                            self._status["bytes_copied"] = total_bytes_done
                            self._status["progress"] = min(overall_pct, 100)
                            self._status["speed_bps"] = speed_bps
                            self._status["eta_seconds"] = eta_total
                            self._status["current_file"] = rel_path

                        self._check_thresholds(overall_pct)

                    self._rsync_proc.wait(timeout=300)
                    rc = self._rsync_proc.returncode
                    stderr = self._rsync_proc.stderr.read() if self._rsync_proc.stderr else ""

                except Exception as e:
                    logger.error(f"rsync exception for {rel_path}: {e}")
                    rc = -1
                    stderr = str(e)

                # rc=0: success
                if rc == 0:
                    break

                # Retryable exit codes
                if rc in (10, 12, 23, 24, 30, 35, 255, -1):
                    attempt += 1
                    continue
                else:
                    logger.error(f"rsync non-retryable error (rc={rc}) for {rel_path}: {stderr.strip()[:200]}")
                    break

            # ── File finished ─────────────────────────────────────────
            if self._cancel_event.is_set():
                break

            if rc == 0:
                bytes_completed_prev += file_size
                with self._lock:
                    self._status["files_completed"].append(rel_path)
                    self._status["bytes_copied"] = bytes_completed_prev
                logger.info(f"Copied: {rel_path} ({self._fmt_size(file_size)})")
            else:
                # Count the file bytes as done even on failure so progress doesn't go backwards
                bytes_completed_prev += file_size
                err_msg = stderr.strip()[:200] if stderr else f"rsync exit code {rc}"
                with self._lock:
                    self._status["files_failed"].append({"file": rel_path, "error": err_msg})
                logger.error(f"Failed: {rel_path} — {err_msg}")

        # ── Finalize ──────────────────────────────────────────────────
        elapsed = time.time() - start_time
        discord_msg = None
        discord_event = None

        with self._lock:
            self._status["active"] = False
            self._status["finished_at"] = time.time()

            if self._cancel_event.is_set():
                self._status["cancelled"] = True
                completed = len(self._status["files_completed"])
                total = self._status["total_files"]
                discord_event = "cancelled"
                discord_msg = (
                    f"⛔ **Copy cancelled** (rsync)\n"
                    f"Completed: {completed}/{total} files\n"
                    f"Time elapsed: {self._fmt_time(elapsed)}"
                )
            else:
                self._status["completed"] = True
                self._status["progress"] = 100
                failed = len(self._status["files_failed"])
                completed = len(self._status["files_completed"])
                avg_speed = bytes_completed_prev / elapsed if elapsed > 0 else 0

                if failed > 0:
                    discord_event = "complete_with_errors"
                    discord_msg = (
                        f"⚠️ **Copy completed with errors** (rsync)\n"
                        f"✅ Completed: {completed}/{self._status['total_files']}\n"
                        f"❌ Failed: {failed}\n"
                        f"Total: {self._fmt_size(bytes_completed_prev)}\n"
                        f"Time: {self._fmt_time(elapsed)}\n"
                        f"Avg speed: {self._fmt_size(avg_speed)}/s"
                    )
                else:
                    discord_event = "complete"
                    discord_msg = (
                        f"✅ **Copy completed ✓ verified** (rsync)\n"
                        f"Files: {completed}\n"
                        f"Total size: {self._fmt_size(bytes_completed_prev)}\n"
                        f"Time: {self._fmt_time(elapsed)}\n"
                        f"Avg speed: {self._fmt_size(avg_speed)}/s"
                    )

        # Send Discord notification outside the lock
        if discord_msg:
            self._notify_discord(discord_event, discord_msg)

    def _ensure_remote_dir(self, cfg: dict, remote_dest: str):
        """Create remote destination directory via SSH."""
        port = cfg.get("ssh_port", 22)
        key_path = cfg.get("ssh_key_path", "")
        ssh_cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                   "-p", str(port)]
        if key_path:
            ssh_cmd += ["-i", key_path]
        ssh_cmd += [f"{cfg['ssh_user']}@{cfg['ssh_host']}",
                    f"mkdir -p '{remote_dest}'"]
        try:
            subprocess.run(ssh_cmd, capture_output=True, timeout=20)
        except Exception as e:
            logger.warning(f"Could not ensure remote dir: {e}")

    @staticmethod
    def _parse_speed(s: str) -> float:
        """Parse rsync speed string like '12.34MB/s' to bytes/sec."""
        s = s.replace("/s", "").strip()
        multipliers = {"B": 1, "kB": 1024, "KB": 1024,
                       "MB": 1024**2, "GB": 1024**3}
        for suffix, mult in sorted(multipliers.items(), key=lambda x: -len(x[0])):
            if s.endswith(suffix):
                try:
                    return float(s[:-len(suffix)]) * mult
                except ValueError:
                    return 0
        try:
            return float(s)
        except ValueError:
            return 0

    @staticmethod
    def _parse_eta(s: str) -> float:
        """Parse rsync ETA string like '0:01:23' to seconds."""
        parts = s.split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            return int(parts[0])
        except ValueError:
            return 0

    # ── SMB worker (flat copy, no parent dirs) ─────────────────────────────────

    def _smb_worker(self, file_paths: list, dest_dir: Path, total_size: int):
        """Copy files via mounted SMB share — best for LAN.

        Copies files flat to the destination (no parent directory structure).
        """
        bytes_copied_global = 0
        start_time = time.time()
        chunk_size = 8 * 1024 * 1024  # 8MB chunks

        dest_dir.mkdir(parents=True, exist_ok=True)

        for idx, (rel_path, src_path, file_size) in enumerate(file_paths):
            if self._cancel_event.is_set():
                break

            with self._lock:
                self._status["current_file"] = rel_path
                self._status["current_file_index"] = idx + 1

            # Flat copy: file goes directly into dest_dir, no sub-directories
            dest_path = dest_dir / src_path.name

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

                        self._check_thresholds(progress)

                if self._cancel_event.is_set():
                    try:
                        dest_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    break

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
                try:
                    dest_path.unlink(missing_ok=True)
                except Exception:
                    pass

        elapsed = time.time() - start_time
        discord_msg = None
        discord_event = None

        with self._lock:
            self._status["active"] = False
            self._status["finished_at"] = time.time()

            if self._cancel_event.is_set():
                self._status["cancelled"] = True
                completed = len(self._status["files_completed"])
                total = self._status["total_files"]
                discord_event = "cancelled"
                discord_msg = (
                    f"⛔ **Copy cancelled** (SMB)\n"
                    f"Completed: {completed}/{total} files\n"
                    f"Time elapsed: {self._fmt_time(elapsed)}"
                )
            else:
                self._status["completed"] = True
                self._status["progress"] = 100
                failed = len(self._status["files_failed"])
                completed = len(self._status["files_completed"])
                avg_speed = bytes_copied_global / elapsed if elapsed > 0 else 0

                if failed > 0:
                    discord_event = "complete_with_errors"
                    discord_msg = (
                        f"⚠️ **Copy completed with errors** (SMB)\n"
                        f"✅ Completed: {completed}/{self._status['total_files']}\n"
                        f"❌ Failed: {failed}\n"
                        f"Total: {self._fmt_size(bytes_copied_global)}\n"
                        f"Time: {self._fmt_time(elapsed)}\n"
                        f"Avg speed: {self._fmt_size(avg_speed)}/s"
                    )
                else:
                    discord_event = "complete"
                    discord_msg = (
                        f"✅ **Copy completed** (SMB)\n"
                        f"Files: {completed}\n"
                        f"Total: {self._fmt_size(bytes_copied_global)}\n"
                        f"Time: {self._fmt_time(elapsed)}\n"
                        f"Avg speed: {self._fmt_size(avg_speed)}/s"
                    )

        # Send Discord notification outside the lock
        if discord_msg:
            self._notify_discord(discord_event, discord_msg)

    # ── Shared helpers ────────────────────────────────────────────────────────

    def _check_thresholds(self, progress: float):
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
                    f"Copied: {self._fmt_size(status['bytes_copied'])} / {self._fmt_size(status['bytes_total'])}\n"
                    f"Speed: {self._fmt_size(status['speed_bps'])}/s\n"
                    f"ETA: {self._fmt_time(status['eta_seconds'])}\n"
                    f"Elapsed: {self._fmt_time(elapsed)}"
                )

    def _notify_discord(self, event_type: str, message: str):
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
        size = float(size_bytes)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if abs(size) < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"

    @staticmethod
    def _fmt_time(seconds) -> str:
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            m, s = divmod(seconds, 60)
            return f"{m}m {s}s"
        h, remainder = divmod(seconds, 3600)
        m, s = divmod(remainder, 60)
        return f"{h}h {m}m {s}s"
