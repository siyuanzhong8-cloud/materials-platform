"""SQLite queries and exact, immutable structure storage."""
import json
import sqlite3
import zlib
from contextlib import contextmanager
from pathlib import Path

from ase.data import atomic_numbers

from backend.domain import canonical

SCHEMA = """
CREATE TABLE IF NOT EXISTS methods(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, name TEXT NOT NULL, imported_at TEXT NOT NULL,
 records INTEGER NOT NULL, method_id TEXT NOT NULL REFERENCES methods(id), source_hashes TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS configurations(
 id TEXT PRIMARY KEY, site_id TEXT NOT NULL, variant TEXT NOT NULL, method_id TEXT NOT NULL REFERENCES methods(id),
 batch_id TEXT NOT NULL REFERENCES batches(id), family_key TEXT NOT NULL, formula TEXT NOT NULL,
 n_elements INTEGER NOT NULL, eta REAL NOT NULL, g1 REAL NOT NULL, g2 REAL NOT NULL, g3 REAL NOT NULL, g4 REAL NOT NULL,
 fingerprint TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(site_id,variant,method_id));
CREATE TABLE IF NOT EXISTS elements(config_id TEXT NOT NULL REFERENCES configurations(id), symbol TEXT NOT NULL,
 PRIMARY KEY(config_id,symbol));
CREATE INDEX IF NOT EXISTS idx_symbol ON elements(symbol,config_id);
CREATE INDEX IF NOT EXISTS idx_eta ON configurations(eta,id);
CREATE INDEX IF NOT EXISTS idx_family ON configurations(family_key);
CREATE TABLE IF NOT EXISTS structures(config_id TEXT NOT NULL REFERENCES configurations(id), state TEXT NOT NULL,
 sha256 TEXT NOT NULL, metadata TEXT NOT NULL, raw BLOB NOT NULL, geometry BLOB NOT NULL,
 PRIMARY KEY(config_id,state));
PRAGMA user_version=1;
"""


class Store:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("数据库版本不兼容，请保留原文件并联系维护者")
            con.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=60)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def summary(self):
        with self.connect() as con:
            return {
                "configurations": con.execute("SELECT count(*) FROM configurations").fetchone()[0],
                "families": con.execute("SELECT count(DISTINCT family_key) FROM configurations").fetchone()[0],
                "structures": con.execute("SELECT count(*) FROM structures").fetchone()[0],
                "elements": sorted((r[0] for r in con.execute("SELECT DISTINCT symbol FROM elements")), key=lambda s: atomic_numbers[s]),
                "methods": [json.loads(r[0]) for r in con.execute("SELECT payload FROM methods ORDER BY id")],
                "review_status": "待科学复核 · UMA + 固定热力学修正估算",
            }

    def query(self, elements=None, match="exact", eta_min=None, eta_max=None,
              sort="eta", order="asc", offset=0, limit=50, method_id=None, q="",
              exclude_elements=None):
        symbols = sorted(set(elements or []))
        excluded = sorted(set(exclude_elements or []))
        if any(s not in atomic_numbers or s == "X" for s in symbols + excluded):
            raise ValueError("元素符号无效，请使用 Cu、Cl 等标准符号")
        if match not in ("exact", "contains") or sort not in ("eta", "g1", "g2", "g3", "g4", "formula") or order not in ("asc", "desc"):
            raise ValueError("筛选或排序方式无效")
        clauses, values = [], []
        if symbols:
            if match == "exact":
                clauses.append("c.family_key=?")
                values.append("+".join(symbols))
            else:
                for symbol in symbols:
                    clauses.append("EXISTS (SELECT 1 FROM elements e WHERE e.config_id=c.id AND e.symbol=?)")
                    values.append(symbol)
        for symbol in excluded:
            clauses.append("NOT EXISTS (SELECT 1 FROM elements e WHERE e.config_id=c.id AND e.symbol=?)")
            values.append(symbol)
        term = (q or "").strip()
        if term:
            like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            clauses.append("(c.formula LIKE ? ESCAPE '\\' OR c.family_key LIKE ? ESCAPE '\\' OR c.id LIKE ? ESCAPE '\\' OR c.site_id LIKE ? ESCAPE '\\' OR c.variant LIKE ? ESCAPE '\\')")
            values.extend([like] * 5)
        for column, op, value in [("eta", ">=", eta_min), ("eta", "<=", eta_max), ("method_id", "=", method_id)]:
            if value is not None and value != "":
                clauses.append(f"c.{column}{op}?")
                values.append(value)
        if eta_min is not None and eta_max is not None and eta_min > eta_max:
            raise ValueError("过电位下限不能大于上限")
        if offset < 0 or (limit is not None and limit < 1):
            raise ValueError("分页参数无效")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        paging = " LIMIT ? OFFSET ?" if limit is not None else ""
        with self.connect() as con:
            total = con.execute("SELECT count(*) FROM configurations c" + where, values).fetchone()[0]
            rows = con.execute(f"SELECT c.payload FROM configurations c{where} ORDER BY c.{sort} {order},c.id{paging}",
                               values + ([limit, offset] if limit is not None else [])).fetchall()
        return {"total": total, "items": [self.public(json.loads(r[0])) for r in rows]}

    @staticmethod
    def public(record):
        keys = ("id", "site_id", "variant", "family_key", "formula", "formula_full", "n_atoms", "elements",
                "g1", "g2", "g3", "g4", "eta", "limiting_steps", "review_status", "method_id", "batch_name")
        return {k: record[k] for k in keys}

    def detail(self, config_id):
        with self.connect() as con:
            row = con.execute("SELECT payload,method_id FROM configurations WHERE id=?", (config_id,)).fetchone()
            if row is None:
                raise KeyError("没有找到这个构型")
            result = json.loads(row["payload"])
            result["method"] = json.loads(con.execute("SELECT payload FROM methods WHERE id=?", (row["method_id"],)).fetchone()[0])
            result["structures"] = {r["state"]: json.loads(r["metadata"]) for r in con.execute("SELECT state,metadata FROM structures WHERE config_id=?", (config_id,))}
            return result

    def structure(self, config_id, state):
        with self.connect() as con:
            row = con.execute("SELECT raw,geometry FROM structures WHERE config_id=? AND state=?", (config_id, state)).fetchone()
            if row is None:
                raise KeyError("没有找到对应结构")
            return json.loads(zlib.decompress(row["geometry"])), zlib.decompress(row["raw"])

    def fingerprints(self):
        with self.connect() as con:
            return dict(con.execute("SELECT id,fingerprint FROM configurations"))

    def batches(self):
        with self.connect() as con:
            return [{**dict(r), "source_hashes": json.loads(r["source_hashes"])} for r in con.execute("SELECT * FROM batches ORDER BY imported_at DESC")]

    def commit_stage(self, stage):
        """Check identities again while holding the write lock; commit all or none."""
        with self.connect() as dest, stage.connect() as src:
            dest.execute("BEGIN IMMEDIATE")
            existing = dict(dest.execute("SELECT id,fingerprint FROM configurations"))
            new_ids, duplicates = [], 0
            for row in src.execute("SELECT id,fingerprint FROM configurations"):
                if row["id"] in existing:
                    if existing[row["id"]] != row["fingerprint"]:
                        raise ValueError("预检后发现位点内容冲突，请重新预检；已有数据未改动")
                    duplicates += 1
                else:
                    new_ids.append(row["id"])
            if not new_ids:
                return {"inserted": 0, "duplicates": duplicates, "batch_id": None}
            for row in src.execute("SELECT * FROM methods"):
                dest.execute("INSERT OR IGNORE INTO methods VALUES (?,?)", tuple(row))
            batch = dict(src.execute("SELECT * FROM batches").fetchone())
            batch["records"] = len(new_ids)
            dest.execute("INSERT INTO batches VALUES (?,?,?,?,?,?)", tuple(batch.values()))
            for config_id in new_ids:
                row = src.execute("SELECT * FROM configurations WHERE id=?", (config_id,)).fetchone()
                dest.execute("INSERT INTO configurations VALUES (" + ",".join("?" for _ in row) + ")", tuple(row))
                dest.executemany("INSERT INTO elements VALUES (?,?)", [tuple(r) for r in src.execute("SELECT * FROM elements WHERE config_id=?", (config_id,))])
                dest.executemany("INSERT INTO structures VALUES (?,?,?,?,?,?)", [tuple(r) for r in src.execute("SELECT * FROM structures WHERE config_id=?", (config_id,))])
            return {"inserted": len(new_ids), "duplicates": duplicates, "batch_id": batch["id"]}
