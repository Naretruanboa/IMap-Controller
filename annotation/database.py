import json
import sqlite3
from pathlib import Path


class AnnotationDatabase:
    """SQLite database for image annotation metadata and draft bounding boxes."""

    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS images (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                filename    TEXT    NOT NULL UNIQUE,
                width       INTEGER,
                height      INTEGER,
                status      TEXT    NOT NULL DEFAULT 'pending',
                annotation_count INTEGER DEFAULT 0,
                image_hash  TEXT,
                session_group TEXT,
                created_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                updated_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS annotations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                image_id    INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
                bboxes_json TEXT    NOT NULL DEFAULT '[]',
                created_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                updated_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                UNIQUE(image_id)
            );
            CREATE INDEX IF NOT EXISTS idx_images_status   ON images(status);
            CREATE INDEX IF NOT EXISTS idx_images_filename ON images(filename);
        """)

    # ── Image CRUD ────────────────────────────────────────────────

    def upsert_image(self, filename: str, width: int | None = None,
                     height: int | None = None, status: str = "pending") -> int:
        with self.conn:
            self.conn.execute(
                """INSERT INTO images(filename, width, height, status)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(filename) DO UPDATE SET
                       width  = COALESCE(excluded.width,  images.width),
                       height = COALESCE(excluded.height, images.height),
                       updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
                (filename, width, height, status),
            )
        row = self.conn.execute("SELECT id FROM images WHERE filename=?", (filename,)).fetchone()
        return row["id"]

    def get_image(self, image_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
        return dict(row) if row else None

    def get_image_by_filename(self, filename: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM images WHERE filename=?", (filename,)).fetchone()
        return dict(row) if row else None

    def delete_image(self, image_id: int) -> bool:
        with self.conn:
            self.conn.execute("DELETE FROM annotations WHERE image_id=?", (image_id,))
            cur = self.conn.execute("DELETE FROM images WHERE id=?", (image_id,))
            return cur.rowcount > 0

    def delete_images(self, image_ids: list[int]) -> int:
        if not image_ids:
            return 0
        placeholders = ",".join("?" for _ in image_ids)
        with self.conn:
            self.conn.execute(f"DELETE FROM annotations WHERE image_id IN ({placeholders})", image_ids)
            cur = self.conn.execute(f"DELETE FROM images WHERE id IN ({placeholders})", image_ids)
            return cur.rowcount

    # ── Paginated image listing with filters ──────────────────────

    def get_images(self, status: str | None = None, has_class: int | None = None,
                   page: int = 1, per_page: int = 50, search: str | None = None) -> dict:
        conds: list[str] = []
        params: list = []

        if status and status != "all":
            conds.append("i.status = ?")
            params.append(status)
        if search:
            conds.append("i.filename LIKE ?")
            params.append(f"%{search}%")

        join = ""
        if has_class is not None:
            join = "JOIN annotations a ON a.image_id = i.id"
            conds.append("a.bboxes_json LIKE ?")
            params.append(f'%"class_id": {has_class},%')

        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        distinct = "DISTINCT " if has_class is not None else ""

        total = self.conn.execute(
            f"SELECT COUNT({distinct}i.id) FROM images i {join} {where}", params
        ).fetchone()[0]

        rows = self.conn.execute(
            f"SELECT {distinct}i.* FROM images i {join} {where} "
            f"ORDER BY i.filename LIMIT ? OFFSET ?",
            params + [per_page, (page - 1) * per_page],
        ).fetchall()

        return {
            "images": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": max(1, (total + per_page - 1) // per_page),
        }

    # ── Annotations ───────────────────────────────────────────────

    def get_annotations(self, image_id: int) -> list:
        row = self.conn.execute(
            "SELECT bboxes_json FROM annotations WHERE image_id=?", (image_id,)
        ).fetchone()
        return json.loads(row["bboxes_json"]) if row else []

    def save_annotations(self, image_id: int, bboxes: list) -> None:
        bboxes_json = json.dumps(bboxes)
        count = len(bboxes)
        with self.conn:
            self.conn.execute(
                """INSERT INTO annotations(image_id, bboxes_json)
                   VALUES (?, ?)
                   ON CONFLICT(image_id) DO UPDATE SET
                       bboxes_json = excluded.bboxes_json,
                       updated_at  = strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
                (image_id, bboxes_json),
            )
            new_status = "draft" if count > 0 else "pending"
            self.conn.execute(
                """UPDATE images SET annotation_count = ?,
                       status     = CASE WHEN status IN ('pending','draft') THEN ? ELSE status END,
                       updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                   WHERE id = ?""",
                (count, new_status, image_id),
            )

    # ── Status updates ────────────────────────────────────────────

    def update_status(self, image_id: int, status: str) -> None:
        with self.conn:
            if status in ("completed", "no_object"):
                self.conn.execute(
                    """UPDATE images SET status = ?, annotation_count = (
                           SELECT COALESCE(
                               json_array_length(a.bboxes_json), 0
                           ) FROM annotations a WHERE a.image_id = images.id
                       ),
                       completed_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                       updated_at   = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                       WHERE id = ?""",
                    (status, image_id),
                )
            else:
                self.conn.execute(
                    """UPDATE images SET status = ?, completed_at = NULL,
                       updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                       WHERE id = ?""",
                    (status, image_id),
                )

    # ── Dashboard stats ───────────────────────────────────────────

    def get_stats(self) -> dict:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM images GROUP BY status"
        ).fetchall()
        s = {r["status"]: r["cnt"] for r in rows}
        total = sum(s.values())
        completed = s.get("completed", 0)
        no_obj = s.get("no_object", 0)
        done = completed + no_obj + s.get("skipped", 0)
        return {
            "total": total,
            "completed": completed,
            "no_object": no_obj,
            "pending": s.get("pending", 0),
            "draft": s.get("draft", 0),
            "skipped": s.get("skipped", 0),
            "progress": round(done / total * 100, 2) if total else 0,
        }

    # ── Navigation helpers ────────────────────────────────────────

    def get_adjacent_ids(self, current_id: int, status: str | None = None) -> dict:
        cond = " AND status = ?" if status and status != "all" else ""
        args = [current_id] + ([status] if cond else [])
        prev_row = self.conn.execute(
            f"SELECT id FROM images WHERE id < ? {cond} ORDER BY id DESC LIMIT 1", args
        ).fetchone()
        next_row = self.conn.execute(
            f"SELECT id FROM images WHERE id > ? {cond} ORDER BY id ASC LIMIT 1", args
        ).fetchone()
        return {
            "prev_id": prev_row["id"] if prev_row else None,
            "next_id": next_row["id"] if next_row else None,
        }

    def close(self) -> None:
        self.conn.close()
