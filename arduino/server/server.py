from flask import Flask, request, jsonify, render_template
import psycopg2
from contextlib import closing
from datetime import datetime
import math
import os
import time

from collector import WaterCollector

app = Flask(__name__)

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5433")),
    "database": os.getenv("DB_NAME", "water_db"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "water123")
}

# All writers use one transaction-scoped lock before checking the latest row.
READING_LOCK_KEY = 1463899205


class ReadingRollbackError(ValueError):
    pass

def get_db():
    retry = 0
    while retry < 5:
        try:
            return psycopg2.connect(**DB_CONFIG, connect_timeout=3)
        except Exception as e:
            reason = str(e).replace(DB_CONFIG["password"], "***")
            print(f"Попытка подключения {retry+1}/5: {reason}")
            retry += 1
            time.sleep(2)
    raise RuntimeError("Не удалось подключиться к PostgreSQL") from None

def init_db():
    with closing(get_db()) as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS water_log (
                id         SERIAL PRIMARY KEY,
                liters     FLOAT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()
    print("✅ База данных готова!")


def store_reading(liters, *, allow_decrease=True):
    """Compare with the latest row and commit at most one changed reading."""
    with closing(get_db()) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (READING_LOCK_KEY,))
                cur.execute("SELECT liters FROM water_log ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                previous = float(row[0]) if row is not None else None
                if not allow_decrease and previous is not None and liters < previous:
                    raise ReadingRollbackError(
                        f"ESP32 reading {liters:g} L is below the latest saved "
                        f"reading {previous:g} L; check the device counter"
                    )
                changed = previous is None or liters != previous
                if changed:
                    cur.execute(
                        "INSERT INTO water_log (liters) VALUES (%s)", (liters,)
                    )
                conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
    if changed:
        timestamp = datetime.now().strftime('%H:%M:%S')
        print(f"✅ [{timestamp}] Записано: {liters} л", flush=True)
    return changed


collector = WaterCollector(
    store_reading,
    url=os.getenv("ESP_READING_URL", "http://192.168.4.1/reading"),
    interval_seconds=float(os.getenv("POLL_INTERVAL_SECONDS", "5")),
    timeout_seconds=float(os.getenv("POLL_TIMEOUT_SECONDS", "3")),
    enabled=os.getenv("COLLECTOR_ENABLED", "true").strip().lower()
    not in {"false", "0", "no", "off"},
)


@app.route('/', methods=['GET'])
def dashboard():
    return render_template('dashboard.html')


@app.route('/status', methods=['GET'])
def collector_status():
    return jsonify(collector.get_status()), 200

@app.route('/water', methods=['POST'])
def receive_data():
    data = request.get_json(silent=True)
    try:
        if not isinstance(data, dict):
            raise ValueError
        value = data['liters']
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError
        liters = float(value)
        if not math.isfinite(liters) or liters < 0:
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        message = "liters must be a finite non-negative JSON number"
        print(f"❌ Ошибка данных: {message}")
        return jsonify({"status": "error", "message": message}), 400

    try:
        # A direct POST remains an explicit way to correct a bad historical
        # reading; automatic collection never accepts a backwards jump.
        store_reading(liters, allow_decrease=True)
        return jsonify({"status": "ok", "liters": liters}), 200
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/water/stats', methods=['GET'])
def get_stats():
    try:
        with closing(get_db()) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT liters, created_at
                FROM water_log
                ORDER BY created_at DESC
                LIMIT 10
            """)
            rows = cur.fetchall()

            cur.execute("""
                SELECT COALESCE(MAX(liters), 0)
                FROM water_log
                WHERE created_at::date = CURRENT_DATE
            """)
            today = cur.fetchone()[0]
        
        return jsonify({
            "today_liters": float(today),
            "last_records": [
                {"liters": float(r[0]), "time": str(r[1])} for r in rows
            ]
        }), 200
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({"status": "alive"}), 200

if __name__ == '__main__':
    print("🚀 Запуск сервера...")
    init_db()
    collector.start()
    print("🌐 Сервер слушает 0.0.0.0:5000 (все сетевые интерфейсы)")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
