from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sqlite3


def canonical(value):
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, list):
        return [canonical(item) for item in value]
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items()}
    return value


def encode(value):
    return json.dumps(canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS datasets (
                    revision TEXT PRIMARY KEY, label TEXT NOT NULL, body TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notebooks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                    revision TEXT NOT NULL REFERENCES datasets(revision),
                    selection TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS freezes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                    revision TEXT NOT NULL REFERENCES datasets(revision),
                    selection TEXT NOT NULL, spec TEXT NOT NULL,
                    data_sha256 TEXT NOT NULL, scope_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL, reasons TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS comparisons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                    freeze_id INTEGER REFERENCES freezes(id),
                    revision TEXT NOT NULL REFERENCES datasets(revision),
                    selection TEXT NOT NULL, spec TEXT NOT NULL,
                    assessment TEXT NOT NULL, result TEXT NOT NULL,
                    deviated INTEGER NOT NULL, created_at TEXT NOT NULL
                );
            """)

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def put_dataset(self, label, data):
        if not isinstance(label, str) or not label.strip() or len(label) > 120:
            raise ValueError("请提供不超过120字的数据名称")
        revision = fingerprint(data)
        with self.connection() as connection:
            connection.execute("INSERT OR IGNORE INTO datasets VALUES(?,?,?,?)",
                               (revision, label.strip(), encode(data), datetime.now(timezone.utc).isoformat()))
        return revision

    def datasets(self):
        with self.connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT revision,label,created_at FROM datasets ORDER BY created_at DESC,revision")]

    def dataset_created_at(self, revision):
        with self.connection() as connection:
            row = connection.execute("SELECT created_at FROM datasets WHERE revision=?", (revision,)).fetchone()
        if row is None:
            raise LookupError("数据版本不存在")
        return row["created_at"]

    def dataset(self, revision):
        with self.connection() as connection:
            row = connection.execute("SELECT body FROM datasets WHERE revision=?", (revision,)).fetchone()
        if row is None:
            raise LookupError("数据版本不存在")
        return json.loads(row["body"])

    def save(self, title, revision, selection, result):
        if not isinstance(title, str) or not title.strip() or len(title) > 160:
            raise ValueError("分析名称应为1至160字")
        with self.connection() as connection:
            if connection.execute("SELECT 1 FROM datasets WHERE revision=?", (revision,)).fetchone() is None:
                raise LookupError("数据版本不存在")
            cursor = connection.execute("INSERT INTO notebooks(title,revision,selection,result,created_at) VALUES(?,?,?,?,?)",
                                        (title.strip(), revision, encode(selection), encode(result), datetime.now(timezone.utc).isoformat()))
            identifier = cursor.lastrowid
        return self.notebook(identifier)

    def notebooks(self):
        with self.connection() as connection:
            return [dict(row) for row in connection.execute("SELECT id,title,revision,created_at FROM notebooks ORDER BY id DESC")]

    def notebook(self, identifier):
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM notebooks WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise LookupError("分析记录不存在")
        result = dict(row)
        for field in ["selection", "result"]:
            result[field] = json.loads(result[field])
        result["dataset"] = self.dataset(result["revision"])
        return result

    def freeze(self, title, revision, selection, spec, data_sha256, scope_sha256,
               status, reasons):
        if not isinstance(title, str) or not title.strip() or len(title) > 160:
            raise ValueError("冻结名称应为1至160字")
        with self.connection() as connection:
            if connection.execute("SELECT 1 FROM datasets WHERE revision=?", (revision,)).fetchone() is None:
                raise LookupError("数据版本不存在")
            cursor = connection.execute(
                "INSERT INTO freezes(title,revision,selection,spec,data_sha256,scope_sha256,status,reasons,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (title.strip(), revision, encode(selection), encode(spec), data_sha256,
                 scope_sha256, status, encode(reasons), datetime.now(timezone.utc).isoformat()))
            identifier = cursor.lastrowid
        return self.freeze_record(identifier)

    def freezes(self):
        with self.connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT id,title,revision,status,created_at FROM freezes ORDER BY id DESC")]

    def freeze_record(self, identifier):
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM freezes WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise LookupError("冻结记录不存在")
        result = dict(row)
        for field in ["selection", "spec", "reasons"]:
            result[field] = json.loads(result[field])
        result["dataset"] = self.dataset(result["revision"])
        return result

    def compare_record(self, title, freeze_id, revision, selection, spec, assessment,
                       result, deviated):
        if not isinstance(title, str) or not title.strip() or len(title) > 160:
            raise ValueError("比较名称应为1至160字")
        with self.connection() as connection:
            if connection.execute("SELECT 1 FROM datasets WHERE revision=?", (revision,)).fetchone() is None:
                raise LookupError("数据版本不存在")
            if freeze_id is not None:
                if connection.execute("SELECT 1 FROM freezes WHERE id=?", (freeze_id,)).fetchone() is None:
                    raise LookupError("冻结记录不存在")
            cursor = connection.execute(
                "INSERT INTO comparisons(title,freeze_id,revision,selection,spec,assessment,result,deviated,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (title.strip(), freeze_id, revision, encode(selection), encode(spec),
                 encode(assessment), encode(result), 1 if deviated else 0,
                 datetime.now(timezone.utc).isoformat()))
            identifier = cursor.lastrowid
        return self.comparison(identifier)

    def comparisons(self):
        with self.connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT id,title,freeze_id,revision,created_at FROM comparisons ORDER BY id DESC")]

    def comparison(self, identifier):
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM comparisons WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise LookupError("比较记录不存在")
        result = dict(row)
        for field in ["selection", "spec", "assessment", "result"]:
            result[field] = json.loads(result[field])
        result["deviated"] = bool(result["deviated"])
        result["dataset"] = self.dataset(result["revision"])
        return result
