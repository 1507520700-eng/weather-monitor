# -*- coding: utf-8 -*-
"""
历史数据存储模块 (SQLite)
记录每10分钟的天气快照, 支持趋势曲线回看
"""
import os
import sqlite3
import threading
from datetime import datetime, timedelta

_DB_PATH = os.path.join(os.path.expanduser("~"), ".weather_monitor", "history.db")
os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)

_lock = threading.Lock()


def _get_conn():
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS weather_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        city TEXT,
        code TEXT,
        ts DATETIME,
        temp REAL,
        weather TEXT,
        wind_dir TEXT,
        wind_level TEXT,
        wind_speed REAL,
        humidity REAL,
        pressure REAL,
        rain REAL,
        rain24h REAL,
        aqi REAL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_city_ts ON weather_snapshots(city, ts)")
    return conn


def save_snapshot(city, code, weather):
    """保存一条天气快照"""
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                "INSERT INTO weather_snapshots (city, code, ts, temp, weather, wind_dir, "
                "wind_level, wind_speed, humidity, pressure, rain, rain24h, aqi) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (city, code, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 weather.get("temp"), weather.get("weather"),
                 weather.get("wind_dir"), weather.get("wind_level"),
                 weather.get("wind_speed"), weather.get("humidity"),
                 weather.get("pressure"), weather.get("rain"),
                 weather.get("rain24h"), weather.get("aqi")))
            conn.commit()
        finally:
            conn.close()


def get_history(city, hours=24):
    """
    获取某城市最近 hours 小时的历史数据
    返回: [{ts,temp,rain,wind_speed,humidity,weather,...}]
    """
    with _lock:
        conn = _get_conn()
        try:
            since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
            rows = conn.execute(
                "SELECT ts, temp, weather, wind_dir, wind_level, wind_speed, "
                "humidity, pressure, rain, rain24h, aqi "
                "FROM weather_snapshots WHERE city=? AND ts>=? ORDER BY ts",
                (city, since)).fetchall()
        finally:
            conn.close()
    cols = ["ts", "temp", "weather", "wind_dir", "wind_level", "wind_speed",
            "humidity", "pressure", "rain", "rain24h", "aqi"]
    return [dict(zip(cols, r)) for r in rows]
