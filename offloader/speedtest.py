"""Speed test utility to measure write throughput to the SMB share.

Uses a time-based approach (like speedtest.net):
  1. Warmup for a few seconds so TCP/SMB reach steady state
  2. Measure for a fixed duration and count bytes written
This way slow links don't take forever and fast links get enough data.
"""

import os
import time
import logging
from pathlib import Path

logger = logging.getLogger("offloader.speedtest")

CHUNK_SIZE = 8 * 1024 * 1024   # 8MB per write — matches typical SMB buffer
WARMUP_SECONDS = 3             # Let TCP window + SMB buffers ramp up
MEASURE_SECONDS = 10           # Measure sustained throughput for this long


def run_speedtest(smb_mount_point: str) -> dict:
    """Run a time-based write speed test to the SMB mount point."""
    mount_path = Path(smb_mount_point)
    if not mount_path.exists() or not mount_path.is_dir():
        return {"error": "SMB mount point not available"}

    test_file = mount_path / ".speedtest_tmp"

    try:
        # Pre-generate one chunk in RAM (not timed)
        chunk = os.urandom(CHUNK_SIZE)

        with open(test_file, "wb") as f:
            # ── Warmup phase (not measured) ───────────────────────────
            warmup_end = time.monotonic() + WARMUP_SECONDS
            while time.monotonic() < warmup_end:
                f.write(chunk)

            # ── Measured phase ────────────────────────────────────────
            bytes_written = 0
            start = time.monotonic()
            deadline = start + MEASURE_SECONDS

            while time.monotonic() < deadline:
                f.write(chunk)
                bytes_written += CHUNK_SIZE

            f.flush()
            elapsed = time.monotonic() - start

        speed_bps = bytes_written / elapsed if elapsed > 0 else 0
        speed_mbps = speed_bps / (1024 * 1024)

        result = {
            "speed_bps": speed_bps,
            "speed_mbps": round(speed_mbps, 1),
            "test_size_bytes": bytes_written,
            "elapsed_seconds": round(elapsed, 2),
            "formatted": f"{speed_mbps:.1f} MB/s",
        }

        logger.info(
            f"Speed test: {speed_mbps:.1f} MB/s "
            f"({bytes_written // 1024 // 1024}MB in {elapsed:.1f}s, "
            f"{WARMUP_SECONDS}s warmup)"
        )
        return result

    except PermissionError:
        return {"error": "Permission denied writing to SMB share"}
    except OSError as e:
        return {"error": f"I/O error during speed test: {e}"}
    except Exception as e:
        return {"error": f"Speed test failed: {e}"}
    finally:
        try:
            test_file.unlink(missing_ok=True)
        except Exception:
            pass
