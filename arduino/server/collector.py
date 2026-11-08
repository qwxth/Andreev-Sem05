"""Poll the ESP32 directly and publish the last committed reading."""

from datetime import datetime, timezone
import json
import math
import threading
import urllib.error
import urllib.request


def _nonnegative_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite non-negative number")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} is too large") from error
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return number


def validate_reading(data):
    if not isinstance(data, dict):
        raise ValueError("ESP32 response must be a JSON object")
    if data.get("storage_ok") is not True:
        raise ValueError("ESP32 storage is unavailable; reading was not saved")
    if data.get("ready") is not True:
        raise ValueError("ESP32 is not ready; reading was not saved")
    liters = _nonnegative_number(data.get("liters"), "liters")
    if not isinstance(data.get("contact_closed"), bool):
        raise ValueError("ESP32 contact_closed must be a boolean")
    device_id = data.get("device_id")
    if not isinstance(device_id, str) or not device_id.strip():
        raise ValueError("ESP32 device_id is missing")
    uptime = _nonnegative_number(data.get("uptime_ms"), "uptime_ms")
    return {
        "liters": liters,
        "device_id": device_id,
        "contact_closed": data["contact_closed"],
        "uptime_ms": uptime,
    }


class WaterCollector:
    def __init__(
        self,
        store_reading,
        *,
        url="http://192.168.4.1/reading",
        interval_seconds=5,
        timeout_seconds=3,
        enabled=True,
        opener=None,
    ):
        interval_seconds = _nonnegative_number(interval_seconds, "poll interval")
        timeout_seconds = _nonnegative_number(timeout_seconds, "HTTP timeout")
        if interval_seconds == 0 or timeout_seconds == 0:
            raise ValueError("Poll interval and HTTP timeout must be positive")
        self.store_reading = store_reading
        self.url = url
        self.interval_seconds = interval_seconds
        self.timeout_seconds = timeout_seconds
        self.enabled = bool(enabled)
        # The ESP32 is on a local network without an Internet proxy.
        self.opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({})
        )
        self._lock = threading.Lock()
        self._poll_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._status = {
            "enabled": self.enabled,
            "connected": False,
            "stale": True,
            "last_attempt": None,
            "last_success": None,
            "last_error": None,
            "liters": None,
            "device_id": None,
            "contact_closed": None,
            "uptime_ms": None,
        }

    def get_status(self):
        with self._lock:
            return self._status.copy()

    def poll_once(self):
        if not self.enabled:
            return False
        # Keep HTTP and persistence in order even if a caller requests a poll
        # while the background worker is already polling.
        with self._poll_lock:
            with self._lock:
                self._status["last_attempt"] = datetime.now(timezone.utc).isoformat()
            try:
                request = urllib.request.Request(
                    self.url, headers={"Accept": "application/json"}, method="GET"
                )
                with self.opener.open(request, timeout=self.timeout_seconds) as response:
                    if response.status != 200:
                        raise ValueError(f"ESP32 returned HTTP {response.status}")
                    body = response.read(16385)
                    if len(body) > 16384:
                        raise ValueError("ESP32 response is too large")
                    reading = validate_reading(json.loads(body))

                # The callback returns only after its transaction commits.
                # Do not publish the candidate reading before that boundary.
                self.store_reading(reading["liters"], allow_decrease=False)
                with self._lock:
                    self._status.update(reading)
                    self._status.update(
                        connected=True,
                        stale=False,
                        last_success=datetime.now(timezone.utc).isoformat(),
                        last_error=None,
                    )
                return True
            except Exception as error:
                message = str(error) or type(error).__name__
                if isinstance(error, urllib.error.HTTPError):
                    try:
                        error.close()
                    except Exception:
                        pass
                with self._lock:
                    self._status.update(
                        connected=False, stale=True, last_error=message
                    )
                print(f"⚠️ Коллектор: {message}", flush=True)
                return False

    def _run(self):
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.interval_seconds)

    def start(self):
        with self._lock:
            if not self.enabled or (self._thread and self._thread.is_alive()):
                return False
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="water-collector", daemon=True
            )
            self._thread.start()
            return True

    def stop(self):
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=self.timeout_seconds + 1)
