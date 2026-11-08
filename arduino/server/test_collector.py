import importlib.util
import io
import json
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError


spec = importlib.util.spec_from_file_location(
    "water_collector_under_test", Path(__file__).with_name("collector.py")
)
collector_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = collector_module
spec.loader.exec_module(collector_module)
WaterCollector = collector_module.WaterCollector


class FakeResponse:
    def __init__(self, data, *, status=200, raw=False):
        self.status = status
        self.body = io.BytesIO(data if raw else json.dumps(data).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.body.close()

    def read(self, size=-1):
        return self.body.read(size)


class FakeOpener:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append((request, timeout))
        if not self.results:
            raise AssertionError("Unexpected extra HTTP request")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.network_guard = patch(
            "socket.create_connection",
            side_effect=AssertionError("Real network is forbidden in collector tests"),
        )
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)
        output_redirect = redirect_stdout(io.StringIO())
        output_redirect.__enter__()
        self.addCleanup(output_redirect.__exit__, None, None, None)

    def reading(self, **changes):
        data = {
            "liters": 1170.0,
            "ready": True,
            "storage_ok": True,
            "contact_closed": False,
            "device_id": "esp32-test",
            "uptime_ms": 1234,
        }
        data.update(changes)
        return data

    def make_collector(self, *results, store=None, **kwargs):
        store = store if store is not None else Mock(return_value=True)
        opener = FakeOpener(*results)
        return WaterCollector(store, opener=opener, **kwargs), store, opener

    def assert_failed(self, collector):
        state = collector.get_status()
        self.assertFalse(state["connected"])
        self.assertTrue(state["stale"])
        self.assertIsNotNone(state["last_attempt"])
        self.assertTrue(state["last_error"])
        return state

    def test_initial_status_is_unknown_and_snapshot_is_independent(self):
        collector, store, opener = self.make_collector()
        state = collector.get_status()
        self.assertEqual(
            set(state),
            {
                "enabled", "connected", "stale", "last_attempt", "last_success",
                "last_error", "liters", "device_id", "contact_closed", "uptime_ms",
            },
        )
        self.assertTrue(state["enabled"])
        self.assertFalse(state["connected"])
        self.assertTrue(state["stale"])
        for key in (
            "last_attempt", "last_success", "last_error", "liters",
            "device_id", "contact_closed", "uptime_ms",
        ):
            self.assertIsNone(state[key])
        state["liters"] = 0
        self.assertIsNone(collector.get_status()["liters"])
        store.assert_not_called()
        self.assertEqual(opener.calls, [])

    def test_success_is_published_only_after_store_returns(self):
        collector, store, opener = self.make_collector(
            FakeResponse(self.reading()), timeout_seconds=2.5
        )

        def check_before_commit(liters, *, allow_decrease):
            self.assertEqual(liters, 1170.0)
            self.assertFalse(allow_decrease)
            state = collector.get_status()
            self.assertFalse(state["connected"])
            self.assertTrue(state["stale"])
            self.assertIsNone(state["liters"])
            self.assertIsNone(state["last_success"])
            return True

        store.side_effect = check_before_commit
        self.assertTrue(collector.poll_once())
        store.assert_called_once_with(1170.0, allow_decrease=False)
        state = collector.get_status()
        self.assertTrue(state["connected"])
        self.assertFalse(state["stale"])
        self.assertIsNone(state["last_error"])
        self.assertEqual(state["liters"], 1170.0)
        self.assertEqual(state["device_id"], "esp32-test")
        self.assertIs(state["contact_closed"], False)
        self.assertEqual(state["uptime_ms"], 1234)
        for key in ("last_attempt", "last_success"):
            stamp = datetime.fromisoformat(state[key].replace("Z", "+00:00"))
            self.assertEqual(stamp.utcoffset(), timezone.utc.utcoffset(stamp))
        request, timeout = opener.calls[0]
        self.assertEqual(getattr(request, "full_url", request), "http://192.168.4.1/reading")
        self.assertEqual(timeout, 2.5)

    def test_unchanged_committed_reading_is_still_a_success(self):
        collector, store, _ = self.make_collector(
            FakeResponse(self.reading()), store=Mock(return_value=False)
        )
        self.assertTrue(collector.poll_once())
        self.assertTrue(collector.get_status()["connected"])
        self.assertFalse(collector.get_status()["stale"])
        store.assert_called_once_with(1170.0, allow_decrease=False)

    def test_http_failure_marks_previous_reading_stale_and_can_recover(self):
        collector, store, _ = self.make_collector(
            FakeResponse(self.reading()),
            URLError("ESP unavailable"),
            FakeResponse(self.reading(liters=1180, contact_closed=True, uptime_ms=2000)),
        )
        self.assertTrue(collector.poll_once())
        good = collector.get_status()
        self.assertFalse(collector.poll_once())
        failed = self.assert_failed(collector)
        for key in ("liters", "device_id", "contact_closed", "uptime_ms", "last_success"):
            self.assertEqual(failed[key], good[key])
        self.assertEqual(store.call_count, 1)
        self.assertTrue(collector.poll_once())
        recovered = collector.get_status()
        self.assertTrue(recovered["connected"])
        self.assertFalse(recovered["stale"])
        self.assertIsNone(recovered["last_error"])
        self.assertEqual(recovered["liters"], 1180.0)
        self.assertIs(recovered["contact_closed"], True)
        self.assertEqual(store.call_count, 2)

    def test_db_failure_does_not_accept_new_reading_or_invent_zero(self):
        store = Mock(side_effect=[True, RuntimeError("mock DB failure"), False])
        collector, _, _ = self.make_collector(
            FakeResponse(self.reading()),
            FakeResponse(self.reading(liters=1180)),
            FakeResponse(self.reading(liters=1180)),
            store=store,
        )
        self.assertTrue(collector.poll_once())
        good = collector.get_status()
        self.assertFalse(collector.poll_once())
        failed = self.assert_failed(collector)
        self.assertEqual(failed["liters"], 1170.0)
        self.assertEqual(failed["last_success"], good["last_success"])
        self.assertIn("mock DB failure", failed["last_error"])
        self.assertTrue(collector.poll_once())
        self.assertEqual(collector.get_status()["liters"], 1180.0)
        self.assertEqual(store.call_count, 3)

    def test_first_db_failure_leaves_reading_unknown(self):
        collector, _, _ = self.make_collector(
            FakeResponse(self.reading()), store=Mock(side_effect=RuntimeError("DB down"))
        )
        self.assertFalse(collector.poll_once())
        state = self.assert_failed(collector)
        self.assertIsNone(state["liters"])
        self.assertIsNone(state["last_success"])

    def test_invalid_device_data_never_reaches_store(self):
        bad_payloads = [
            None, [], "not an object",
            self.reading(ready=False), self.reading(ready=1),
            self.reading(storage_ok=False), self.reading(storage_ok="true"),
            self.reading(liters=-1), self.reading(liters=True),
            self.reading(liters="1170"), self.reading(liters=None),
            self.reading(liters=float("nan")), self.reading(liters=float("inf")),
            self.reading(liters=-float("inf")), self.reading(liters=10 ** 1000),
            self.reading(contact_closed=0), self.reading(contact_closed="false"),
            self.reading(device_id=None), self.reading(device_id=123),
            self.reading(device_id=""), self.reading(device_id="   "),
            self.reading(uptime_ms=False), self.reading(uptime_ms=-1),
            self.reading(uptime_ms="1000"), self.reading(uptime_ms=float("nan")),
            self.reading(uptime_ms=float("inf")),
        ]
        for key in self.reading():
            missing = self.reading()
            del missing[key]
            bad_payloads.append(missing)
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                collector, store, _ = self.make_collector(FakeResponse(payload))
                self.assertFalse(collector.poll_once())
                state = self.assert_failed(collector)
                self.assertIsNone(state["liters"])
                self.assertIsNone(state["last_success"])
                store.assert_not_called()

    def test_malformed_json_and_http_errors_do_not_store(self):
        failures = [
            FakeResponse(b"{bad", raw=True),
            FakeResponse(b"\xff", raw=True),
            FakeResponse(self.reading(), status=503),
            HTTPError("http://192.168.4.1/reading", 503, "Not ready", None, None),
            TimeoutError("ESP timeout"),
        ]
        for failure in failures:
            with self.subTest(failure=failure):
                collector, store, _ = self.make_collector(failure)
                self.assertFalse(collector.poll_once())
                self.assert_failed(collector)
                store.assert_not_called()

    def test_disabled_poll_does_not_touch_http_or_database(self):
        collector, store, opener = self.make_collector(enabled=False)
        self.assertFalse(collector.poll_once())
        state = collector.get_status()
        self.assertFalse(state["enabled"])
        self.assertFalse(state["connected"])
        self.assertTrue(state["stale"])
        self.assertIsNone(state["last_attempt"])
        self.assertIsNone(state["liters"])
        store.assert_not_called()
        self.assertEqual(opener.calls, [])


if __name__ == "__main__":
    unittest.main()
