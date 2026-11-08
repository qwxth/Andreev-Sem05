import importlib.util
import io
from contextlib import redirect_stdout
from datetime import datetime
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, call, patch


spec = importlib.util.spec_from_file_location(
    "water_server_under_test", Path(__file__).with_name("server.py")
)
water_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(water_server)


class WaterServerTests(unittest.TestCase):
    def setUp(self):
        self.client = water_server.app.test_client()
        self.output = io.StringIO()
        output_redirect = redirect_stdout(self.output)
        output_redirect.__enter__()
        self.addCleanup(output_redirect.__exit__, None, None, None)

    def make_db(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.__enter__.return_value = cursor
        cursor.fetchone.return_value = None
        return connection, cursor

    def assert_closed(self, connection, cursor):
        cursor.__exit__.assert_called_once()
        connection.close.assert_called_once()

    def test_liters_only_is_saved_and_logged_once(self):
        connection, cursor = self.make_db()
        with patch.object(water_server, "get_db", return_value=connection):
            response = self.client.post("/water", json={"liters": 1090.0})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "ok", "liters": 1090.0})
        cursor.execute.assert_called_with(
            "INSERT INTO water_log (liters) VALUES (%s)", (1090.0,)
        )
        self.assertEqual(
            cursor.execute.call_args_list[:2],
            [
                call("SELECT pg_advisory_xact_lock(%s)", (water_server.READING_LOCK_KEY,)),
                call("SELECT liters FROM water_log ORDER BY id DESC LIMIT 1"),
            ],
        )
        connection.commit.assert_called_once()
        self.assert_closed(connection, cursor)
        lines = self.output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("1090.0", lines[0])
        self.assertNotIn("RAW", lines[0])
        self.assertNotIn("pulses", lines[0])

    def test_legacy_pulses_field_is_ignored(self):
        connection, cursor = self.make_db()
        with patch.object(water_server, "get_db", return_value=connection):
            response = self.client.post(
                "/water", json={"liters": 1100, "pulses": {"ignored": True}}
            )

        self.assertEqual(response.status_code, 200)
        cursor.execute.assert_called_with(
            "INSERT INTO water_log (liters) VALUES (%s)", (1100.0,)
        )
        connection.commit.assert_called_once()
        self.assert_closed(connection, cursor)

    def test_zero_liters_is_valid(self):
        connection, cursor = self.make_db()
        with patch.object(water_server, "get_db", return_value=connection):
            response = self.client.post("/water", json={"liters": 0})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["liters"], 0.0)
        connection.commit.assert_called_once()
        self.assert_closed(connection, cursor)

    def test_invalid_requests_never_access_database(self):
        invalid_json_values = [
            {},
            {"pulses": 1},
            [],
            None,
            "not an object",
            {"liters": None},
            {"liters": True},
            {"liters": "10"},
            {"liters": []},
            {"liters": {}},
            {"liters": -1},
            {"liters": float("nan")},
            {"liters": float("inf")},
            {"liters": -float("inf")},
            {"liters": 10 ** 1000},
        ]
        with patch.object(water_server, "get_db") as get_db:
            for data in invalid_json_values:
                with self.subTest(data=data):
                    response = self.client.post("/water", json=data)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.json["status"], "error")
            for kwargs in [
                {"data": "{bad", "content_type": "application/json"},
                {"data": '{"liters": 10}'},
                {},
            ]:
                with self.subTest(request=kwargs):
                    response = self.client.post("/water", **kwargs)
                    self.assertEqual(response.status_code, 400)
            get_db.assert_not_called()

    def test_sql_error_returns_500_and_closes_resources(self):
        connection, cursor = self.make_db()
        cursor.execute.side_effect = RuntimeError("mock SQL failure")
        with patch.object(water_server, "get_db", return_value=connection):
            response = self.client.post("/water", json={"liters": 1100})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json["status"], "error")
        connection.commit.assert_not_called()
        connection.rollback.assert_called_once()
        self.assert_closed(connection, cursor)

    def test_commit_error_returns_500_and_closes_resources(self):
        connection, cursor = self.make_db()
        connection.commit.side_effect = RuntimeError("mock commit failure")
        with patch.object(water_server, "get_db", return_value=connection):
            response = self.client.post("/water", json={"liters": 1100})

        self.assertEqual(response.status_code, 500)
        connection.commit.assert_called_once()
        connection.rollback.assert_called_once()
        self.assert_closed(connection, cursor)

    def test_same_reading_after_restart_does_not_insert_again(self):
        connection, cursor = self.make_db()
        cursor.fetchone.return_value = (1170.0,)
        with patch.object(water_server, "get_db", return_value=connection):
            changed = water_server.store_reading(1170.0, allow_decrease=False)

        self.assertFalse(changed)
        self.assertEqual(cursor.execute.call_count, 2)
        connection.commit.assert_called_once()
        self.assert_closed(connection, cursor)
        self.assertEqual(self.output.getvalue(), "")

    def test_same_direct_post_does_not_insert_again(self):
        connection, cursor = self.make_db()
        cursor.fetchone.return_value = (1170.0,)
        with patch.object(water_server, "get_db", return_value=connection):
            response = self.client.post("/water", json={"liters": 1170.0})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(cursor.execute.call_count, 2)
        connection.commit.assert_called_once()
        self.assert_closed(connection, cursor)

    def test_automatic_rollback_is_rejected_without_insert(self):
        connection, cursor = self.make_db()
        cursor.fetchone.return_value = (1170.0,)
        with patch.object(water_server, "get_db", return_value=connection):
            with self.assertRaises(water_server.ReadingRollbackError):
                water_server.store_reading(1090.0, allow_decrease=False)

        self.assertEqual(cursor.execute.call_count, 2)
        connection.commit.assert_not_called()
        connection.rollback.assert_called_once()
        self.assert_closed(connection, cursor)

    def test_direct_post_can_correct_bad_historical_value(self):
        connection, cursor = self.make_db()
        cursor.fetchone.return_value = (1100.0,)
        with patch.object(water_server, "get_db", return_value=connection):
            response = self.client.post("/water", json={"liters": 1090.0})

        self.assertEqual(response.status_code, 200)
        cursor.execute.assert_called_with(
            "INSERT INTO water_log (liters) VALUES (%s)", (1090.0,)
        )
        connection.commit.assert_called_once()
        self.assert_closed(connection, cursor)

    def test_failed_commit_is_retried_against_database(self):
        first_connection, first_cursor = self.make_db()
        first_cursor.fetchone.return_value = (1170.0,)
        first_connection.commit.side_effect = RuntimeError("mock commit failure")
        second_connection, second_cursor = self.make_db()
        second_cursor.fetchone.return_value = (1170.0,)
        with patch.object(
            water_server, "get_db", side_effect=[first_connection, second_connection]
        ):
            with self.assertRaises(RuntimeError):
                water_server.store_reading(1180.0, allow_decrease=False)
            self.assertTrue(water_server.store_reading(1180.0, allow_decrease=False))

        first_connection.rollback.assert_called_once()
        second_cursor.execute.assert_called_with(
            "INSERT INTO water_log (liters) VALUES (%s)", (1180.0,)
        )
        second_connection.commit.assert_called_once()
        self.assert_closed(first_connection, first_cursor)
        self.assert_closed(second_connection, second_cursor)

    def test_connection_error_returns_500(self):
        with patch.object(
            water_server, "get_db", side_effect=RuntimeError("mock connection failure")
        ):
            response = self.client.post("/water", json={"liters": 1100})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json["status"], "error")

    def test_new_table_has_no_pulses_column(self):
        connection, cursor = self.make_db()
        with patch.object(water_server, "get_db", return_value=connection):
            water_server.init_db()

        sql = cursor.execute.call_args.args[0]
        self.assertIn("CREATE TABLE IF NOT EXISTS water_log", sql)
        self.assertIn("liters", sql)
        self.assertIn("created_at", sql)
        self.assertNotIn("pulses", sql)
        connection.commit.assert_called_once()
        self.assert_closed(connection, cursor)

    def test_stats_still_returns_maximum_cumulative_value(self):
        connection, cursor = self.make_db()
        cursor.fetchall.return_value = [(1100.0, datetime(2026, 11, 7, 12, 0))]
        cursor.fetchone.return_value = (1110.0,)
        with patch.object(water_server, "get_db", return_value=connection):
            response = self.client.get("/water/stats")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json,
            {
                "today_liters": 1110.0,
                "last_records": [{"liters": 1100.0, "time": "2026-11-07 12:00:00"}],
            },
        )
        self.assertIn("MAX(liters)", cursor.execute.call_args.args[0])
        connection.commit.assert_not_called()
        self.assert_closed(connection, cursor)

    def test_ping_does_not_access_database(self):
        with patch.object(water_server, "get_db") as get_db:
            response = self.client.get("/ping")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "alive"})
        get_db.assert_not_called()

    def test_status_exposes_collector_without_database_access(self):
        expected = water_server.collector.get_status()
        with patch.object(water_server, "get_db") as get_db:
            response = self.client.get("/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, expected)
        self.assertIsNone(water_server.collector._thread)
        get_db.assert_not_called()

    def test_root_renders_dashboard_template(self):
        with patch.object(water_server, "render_template", return_value="dashboard") as render:
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "dashboard")
        render.assert_called_once_with("dashboard.html")

    def test_environment_settings_do_not_start_collector_at_import(self):
        env = {
            "DB_HOST": "postgres",
            "DB_PORT": "5432",
            "DB_NAME": "example_db",
            "DB_USER": "example_user",
            "DB_PASSWORD": "example_password",
            "ESP_READING_URL": "http://192.168.4.1/reading",
            "POLL_INTERVAL_SECONDS": "7",
            "POLL_TIMEOUT_SECONDS": "2",
            "COLLECTOR_ENABLED": "false",
        }
        with patch.dict(os.environ, env):
            reloaded = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(reloaded)

        self.assertEqual(reloaded.DB_CONFIG, {
            "host": "postgres", "port": 5432, "database": "example_db",
            "user": "example_user", "password": "example_password",
        })
        self.assertFalse(reloaded.collector.enabled)
        self.assertEqual(reloaded.collector.interval_seconds, 7)
        self.assertEqual(reloaded.collector.timeout_seconds, 2)
        self.assertIsNone(reloaded.collector._thread)


if __name__ == "__main__":
    unittest.main()
