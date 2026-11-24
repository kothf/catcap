"""Capture library: SQLite metadata plus original PNGs and annotation JSON.

Layout (LIB-02/LIB-03):
  <images_dir>/YYYY/MM/YYYY-MM-DD_HHMMSS_<id8>.png   original, never modified
  <images_dir>/YYYY/MM/YYYY-MM-DD_HHMMSS_<id8>.json  annotations (sidecar)
  $XDG_DATA_HOME/catcap/library.db                    metadata
Rendered images are never stored; they are produced on demand.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS captures (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL,
    modified REAL NOT NULL,
    mode TEXT NOT NULL DEFAULT '',
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    size INTEGER NOT NULL DEFAULT 0,
    app TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    monitor TEXT NOT NULL DEFAULT '',
    favourite INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    ocr TEXT NOT NULL DEFAULT '',
    has_annotations INTEGER NOT NULL DEFAULT 0,
    trashed REAL,
    path TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS captures_created ON captures(created);
CREATE TABLE IF NOT EXISTS tags (
    capture_id TEXT NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (capture_id, tag)
);
CREATE INDEX IF NOT EXISTS tags_tag ON tags(tag);
CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS capture_collections (
    capture_id TEXT NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    PRIMARY KEY (capture_id, collection_id)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

SCHEMA_VERSION = "1"

SORTS = {
    "date_desc": "c.created DESC",
    "date_asc": "c.created ASC",
    "name": "c.name COLLATE NOCASE ASC, c.created DESC",
    "size": "c.size DESC",
    "dims": "(c.width * c.height) DESC",
    "app": "c.app COLLATE NOCASE ASC, c.created DESC",
}


@dataclass
class Capture:
    id: str
    name: str
    created: float
    modified: float
    mode: str
    width: int
    height: int
    size: int
    app: str
    title: str
    monitor: str
    favourite: bool
    notes: str
    ocr: str
    has_annotations: bool
    trashed: float | None
    path: str
    tags: list[str] = field(default_factory=list)


@dataclass
class Query:
    view: str = "all"  # all | favourites | recent | trash | collection | tag
    collection_id: int | None = None
    tag: str | None = None
    search: str = ""
    date_from: float | None = None
    date_to: float | None = None
    app: str | None = None
    mode: str | None = None
    annotated_only: bool = False
    sort: str = "date_desc"


class Library:
    def __init__(self, images_dir: Path, db_path: Path | None = None):
        self.images_dir = Path(images_dir)
        self.db_path = db_path or (paths.data_dir() / "library.db")
        paths.ensure_dir(self.db_path.parent)
        paths.ensure_dir(self.images_dir)
        self.db = sqlite3.connect(str(self.db_path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        self.db.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema', ?)", (SCHEMA_VERSION,)
        )
        self.db.commit()
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass
        self._listeners: list = []

    # ------------------------------------------------------------------ events
    def connect(self, callback) -> None:
        """callback(kind: str, ids: list[str]) after any change."""
        self._listeners.append(callback)

    def disconnect(self, callback) -> None:
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def _emit(self, kind: str, ids=()) -> None:
        for cb in list(self._listeners):
            try:
                cb(kind, list(ids))
            except Exception as exc:  # keep the library usable if a UI hook fails
                print(f"catcap: listener error: {exc}")

    # ------------------------------------------------------------------ paths
    def original_path(self, cap: Capture | str) -> Path:
        cap = self.get(cap) if isinstance(cap, str) else cap
        return self.images_dir / cap.path

    def doc_path(self, cap: Capture | str) -> Path:
        return self.original_path(cap).with_suffix(".json")

    def thumb_path(self, capture_id: str) -> Path:
        return paths.thumbs_dir() / f"{capture_id}.png"

    # ------------------------------------------------------------------ create
    def add_capture(self, image, *, mode: str = "", app: str = "", title: str = "",
                    monitor: str = "", name: str = "", created: float | None = None) -> str:
        """Store a QImage as a new capture and return its id."""
        created = created or time.time()
        cid = uuid.uuid4().hex
        rel = self._new_relpath(created, cid)
        full = self.images_dir / rel
        paths.ensure_dir(full.parent)
        if not image.save(str(full), "PNG"):
            raise OSError(f"could not write {full}")
        os.chmod(full, 0o600)
        if not name:
            name = (app or mode or "Capture").strip() or "Capture"
            name = f"{name} {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(created))}"
        self.db.execute(
            "INSERT INTO captures(id, name, created, modified, mode, width, height, size, app,"
            " title, monitor, path) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, name, created, created, mode, image.width(), image.height(),
             full.stat().st_size, app, title, monitor, rel),
        )
        self.db.commit()
        self._emit("added", [cid])
        return cid

    def import_file(self, path: str | Path) -> str:
        path = Path(path)
        image = read_image(path)
        created = path.stat().st_mtime
        return self.add_capture(image, mode="import", name=path.stem, created=created)

    def _new_relpath(self, created: float, cid: str) -> str:
        t = time.localtime(created)
        stem = time.strftime("%Y-%m-%d_%H%M%S", t) + f"_{cid[:8]}"
        return f"{t.tm_year:04d}/{t.tm_mon:02d}/{stem}.png"

    # ------------------------------------------------------------------ read
    def _row_to_capture(self, row: sqlite3.Row, with_tags: bool = True) -> Capture:
        cap = Capture(
            id=row["id"], name=row["name"], created=row["created"], modified=row["modified"],
            mode=row["mode"], width=row["width"], height=row["height"], size=row["size"],
            app=row["app"], title=row["title"], monitor=row["monitor"],
            favourite=bool(row["favourite"]), notes=row["notes"], ocr=row["ocr"],
            has_annotations=bool(row["has_annotations"]), trashed=row["trashed"],
            path=row["path"],
        )
        if with_tags:
            cap.tags = self.get_tags(cap.id)
        return cap

    def get(self, capture_id: str) -> Capture:
        row = self.db.execute("SELECT * FROM captures WHERE id=?", (capture_id,)).fetchone()
        if row is None:
            raise KeyError(capture_id)
        return self._row_to_capture(row)

    def exists(self, capture_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM captures WHERE id=?", (capture_id,)).fetchone() is not None

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM captures WHERE trashed IS NULL").fetchone()[0]

    def query(self, q: Query) -> list[Capture]:
        where, args = [], []
        joins = ""
        if q.view == "trash":
            where.append("c.trashed IS NOT NULL")
        else:
            where.append("c.trashed IS NULL")
        if q.view == "favourites":
            where.append("c.favourite = 1")
        elif q.view == "recent":
            where.append("c.created >= ?")
            args.append(time.time() - 7 * 86400)
        elif q.view == "collection" and q.collection_id is not None:
            joins += " JOIN capture_collections cc ON cc.capture_id = c.id"
            where.append("cc.collection_id = ?")
            args.append(q.collection_id)
        elif q.view == "tag" and q.tag:
            where.append("EXISTS (SELECT 1 FROM tags t WHERE t.capture_id = c.id AND t.tag = ?)")
            args.append(q.tag)
        if q.date_from is not None:
            where.append("c.created >= ?")
            args.append(q.date_from)
        if q.date_to is not None:
            where.append("c.created < ?")
            args.append(q.date_to)
        if q.app:
            where.append("c.app = ?")
            args.append(q.app)
        if q.mode:
            where.append("c.mode = ?")
            args.append(q.mode)
        if q.annotated_only:
            where.append("c.has_annotations = 1")
        for word in q.search.split():
            like = f"%{word}%"
            where.append(
                "(c.name LIKE ? OR c.app LIKE ? OR c.title LIKE ? OR c.notes LIKE ? OR c.ocr LIKE ?"
                " OR EXISTS (SELECT 1 FROM tags t WHERE t.capture_id = c.id AND t.tag LIKE ?))"
            )
            args.extend([like] * 6)
        order = SORTS.get(q.sort, SORTS["date_desc"])
        sql = f"SELECT c.* FROM captures c{joins} WHERE {' AND '.join(where)} ORDER BY {order}"
        rows = self.db.execute(sql, args).fetchall()
        tags = self._tags_for([r["id"] for r in rows])
        result = []
        for row in rows:
            cap = self._row_to_capture(row, with_tags=False)
            cap.tags = tags.get(cap.id, [])
            result.append(cap)
        return result

    def _tags_for(self, ids: list[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            for row in self.db.execute(
                f"SELECT capture_id, tag FROM tags WHERE capture_id IN ({marks}) ORDER BY tag", chunk
            ):
                out.setdefault(row[0], []).append(row[1])
        return out

    def apps(self) -> list[str]:
        rows = self.db.execute(
            "SELECT DISTINCT app FROM captures WHERE app != '' AND trashed IS NULL ORDER BY app COLLATE NOCASE"
        )
        return [r[0] for r in rows]

    def modes(self) -> list[str]:
        rows = self.db.execute("SELECT DISTINCT mode FROM captures WHERE mode != '' ORDER BY mode")
        return [r[0] for r in rows]

    # ------------------------------------------------------------------ update
    _EDITABLE = {"name", "favourite", "notes", "ocr", "has_annotations", "app", "title"}

    def update(self, capture_id: str, **fields) -> None:
        bad = set(fields) - self._EDITABLE
        if bad:
            raise ValueError(f"cannot update {bad}")
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = [int(v) if isinstance(v, bool) else v for v in fields.values()]
        self.db.execute(f"UPDATE captures SET {cols}, modified=? WHERE id=?",
                        (*vals, time.time(), capture_id))
        self.db.commit()
        self._emit("changed", [capture_id])

    def set_favourite(self, ids: list[str], value: bool) -> None:
        self.db.executemany("UPDATE captures SET favourite=? WHERE id=?", [(int(value), i) for i in ids])
        self.db.commit()
        self._emit("changed", ids)

    # ------------------------------------------------------------------ tags
    @staticmethod
    def normalize_tag(tag: str) -> str:
        return " ".join(tag.strip().lower().split())

    def get_tags(self, capture_id: str) -> list[str]:
        rows = self.db.execute("SELECT tag FROM tags WHERE capture_id=? ORDER BY tag", (capture_id,))
        return [r[0] for r in rows]

    def set_tags(self, capture_id: str, tags) -> None:
        clean = sorted({self.normalize_tag(t) for t in tags if self.normalize_tag(t)})
        self.db.execute("DELETE FROM tags WHERE capture_id=?", (capture_id,))
        self.db.executemany("INSERT INTO tags(capture_id, tag) VALUES(?,?)", [(capture_id, t) for t in clean])
        self.db.commit()
        self._emit("tags", [capture_id])

    def add_tag(self, ids: list[str], tag: str) -> None:
        tag = self.normalize_tag(tag)
        if not tag:
            return
        self.db.executemany("INSERT OR IGNORE INTO tags(capture_id, tag) VALUES(?,?)", [(i, tag) for i in ids])
        self.db.commit()
        self._emit("tags", ids)

    def all_tags(self) -> list[tuple[str, int]]:
        rows = self.db.execute(
            "SELECT t.tag, COUNT(*) FROM tags t JOIN captures c ON c.id = t.capture_id"
            " WHERE c.trashed IS NULL GROUP BY t.tag ORDER BY t.tag"
        )
        return [(r[0], r[1]) for r in rows]

    # ------------------------------------------------------------------ collections
    def collections(self) -> list[tuple[int, str, int]]:
        rows = self.db.execute(
            "SELECT k.id, k.name, COUNT(c.id) FROM collections k"
            " LEFT JOIN capture_collections cc ON cc.collection_id = k.id"
            " LEFT JOIN captures c ON c.id = cc.capture_id AND c.trashed IS NULL"
            " GROUP BY k.id ORDER BY k.name COLLATE NOCASE"
        )
        return [(r[0], r[1], r[2]) for r in rows]

    def create_collection(self, name: str) -> int:
        name = name.strip()
        if not name:
            raise ValueError("empty collection name")
        cur = self.db.execute("INSERT INTO collections(name, created) VALUES(?,?)", (name, time.time()))
        self.db.commit()
        self._emit("collections")
        return cur.lastrowid

    def rename_collection(self, collection_id: int, name: str) -> None:
        self.db.execute("UPDATE collections SET name=? WHERE id=?", (name.strip(), collection_id))
        self.db.commit()
        self._emit("collections")

    def delete_collection(self, collection_id: int) -> None:
        self.db.execute("DELETE FROM collections WHERE id=?", (collection_id,))
        self.db.commit()
        self._emit("collections")

    def add_to_collection(self, ids: list[str], collection_id: int) -> None:
        self.db.executemany(
            "INSERT OR IGNORE INTO capture_collections(capture_id, collection_id) VALUES(?,?)",
            [(i, collection_id) for i in ids],
        )
        self.db.commit()
        self._emit("collections", ids)

    def remove_from_collection(self, ids: list[str], collection_id: int) -> None:
        self.db.executemany(
            "DELETE FROM capture_collections WHERE capture_id=? AND collection_id=?",
            [(i, collection_id) for i in ids],
        )
        self.db.commit()
        self._emit("collections", ids)

    def capture_collections(self, capture_id: str) -> set[int]:
        rows = self.db.execute("SELECT collection_id FROM capture_collections WHERE capture_id=?", (capture_id,))
        return {r[0] for r in rows}

    # ------------------------------------------------------------------ trash
    def trash(self, ids: list[str]) -> None:
        now = time.time()
        self.db.executemany("UPDATE captures SET trashed=? WHERE id=?", [(now, i) for i in ids])
        self.db.commit()
        self._emit("trashed", ids)

    def restore(self, ids: list[str]) -> None:
        self.db.executemany("UPDATE captures SET trashed=NULL WHERE id=?", [(i,) for i in ids])
        self.db.commit()
        self._emit("restored", ids)

    def delete_permanently(self, ids: list[str]) -> None:
        for cid in ids:
            try:
                cap = self.get(cid)
            except KeyError:
                continue
            for p in (self.original_path(cap), self.doc_path(cap), self.thumb_path(cid)):
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass
            self.db.execute("DELETE FROM captures WHERE id=?", (cid,))
        self.db.commit()
        self._emit("deleted", ids)

    def empty_trash(self) -> int:
        ids = [r[0] for r in self.db.execute("SELECT id FROM captures WHERE trashed IS NOT NULL")]
        self.delete_permanently(ids)
        return len(ids)

    def purge_trash(self, days: int) -> int:
        if days <= 0:
            return 0
        cutoff = time.time() - days * 86400
        ids = [r[0] for r in self.db.execute("SELECT id FROM captures WHERE trashed IS NOT NULL AND trashed < ?", (cutoff,))]
        self.delete_permanently(ids)
        return len(ids)

    # ------------------------------------------------------------------ annotations
    def load_doc(self, capture_id: str):
        from .annotations import Document

        path = self.doc_path(capture_id)
        try:
            return Document.from_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return Document()
        except (OSError, ValueError) as exc:
            print(f"catcap: cannot read annotations {path}: {exc}")
            return Document()

    def save_doc(self, capture_id: str, doc) -> None:
        path = self.doc_path(capture_id)
        if doc.is_empty():
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        else:
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(doc.to_json(), encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        w, h = doc.output_size(*self._original_size(capture_id))
        self.db.execute(
            "UPDATE captures SET has_annotations=?, width=?, height=?, modified=? WHERE id=?",
            (int(bool(doc.items)), w, h, time.time(), capture_id),
        )
        self.db.commit()
        try:
            self.thumb_path(capture_id).unlink()
        except FileNotFoundError:
            pass
        self._emit("edited", [capture_id])

    def _original_size(self, capture_id: str) -> tuple[int, int]:
        return image_size(self.original_path(capture_id))

    # ------------------------------------------------------------------ maintenance
    def rebuild(self) -> tuple[int, list[str]]:
        """Re-add originals found on disk that are missing from the database (LIB-05).

        Returns (number added, ids of captures whose original file is missing). Missing captures
        are only reported, never removed here: the image folder may sit on a drive that is not
        mounted, and dropping the rows would lose names, tags, notes and collections for good.
        Call forget() for the ids the user really wants gone.
        """
        if not self.images_dir.is_dir():
            raise FileNotFoundError(f"the image folder {self.images_dir} is not available")
        known = {r[0] for r in self.db.execute("SELECT path FROM captures")}
        added = 0
        for png in sorted(self.images_dir.rglob("*.png")):
            rel = png.relative_to(self.images_dir).as_posix()
            if rel in known:
                continue
            w, h = image_size(png)
            if w <= 0 or h <= 0:
                continue
            cid = uuid.uuid4().hex
            created = png.stat().st_mtime
            self.db.execute(
                "INSERT INTO captures(id, name, created, modified, mode, width, height, size, path)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (cid, png.stem, created, created, "recovered", w, h, png.stat().st_size, rel),
            )
            added += 1
        self.db.commit()
        self._emit("rebuilt")
        return added, self.missing()

    def missing(self) -> list[str]:
        """Ids of captures whose original PNG is not on disk."""
        return [r[0] for r in self.db.execute("SELECT id, path FROM captures")
                if not (self.images_dir / r[1]).exists()]

    def forget(self, ids: list[str]) -> None:
        """Remove captures from the database only (their files are already gone)."""
        self.db.executemany("DELETE FROM captures WHERE id=?", [(i,) for i in ids])
        self.db.commit()
        for cid in ids:
            try:
                self.thumb_path(cid).unlink()
            except FileNotFoundError:
                pass
        self._emit("deleted", ids)

    def move_images(self, new_dir: Path) -> None:
        """Move the image store to a new folder (LIB-06).

        Files are moved one by one and merged into folders that already exist, so a target that
        already has a 2026/ folder does not end up with 2026/2026/. Nothing is overwritten. If a
        file cannot be moved, everything moved so far goes back and the library stays where it was.
        """
        old_dir = self.images_dir.resolve()
        new_dir = Path(new_dir).expanduser().resolve()
        if new_dir == old_dir:
            return
        if new_dir.is_relative_to(old_dir):
            raise ValueError("the new folder is inside the current image folder")
        if old_dir.is_relative_to(new_dir):
            raise ValueError("the current image folder is inside the new folder")
        files = [f for f in sorted(old_dir.rglob("*")) if f.is_file() or f.is_symlink()]
        clashes = [f.relative_to(old_dir).as_posix() for f in files if (new_dir / f.relative_to(old_dir)).exists()]
        if clashes:
            more = f" and {len(clashes) - 5} more" if len(clashes) > 5 else ""
            raise FileExistsError("these files already exist in the new folder: "
                                  + ", ".join(clashes[:5]) + more)
        paths.ensure_dir(new_dir)
        moved: list[tuple[Path, Path]] = []
        try:
            for src in files:
                dst = new_dir / src.relative_to(old_dir)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                moved.append((src, dst))
        except OSError:
            for src, dst in reversed(moved):
                try:
                    src.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(dst), str(src))
                except OSError as exc:
                    print(f"catcap: could not move {dst} back: {exc}")
            raise
        # Remove the now empty folders of the old store (keep the old root itself).
        for d in sorted((d for d in old_dir.rglob("*") if d.is_dir()), key=lambda d: len(d.parts), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass
        self.images_dir = new_dir
        self._emit("moved")

    def close(self) -> None:
        self.db.close()


def read_image(path):
    from PyQt6.QtGui import QImageReader

    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        raise ValueError(f"{path}: {reader.errorString()}")
    return image


def image_size(path) -> tuple[int, int]:
    from PyQt6.QtGui import QImageReader

    size = QImageReader(str(path)).size()
    return size.width(), size.height()


def export_metadata(cap: Capture) -> str:
    return json.dumps({k: getattr(cap, k) for k in ("id", "name", "created", "app", "title", "tags")})
