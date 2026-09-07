from flask import Flask, request, jsonify
import psycopg2
from datetime import datetime
import time

app = Flask(__name__)

DB_CONFIG = {
    "host": "localhost",
    "port": 5433,
    "database": "water_db",
    "user": "postgres",
    "password": "water123"
}

def get_db():
    retry = 0
    while retry < 5:
        try:
            return psycopg2.connect(**DB_CONFIG)
        except Exception as e:
            print(f"Попытка подключения {retry+1}/5...")
            retry += 1
            time.sleep(2)
    raise Exception("Не удалось подключиться к PostgreSQL")

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS water_log (
            id         SERIAL PRIMARY KEY,
            pulses     INTEGER NOT NULL,
            liters     FLOAT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("✅ База данных готова!")

@app.route('/water', methods=['POST'])
def receive_data():
    try:
        # ЛОГИРУЕМ RAW данные
        raw_data = request.get_data()
        print(f"RAW: {raw_data}")
        
        data = request.get_json()
        
        # ПРОВЕРКА типов данных
        if not isinstance(data, dict):
            print(f"❌ Не словарь: {type(data)}")
            return jsonify({"status": "error", "message": "Invalid data type"}), 400
            
        pulses = int(data['pulses'])
        liters = float(data['liters'])
        
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO water_log (pulses, liters) VALUES (%s, %s)",
            (pulses, liters)
        )
        conn.commit()
        cur.close()
        conn.close()
        
        timestamp = datetime.now().strftime('%H:%M:%S')
        print(f"✅ [{timestamp}] Записано: {liters}л (импульсов: {pulses})")
        return jsonify({"status": "ok", "liters": liters}), 200
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/water/stats', methods=['GET'])
def get_stats():
    try:
        conn = get_db()
        cur = conn.cursor()
        
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
        cur.close()
        conn.close()
        
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
    print("🌐 Сервер запущен на http://192.168.1.11:5000")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=True)
