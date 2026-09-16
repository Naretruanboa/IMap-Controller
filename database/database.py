import sqlite3
from pathlib import Path

from models.schemas import Coordinates, Favorite


class Database:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, latitude REAL NOT NULL,
                longitude REAL NOT NULL, created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
            CREATE TABLE IF NOT EXISTS location_history (
                id INTEGER PRIMARY KEY, latitude REAL NOT NULL, longitude REAL NOT NULL,
                label TEXT NOT NULL, timestamp TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL,
                updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
        """)

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.connection.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO app_settings(key, value, updated_at) VALUES(?, ?, (strftime('%Y-%m-%dT%H:%M:%fZ','now'))) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value),
            )

    def get_last_location(self) -> dict | None:
        val = self.get_setting("last_location")
        if val:
            try:
                import json
                return json.loads(val)
            except Exception:
                pass
        row = self.connection.execute("SELECT latitude, longitude FROM location_history ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            return {"latitude": row["latitude"], "longitude": row["longitude"], "bearing": 0.0}
        return None

    def save_last_location(self, point: Coordinates, bearing: float = 0.0) -> None:
        import json
        self.set_setting("last_location", json.dumps({
            "latitude": point.latitude,
            "longitude": point.longitude,
            "bearing": bearing
        }))

    def favorites(self) -> list[dict]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM favorites ORDER BY id DESC")]

    def add_favorite(self, point: Favorite) -> int:
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO favorites(name,latitude,longitude) VALUES (?,?,?)",
                (point.name, point.latitude, point.longitude),
            )
        return cursor.lastrowid

    def delete_favorite(self, identifier: int) -> bool:
        with self.connection:
            return self.connection.execute("DELETE FROM favorites WHERE id=?", (identifier,)).rowcount > 0

    def record(self, point: Coordinates, label: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO location_history(latitude,longitude,label) VALUES (?,?,?)",
                (point.latitude, point.longitude, label),
            )
        self.save_last_location(point)

    def history(self) -> list[dict]:
        return [
            dict(row)
            for row in self.connection.execute("SELECT * FROM location_history ORDER BY id DESC LIMIT 500")
        ]

    def close(self) -> None:
        self.connection.close()
