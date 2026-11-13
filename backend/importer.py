"""Validate a batch into an isolated staging database, then commit atomically."""
import csv
import hashlib
import json
import re
import tarfile
import threading
import uuid
import zlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from backend.bundle import (ACCEPTED_FILES, ARCHIVE_FILE, check_state_composition,
                            geometry_bytes, identity, json_file, sha_file, table_inputs)
from backend.domain import STATES, adsorption_energies, canonical, digest, finite, normalize_method, thermodynamics
from backend.store import Store


class Importer:
    def __init__(self, store, staging_root):
        self.store = store
        self.staging_root = Path(staging_root).resolve()
        self.staging_root.mkdir(parents=True, exist_ok=True)
        self._commit_lock = threading.Lock()

    def token_dir(self, token):
        if not re.fullmatch(r"[0-9a-f]{32}", token):
            raise ValueError("无效的导入编号")
        return self.staging_root / token

    def preview(self, directory, batch_name, method_override=None):
        token = uuid.uuid4().hex
        folder = self.token_dir(token)
        folder.mkdir()
        directory = Path(directory)
        report = {"token": token, "rows": 0, "new": 0, "duplicates": 0, "conflicts": 0,
                  "errors": [], "error_count": 0, "can_commit": False, "method_name": ""}

        def error(message):
            report["error_count"] += 1
            if len(report["errors"]) < 100:
                report["errors"].append(str(message))

        try:
            method_path = directory / "method.json"
            if not method_path.is_file() and method_override is None:
                raise ValueError("缺少 method.json；请使用导入模板填写参考能、模型哈希和热力学修正")
            method = normalize_method(json_file(method_path) if method_path.is_file() else method_override)
            report["method_name"] = method["name"]
            by_row, index, g_tables = table_inputs(directory)
            report["rows"] = len(by_row)
            stage = Store(folder / "stage.sqlite")
            batch_id = uuid.uuid4().hex
            batch_name = str(batch_name or "新增完整四态数据").strip()[:200]
            manifest_path = directory / "manifest.json"
            review = str(json_file(manifest_path).get("status", "READY_FOR_REVIEW")) if manifest_path.is_file() else "READY_FOR_REVIEW"
            source_hashes = {name: sha_file(directory / name) for name in ACCEPTED_FILES if (directory / name).is_file()}
            existing = self.store.fingerprints()
            staged_ids = {}
            # Build identities and thermodynamics first; structure metadata is appended below.
            with stage.connect() as con:
                con.execute("INSERT INTO methods VALUES (?,?)", (method["id"], canonical(method)))
                con.execute("INSERT INTO batches VALUES (?,?,?,?,?,?)", (batch_id, batch_name, datetime.now(timezone.utc).isoformat(), len(by_row), method["id"], canonical(source_hashes)))
                records = {}
                for number, row in by_row.items():
                    try:
                        site, variant = identity(row)
                        es = {s: finite(row[f"E_{s}_eV"]) for s in STATES}
                        delta = adsorption_energies(es, method)
                        td = thermodynamics(delta, method)
                        for s in ("O", "OH", "OOH"):
                            key = f"DeltaE_{s}_eV"
                            if row.get(key) not in (None, "") and abs(finite(row[key]) - delta[s]) > 1e-8:
                                raise ValueError(f"{key} 与四态能量和参考能不一致")
                        for name, g_rows in g_tables.items():
                            grow = g_rows[(site, variant)]
                            checks = {**{f"DeltaG_{s}_eV": td["intermediates"][s] for s in ("O", "OH", "OOH")},
                                      **{f"DeltaG_step{i+1}_eV": v for i, v in enumerate(td["steps"])}, "overpotential_V": td["eta"]}
                            for k, value in checks.items():
                                if k in grow and abs(finite(grow[k], k) - value) > 1e-8:
                                    raise ValueError(f"{name} 的 {k} 与当前口径计算不一致")
                        config_id = digest([site, variant, method["id"]])[:32]
                        records[number] = {"id": config_id, "site_id": site, "variant": variant, "source_row": number,
                                           "method_id": method["id"], "batch_id": batch_id, "batch_name": batch_name,
                                           "energies": es, "delta_e": delta, "intermediates": td["intermediates"],
                                           **{f"g{i+1}": v for i, v in enumerate(td["steps"])}, "eta": td["eta"],
                                           "limiting_steps": td["limiting_steps"], "review_status": review}
                        # Insert after bare parsing; stage structures are collected in a separate table until then.
                        staged_ids[number] = config_id
                    except (ValueError, KeyError) as e:
                        error(f"第 {number} 行：{e}")
                con.execute("CREATE TABLE pending(config_id TEXT,state TEXT,sha256 TEXT,metadata TEXT,raw BLOB,geometry BLOB,counts TEXT,PRIMARY KEY(config_id,state))")
                seen = set()
                total_bytes = 0
                with tarfile.open(directory / ARCHIVE_FILE, "r|gz") as tar:
                    for member in tar:
                        if member.isdir():
                            continue
                        if not member.isfile() or member.name not in index:
                            error(f"结构包含未索引的文件或特殊条目：{member.name}")
                            continue
                        if member.name in seen:
                            error(f"结构包路径重复：{member.name}")
                            continue
                        seen.add(member.name)
                        if member.size > 4 * 1024 * 1024 or member.size < 1:
                            error(f"结构大小无效：{member.name}")
                            continue
                        total_bytes += member.size
                        if total_bytes > 4 * 1024**3:
                            raise ValueError("结构包解压后超过 4 GB，请拆分批次")
                        ix = index[member.name]
                        number, state = ix["row"], ix["state"]
                        if number not in records:
                            continue
                        try:
                            data = tar.extractfile(member).read()
                            sha = hashlib.sha256(data).hexdigest()
                            if sha != (ix.get("sha256") or "").lower():
                                raise ValueError("结构 SHA256 校验不一致")
                            record = records[number]
                            atoms, geo = geometry_bytes(data, state, record["energies"][state], sha)
                            counts = Counter(atoms.get_chemical_symbols())
                            metadata = {k: ix.get(k, "") for k in ("source", "manifest", "archive_member")}
                            metadata.update(sha256=sha, n_atoms=len(atoms))
                            con.execute("INSERT INTO pending VALUES (?,?,?,?,?,?,?)", (record["id"], state, sha, canonical(metadata),
                                        zlib.compress(data), zlib.compress(canonical(geo).encode()), canonical(counts)))
                            if state == "bare":
                                record.update(formula=atoms.get_chemical_formula(mode="metal", empirical=True),
                                              formula_full=atoms.get_chemical_formula(mode="metal"), n_atoms=len(atoms),
                                              elements=sorted(counts), family_key="+".join(sorted(counts)))
                                if by_row[number].get("family_key") and by_row[number]["family_key"] != record["family_key"]:
                                    raise ValueError("family_key 与裸基底元素集合不一致")
                        except (ValueError, KeyError, UnicodeError) as e:
                            error(f"第 {number} 行 {state}：{e}")
                for missing in set(index) - seen:
                    error(f"结构包缺少索引文件：{missing}")
                for number, record in records.items():
                    try:
                        parts = list(con.execute("SELECT * FROM pending WHERE config_id=?", (record["id"],)))
                        if len(parts) != 4 or "family_key" not in record:
                            raise ValueError("四态结构未完整通过校验")
                        base = json.loads(next(x["counts"] for x in parts if x["state"] == "bare"))
                        for part in parts:
                            check_state_composition(base, json.loads(part["counts"]), part["state"])
                        for name, g_rows in g_tables.items():
                            grow = g_rows[(record["site_id"], record["variant"])]
                            if "family_key" in grow and grow["family_key"] != record["family_key"]:
                                raise ValueError(f"{name} 的 family_key 与裸基底元素集合不一致")
                        fingerprint = digest({"identity": [record["site_id"], record["variant"], method["id"]],
                                              "energies": record["energies"], "hashes": {x["state"]: x["sha256"] for x in parts}})
                        config_id = record["id"]
                        if config_id in existing:
                            if fingerprint == existing[config_id]:
                                report["duplicates"] += 1
                            else:
                                report["conflicts"] += 1
                                error(f"位点 {record['site_id']} 的内容与已有记录冲突；请为新构型使用新标识或 variant")
                        else:
                            report["new"] += 1
                        con.execute("INSERT INTO configurations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                            config_id, record["site_id"], record["variant"], method["id"], batch_id,
                            record["family_key"], record["formula"], len(record["elements"]), record["eta"],
                            record["g1"], record["g2"], record["g3"], record["g4"], fingerprint, canonical(record)))
                        con.executemany("INSERT INTO elements VALUES (?,?)", [(config_id, s) for s in record["elements"]])
                        con.executemany("INSERT INTO structures VALUES (?,?,?,?,?,?)", [tuple(part)[:6] for part in parts])
                    except (ValueError, KeyError) as e:
                        error(f"第 {number} 行：{e}")
                con.execute("DROP TABLE pending")
            report["can_commit"] = report["error_count"] == 0 and report["conflicts"] == 0
        except (ValueError, OSError, tarfile.TarError, csv.Error) as e:
            error(str(e))
        (folder / "report.json").write_text(canonical(report), encoding="utf-8")
        return report

    def report(self, token):
        path = self.token_dir(token) / "report.json"
        if not path.is_file():
            raise ValueError("导入预检记录不存在，请重新选择文件")
        return json_file(path)

    def commit(self, token):
        with self._commit_lock:
            report = self.report(token)
            if "receipt" in report:
                return report["receipt"]
            if not report["can_commit"]:
                raise ValueError("预检存在错误或冲突，整批未导入")
            stage_path = self.token_dir(token) / "stage.sqlite"
            if not stage_path.is_file():
                raise ValueError("预检数据已清理，请重新选择文件")
            receipt = self.store.commit_stage(Store(stage_path))
            report["receipt"] = receipt
            (self.token_dir(token) / "report.json").write_text(canonical(report), encoding="utf-8")
            stage_path.unlink()
            return receipt
