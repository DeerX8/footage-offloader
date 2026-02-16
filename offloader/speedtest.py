"""Speed test utility to measure write throughput to the SMB share."""

import os
import time
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger("offloader.speedtest")

# Test with 256MB of data
TEST_SIZE_BYTES = 256 * 1024 * 1024
CHUNK_SIZE = 4 * 1024 * 1024  # 4MB chunks


def run_speedtest(smb_mount_point: str) -> dict:
    """Run a write speed test to the SMB mount point.
    
    Writes a temporary test file and measures throughput.
    """
    mount_path = Path(smb_mount_point)
    if not mount_path.exists() or not mount_path.is_dir():
        return {"error": "SMB mount point not available"}

    test_file = mount_path / ".speedtest_tmp"

    try:
        # Generate a chunk of random-ish data (repeating for speed)
        chunk = os.urandom(CHUNK_SIZE)
        chunks_needed = TEST_SIZE_BYTES // CHUNK_SIZE

        # Write test
        start = time.monotonic()
        bytes_written = 0

        with open(test_file, "wb") as f:
            for _ in range(chunks_needed):
                f.write(chunk)
                bytes_written += CHUNK_SIZE
            f.flush()
            os.fsync(f.fileno())

        elapsed = time.monotonic() - start

        # Calculate speed
        speed_bps = bytes_written / elapsed if elapsed > 0 else 0
        speed_mbps = speed_bps / (1024 * 1024)

        result = {
            "speed_bps": speed_bps,
            "speed_mbps": round(speed_mbps, 1),
            "test_size_bytes": bytes_written,
            "elapsed_seconds": round(elapsed, 2),
            "formatted": f"{speed_mbps:.1f} MB/s",
        }

        logger.info(f"Speed test result: {speed_mbps:.1f} MB/s ({elapsed:.1f}s for {bytes_written / 1024 / 1024:.0f}MB)")
        return result

    except PermissionError:
        return {"error": "Permission denied writing to SMB share"}
    except OSError as e:
        return {"error": f"I/O error during speed test: {e}"}
    except Exception as e:
        return {"error": f"Speed test failed: {e}"}
    finally:
        # Clean up test file
        try:
            test_file.unlink(missing_ok=True)
        except Exception:
            pass
