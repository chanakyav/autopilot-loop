"""Optional background keep-alive heartbeat thread.

Disabled by default. Only needed as a fallback if the codespace
idle timeout API doesn't work.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

_heartbeat_thread = None
_stop_event = threading.Event()


def start_keepalive(interval_seconds=300):
    """Start a background heartbeat thread.

    Writes a timestamp to /tmp/autopilot-keepalive periodically.
    """
    global _heartbeat_thread

    if _heartbeat_thread is not None and _heartbeat_thread.is_alive():
        logger.debug("Keep-alive already running")
        return

    _stop_event.clear()
    _heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(interval_seconds,),
        daemon=True,
        name="autopilot-keepalive",
    )
    _heartbeat_thread.start()
    logger.info("Keep-alive started (interval: %ds)", interval_seconds)


def stop_keepalive():
    """Stop the background heartbeat thread."""
    _stop_event.set()
    if _heartbeat_thread is not None:
        _heartbeat_thread.join(timeout=5)
    logger.info("Keep-alive stopped")


def _heartbeat_loop(interval_seconds):
    """Write periodic heartbeats until stopped."""
    heartbeat_file = "/tmp/autopilot-keepalive"

    while not _stop_event.is_set():
        try:
            with open(heartbeat_file, "w") as f:
                f.write(str(time.time()))
        except OSError:
            pass
        _stop_event.wait(timeout=interval_seconds)
