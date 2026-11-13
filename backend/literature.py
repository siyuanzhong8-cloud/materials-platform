"""Source-grounded public materials, independent from the UMA prediction cohort."""
import csv
import hashlib
import io
import json
import sqlite3
import zlib
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
from ase.data import atomic_numbers
from ase.io import read

from backend.domain import STATES, canonical, digest, finite


def web_url(value):
    return isinstance(value, str) and urlparse(value).scheme in ("https", "http") and bool(urlparse(value).netloc)


def optional_number(value):
    return None if value in (None, "") else finite(value)


DATA_CLASS_LABELS = {"experimental": "实验数据", "computed": "计算数据", "predicted": "预测数据"}

REACTION_CODES = {"OER": "OER", "ORR": "ORR", "CO2": "CO2", "CO2RR": "CO2"}
REACTION_LABELS = {"OER": "OER", "ORR": "ORR", "CO2": "CO2RR"}


def normalize_reactions(reaction):
    """Accept a single code, alias (CO2RR), or a comma-separated / list of codes."""
    if isinstance(reaction, (list, tuple, set)):
        parts = [str(x) for x in reaction]
    else:
        parts = [p for p in str(reaction or "").replace(";", ",").split(",") if p.strip()]
    codes = []
    for part in parts:
        key = part.strip().upper().replace(" ", "").replace("₂", "2")
        if key not in REACTION_CODES:
            raise ValueError("公开材料反应类型无效")
        code = REACTION_CODES[key]
        if code not in codes:
            codes.append(code)
    if not codes:
        raise ValueError("请至少选择一种反应")
    return codes


def reaction_label(code):
    return REACTION_LABELS.get(code, code or "")



def data_class_of(record):
    """数据类型: explicit payload field first; otherwise derived without rewriting stored rows.
    Measured values -> experimental; literature DFT values -> computed (UMA/ML predictions live in oer.sqlite)."""
    explicit = record.get("data_class")
    if explicit in DATA_CLASS_LABELS:
        return explicit
    method = record.get("method")
    mtype = (method.get("type") if isinstance(method, dict) else str(method or "")) or ""
    if record.get("experimental_overpotential_V") is not None:
        return "experimental"
    if "DFT" in mtype.upper():
        return "computed"
    if record.get("experimental_status") == "experimentally_verified" or (record.get("source") or {}).get("id") == "dash_experimental_2025":
        return "experimental"
    return "computed"


def measurement_basis_of(record):
    if record.get("measurement_basis"):
        return record["measurement_basis"]
    j = record.get("experimental_current_density_mA_cm2")
    if record.get("experimental_overpotential_V") is not None:
        return f"η@{j:g} mA/cm²" if isinstance(j, (int, float)) else "η（电流密度未注明）"
    return ""


def group_key(record):
    """Sort/group key: never interleave data types or different measurement bases."""
    cls = data_class_of(record)
    return ({"computed": 0, "experimental": 1, "predicted": 2}.get(cls, 3), bool(record.get("high_throughput")),
            measurement_basis_of(record))


_RECORD_CACHE = {}


class LiteratureStore:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS materials(id TEXT PRIMARY KEY,reaction TEXT NOT NULL,
                    source_id TEXT NOT NULL REFERENCES sources(id),family_key TEXT NOT NULL,
                    experimental_status TEXT NOT NULL,complete INTEGER NOT NULL,fingerprint TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS public_reaction ON materials(reaction,family_key);
                CREATE TABLE IF NOT EXISTS structures(material_id TEXT NOT NULL REFERENCES materials(id),
                    state TEXT NOT NULL,format TEXT NOT NULL,raw BLOB NOT NULL,geometry BLOB NOT NULL,
                    PRIMARY KEY(material_id,state));
            """)

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

    @staticmethod
    def geometry(data, fmt, state, sha, energy):
        formats = {"extxyz": "extxyz", "xyz": "extxyz", "cif": "cif", "poscar": "vasp", "vasp": "vasp"}
        if fmt not in formats:
            raise ValueError(f"不支持的文献结构格式：{fmt}")
        try:
            frames = read(io.StringIO(data.decode("utf-8-sig")), format=formats[fmt], index=":")
        except Exception as error:
            raise ValueError(f"文献结构无法读取：{error}") from None
        if len(frames) != 1 or not len(frames[0]):
            raise ValueError("每个文献结构文件必须恰好包含一个有效结构")
        atoms = frames[0]
        if not np.isfinite(atoms.positions).all() or not np.isfinite(atoms.cell.array).all():
            raise ValueError("文献结构含无效坐标或晶胞")
        symbols = atoms.get_chemical_symbols()
        if "X" in symbols:
            raise ValueError("文献结构包含未知元素")
        return {"state": state, "atoms": [{"elem": s, "x": float(p[0]), "y": float(p[1]), "z": float(p[2])} for s, p in zip(symbols, atoms.positions)],
                "cell": atoms.cell.array.tolist(), "pbc": atoms.pbc.tolist(), "n_atoms": len(atoms),
                "sha256": sha, "energy_eV": energy}

    def import_catalog(self, path):
        path = Path(path).resolve()
        catalog = json.loads(path.read_text(encoding="utf-8-sig"))
        if catalog.get("schema_version") != 1:
            raise ValueError("公开材料目录必须使用 schema_version=1")
        try:
            imported = normalize_reactions(catalog.get("reaction", "OER"))
        except ValueError:
            raise ValueError("未知反应类型") from None
        if len(imported) != 1:
            raise ValueError("一份公开材料目录只能标注一种反应")
        reaction = imported[0]
        sources = {}
        for src in catalog.get("sources", []):
            if not src.get("id") or not src.get("title") or not web_url(src.get("url")):
                raise ValueError("每个公开数据来源需有 id、标题和可追溯 URL")
            if src["id"] in sources:
                raise ValueError("公开数据来源 id 重复")
            sources[src["id"]] = src
        inserted, duplicates = 0, 0
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            seen = set()
            for original in catalog.get("materials", []):
                source = sources.get(original.get("source_id"))
                if source is None or not original.get("id"):
                    raise ValueError("材料缺少有效来源或原始 id")
                source_id = digest([source["id"], source.get("doi"), source.get("data_url") or source["url"]])[:32]
                material_id = digest([reaction, source_id, original["id"]])[:32]
                if material_id in seen:
                    raise ValueError("同批公开材料标识重复")
                seen.add(material_id)
                elements = sorted(set(original.get("elements") or []))
                if not elements or any(s not in atomic_numbers or s == "X" for s in elements):
                    raise ValueError("公开材料需声明有效的基底元素集合")
                status = original.get("experimental_status") or "unknown"
                if status not in ("experimentally_verified", "computational_only", "unknown"):
                    raise ValueError("公开材料实验验证分类无效")
                if status == "experimentally_verified" and not web_url(original.get("experimental_evidence_url")):
                    raise ValueError("实验验证分类必须提供原文证据链接")
                steps = original.get("steps_dG_eV")
                if steps is not None:
                    if not isinstance(steps, list) or len(steps) != 4:
                        raise ValueError("四步自由能必须提供四个数值，未公开时应留空")
                    steps = [finite(v, "文献四步自由能") for v in steps]
                record = {**original, "id": material_id, "external_id": original["id"], "reaction": reaction,
                          "name": str(original.get("name") or original.get("formula") or original["id"]),
                          "formula": str(original.get("formula") or ""), "elements": elements,
                          "family_key": "+".join(elements), "site_id": str(original.get("site_id") or original["id"]),
                          "variant": str(original.get("variant") or ""), "experimental_status": status,
                          "source": source, "steps_dG_eV": steps, "overpotential_V": optional_number(original.get("overpotential_V")),
                          "experimental_overpotential_V": optional_number(original.get("experimental_overpotential_V")),
                          "notes": "\n".join(str(x) for x in original["notes"]) if isinstance(original.get("notes"), list) else (original.get("notes") or ""),
                          "source_notes": original.get("notes"), "method": original.get("method") or source.get("method") or "",
                          "states": {s: {"available": False} for s in STATES}}
                structures = []
                for state in STATES:
                    item = (original.get("states") or {}).get(state) or {}
                    energy = optional_number(item.get("energy_eV"))
                    record["states"][state].update({**item, "available": False, "energy_eV": energy})
                    if not item.get("path"):
                        continue
                    file = (path.parent / item["path"]).resolve()
                    if not file.is_relative_to(path.parent) or not file.is_file():
                        raise ValueError("文献结构路径缺失或超出导入目录")
                    if file.stat().st_size > 16 * 1024**2:
                        raise ValueError("单个文献结构过大")
                    raw = file.read_bytes()
                    sha = hashlib.sha256(raw).hexdigest()
                    if sha != (item.get("sha256") or "").lower():
                        raise ValueError(f"文献结构哈希不一致：{item['path']}")
                    fmt = str(item.get("format") or file.suffix.lstrip(".")).lower()
                    geometry = self.geometry(raw, fmt, state, sha, energy)
                    has_cell = int(np.linalg.matrix_rank(np.asarray(geometry["cell"]))) == 3
                    record["states"][state] = {**item, "available": True, "n_atoms": geometry["n_atoms"], "energy_eV": energy,
                                               "sha256": sha, "format": fmt, "periodic_cell_available": has_cell}
                    structures.append((material_id, state, fmt, zlib.compress(raw), zlib.compress(canonical(geometry).encode())))
                    if state == "bare":
                        actual = sorted(set(a["elem"] for a in geometry["atoms"]))
                        if actual != elements:
                            raise ValueError("文献声明元素集合与裸态结构不一致")
                complete = (all(record["states"][s]["available"] and record["states"][s].get("energy_eV") is not None
                                and record["states"][s].get("periodic_cell_available")
                                and not record["states"][s].get("periodic_cell_missing") for s in STATES)
                            and (record.get("pairing_validation") or {}).get("periodic_cell_available") is not False)
                record["complete_four_states"] = complete
                missing = []
                for s in STATES:
                    if not record["states"][s]["available"]:
                        missing.append(f"{s} 结构")
                    if record["states"][s].get("energy_eV") is None:
                        missing.append(f"{s} 能量")
                    if record["states"][s]["available"] and (not record["states"][s].get("periodic_cell_available") or record["states"][s].get("periodic_cell_missing")):
                        missing.append(f"{s} 晶胞")
                if steps is None:
                    missing.append("四步 ΔG")
                if record["overpotential_V"] is None:
                    missing.append("理论过电位")
                record["data_missing"] = bool(missing)
                record["missing_fields"] = missing
                record["completeness"] = str(original.get("completeness") or ("完整四态" if complete else "部分公开数据"))
                fingerprint = digest(record)
                old = con.execute("SELECT fingerprint FROM materials WHERE id=?", (material_id,)).fetchone()
                if old:
                    if old[0] != fingerprint:
                        raise ValueError("相同公开材料标识内容有变化，请核查并使用明确版本，不覆盖既有数据")
                    duplicates += 1
                    continue
                con.execute("INSERT OR IGNORE INTO sources VALUES (?,?)", (source_id, canonical(source)))
                con.execute("INSERT INTO materials VALUES (?,?,?,?,?,?,?,?)", (material_id, reaction, source_id, record["family_key"], status, int(complete), fingerprint, canonical(record)))
                con.executemany("INSERT INTO structures VALUES (?,?,?,?,?)", structures)
                inserted += 1
        return {"inserted": inserted, "duplicates": duplicates}

    def summary(self, reaction="OER"):
        rows = self.query(reaction=reaction, limit=None)["items"]
        payload = {"materials": len(rows), "sources": len({r["source"]["id"] for r in rows}),
                "complete": sum(bool(r["complete_four_states"]) for r in rows),
                "data_complete": sum(not r["data_missing"] for r in rows),
                "data_missing": sum(bool(r["data_missing"]) for r in rows),
                "experimental": sum(r["experimental_status"] == "experimentally_verified" for r in rows),
                "computational": sum(r["experimental_status"] == "computational_only" for r in rows),
                "elements": sorted({s for r in rows for s in r["elements"]}, key=lambda s: atomic_numbers[s]),
                "status": "公开四态数据正在检索整理" if not rows else "已接入公开文献数据"}
        payload.update(self.facets(reaction))
        return payload

    @staticmethod
    def _haystack(record):
        source = record.get("source") or {}
        parts = [
            record.get("name"), record.get("formula"), record.get("family_key"),
            record.get("external_id"), record.get("electrolyte"), record.get("notes"),
            record.get("source_notes"), record.get("condition_basis"),
            " ".join(record.get("elements") or []),
            source.get("title"), source.get("doi"), source.get("dataset_doi"),
            source.get("url"), source.get("id"),
        ]
        cond = record.get("electrolyte_conditions") or []
        if isinstance(cond, list):
            parts.extend(str(x) for x in cond)
        else:
            parts.append(json.dumps(cond, ensure_ascii=False))
        return " ".join("" if v is None else str(v) for v in parts).casefold()

    @staticmethod
    def _in_range(value, lo, hi):
        if value is None:
            return False
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        if not isinstance(number, (int, float)) or number != number:
            return False
        if lo is not None and number < lo:
            return False
        if hi is not None and number > hi:
            return False
        return True

    @staticmethod
    def _electrolyte_match(record, needle):
        if not needle:
            return True
        tag = needle.strip().casefold()
        cond = record.get("electrolyte_conditions") or []
        tags = [str(x).casefold() for x in cond] if isinstance(cond, list) else [json.dumps(cond, ensure_ascii=False).casefold()]
        text = (record.get("electrolyte") or "").casefold()
        aliases = {
            "acidic": ("acidic", "酸性", "acid"),
            "alkaline": ("alkaline", "碱性", "alkali", "koh", "naoh"),
            "neutral": ("neutral", "中性"),
        }
        if tag in aliases:
            words = aliases[tag]
            return any(any(word in item for word in words) for item in tags + [text])
        return tag in text or any(tag in item for item in tags)

    @staticmethod
    def _ph_overlap(record, lo, hi):
        if lo is None and hi is None:
            return True
        pmin, pmax = record.get("pH_min"), record.get("pH_max")
        if pmin is None and pmax is None:
            return False
        try:
            pmin = float(pmin if pmin is not None else pmax)
            pmax = float(pmax if pmax is not None else pmin)
        except (TypeError, ValueError):
            return False
        if pmin > pmax:
            pmin, pmax = pmax, pmin
        if lo is not None and pmax < lo:
            return False
        if hi is not None and pmin > hi:
            return False
        return True

    def query(self, reaction="OER", elements=None, match="contains", offset=0, limit=50,
              q="", completeness="all", status="all", electrolyte="", source_id="",
              eta_min=None, eta_max=None, exp_eta_min=None, exp_eta_max=None,
              pH_min=None, pH_max=None, exclude_elements=None, exp_j=None, data_class=""):
        reactions = normalize_reactions(reaction)
        if match not in ("exact", "contains"):
            raise ValueError("公开材料反应类型或匹配方式无效")
        if completeness not in ("all", "complete", "missing"):
            raise ValueError("公开材料数据完整性筛选无效")
        if status not in ("all", "experimentally_verified", "computational_only", "unknown"):
            raise ValueError("实验/计算状态筛选无效")
        selected = set(elements or [])
        excluded = set(exclude_elements or [])
        if any(s not in atomic_numbers or s == "X" for s in selected | excluded):
            raise ValueError("元素符号无效")
        records = [row for code in reactions for row in self._records(code)]
        if data_class:
            if data_class not in DATA_CLASS_LABELS:
                raise ValueError("数据类型筛选无效")
            records = [r for r in records if data_class_of(r) == data_class]
        if exp_j is not None:
            records = [r for r in records if r.get("experimental_current_density_mA_cm2") is not None
                       and abs(float(r["experimental_current_density_mA_cm2"]) - float(exp_j)) < 1e-9]
        if selected:
            records = [r for r in records if (selected == set(r["elements"]) if match == "exact" else selected <= set(r["elements"]))]
        if excluded:
            records = [r for r in records if not (excluded & set(r["elements"]))]
        term = q.strip().casefold()
        if term:
            records = [r for r in records if term in self._haystack(r)]
        if completeness != "all":
            records = [r for r in records if bool(r["data_missing"]) == (completeness == "missing")]
        if status != "all":
            records = [r for r in records if r.get("experimental_status") == status]
        if electrolyte:
            records = [r for r in records if self._electrolyte_match(r, electrolyte)]
        if source_id:
            records = [r for r in records if (r.get("source") or {}).get("id") == source_id or r.get("source_id") == source_id]
        if eta_min is not None or eta_max is not None:
            records = [r for r in records if self._in_range(r.get("overpotential_V"), eta_min, eta_max)]
        if exp_eta_min is not None or exp_eta_max is not None:
            records = [r for r in records if self._in_range(r.get("experimental_overpotential_V"), exp_eta_min, exp_eta_max)]
        if pH_min is not None or pH_max is not None:
            records = [r for r in records if self._ph_overlap(r, pH_min, pH_max)]
        # Sort the whole result before pagination: data types and measurement bases form separate groups
        # (never interleaved); within a group complete data stays together. Bulk high-throughput sets go last.
        records.sort(key=lambda r: (group_key(r)[0], group_key(r)[1], bool(r["data_missing"]), group_key(r)[2], r["name"].casefold(), r["id"]))
        return {"total": len(records), "items": records[offset:] if limit is None else records[offset:offset+limit]}

    def _records(self, reaction):
        """Parsed payloads cached per file version (path, mtime, size); callers must not mutate them."""
        st = self.path.stat()
        key = (str(self.path), st.st_mtime_ns, st.st_size, reaction)
        hit = _RECORD_CACHE.get(key)
        if hit is None:
            with self.connect() as con:
                rows = con.execute("SELECT payload FROM materials WHERE reaction=? ORDER BY id", (reaction,)).fetchall()
            hit = tuple(json.loads(r[0]) for r in rows)
            for k in [k for k in _RECORD_CACHE if k[0] == key[0] and k[3] == reaction]:
                _RECORD_CACHE.pop(k, None)
            _RECORD_CACHE[key] = hit
        return hit

    def facets(self, reaction="OER"):
        rows = self.query(reaction=reaction, limit=None)["items"]
        sources = {}
        electrolytes = {"acidic": 0, "alkaline": 0, "neutral": 0}
        for record in rows:
            source = record.get("source") or {}
            sid = source.get("id") or record.get("source_id") or ""
            if sid not in sources:
                sources[sid] = {"id": sid, "title": source.get("title") or sid, "n": 0}
            sources[sid]["n"] += 1
            for tag in electrolytes:
                if self._electrolyte_match(record, tag):
                    electrolytes[tag] += 1
        etas = [r["overpotential_V"] for r in rows if r.get("overpotential_V") is not None]
        exp = [r["experimental_overpotential_V"] for r in rows if r.get("experimental_overpotential_V") is not None]
        classes, by_j = {}, {}
        for r in rows:
            c = data_class_of(r)
            classes[c] = classes.get(c, 0) + 1
            if r.get("experimental_overpotential_V") is not None:
                j = r.get("experimental_current_density_mA_cm2")
                b = by_j.setdefault(j, {"current_density_mA_cm2": j, "label": measurement_basis_of(r), "n": 0, "min": None, "max": None})
                v = r["experimental_overpotential_V"]
                b["n"] += 1
                b["min"] = v if b["min"] is None else min(b["min"], v)
                b["max"] = v if b["max"] is None else max(b["max"], v)
        return {
            "data_classes": [{"data_class": k, "label": DATA_CLASS_LABELS[k], "n": classes.get(k, 0)} for k in ("experimental", "computed")],
            "experimental_eta_by_current_density": sorted(by_j.values(), key=lambda b: (b["current_density_mA_cm2"] is None, b["current_density_mA_cm2"] or 0)),
            "source_list": sorted(sources.values(), key=lambda x: (-x["n"], x["title"] or "")),
            "electrolytes": [{"tag": k, "n": v} for k, v in electrolytes.items()],
            "eta": {"min": min(etas) if etas else None, "max": max(etas) if etas else None, "n": len(etas)},
            "experimental_eta": {"min": min(exp) if exp else None, "max": max(exp) if exp else None, "n": len(exp)},
        }

    def detail(self, material_id):
        with self.connect() as con:
            row = con.execute("SELECT payload FROM materials WHERE id=?", (material_id,)).fetchone()
            if not row:
                raise KeyError("未找到公开材料")
            record = json.loads(row[0])
        cls = data_class_of(record)
        return {**record, "data_class": cls, "data_class_label": DATA_CLASS_LABELS[cls],
                "measurement_basis": measurement_basis_of(record)}

    def structure(self, material_id, state):
        with self.connect() as con:
            row = con.execute("SELECT raw,geometry,format FROM structures WHERE material_id=? AND state=?", (material_id, state)).fetchone()
            if not row:
                raise KeyError("该态结构尚未公开或未取得")
            return json.loads(zlib.decompress(row["geometry"])), zlib.decompress(row["raw"]), row["format"]

    def csv_export(self, **filters):
        rows = self.query(**{**filters, "offset": 0, "limit": None})["items"]
        fields = ["id", "name", "formula", "elements", "experimental_status", "DeltaG1_eV", "DeltaG2_eV", "DeltaG3_eV", "DeltaG4_eV", "theoretical_overpotential_V", "experimental_overpotential_V", "experimental_current_density_mA_cm2", "measurement_basis", "data_class", "data_class_label", "source_title", "source_url", "doi", "completeness", "data_status", "missing_fields"]
        out = io.StringIO(newline="")
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            steps = r["steps_dG_eV"] or [None]*4
            writer.writerow({**{k: r.get(k) for k in ("id", "name", "formula", "experimental_status", "experimental_overpotential_V", "completeness")},
                             "elements": "+".join(r["elements"]), **{f"DeltaG{i+1}_eV": v for i, v in enumerate(steps)},
                             "theoretical_overpotential_V": r["overpotential_V"], "source_title": r["source"]["title"],
                             "experimental_current_density_mA_cm2": r.get("experimental_current_density_mA_cm2"),
                             "measurement_basis": measurement_basis_of(r), "data_class": data_class_of(r),
                             "data_class_label": DATA_CLASS_LABELS[data_class_of(r)],
                             "source_url": r["source"]["url"], "doi": r["source"].get("doi", ""),
                             "data_status": "数据缺失" if r["data_missing"] else "完整数据",
                             "missing_fields": "; ".join(r["missing_fields"])})
        return out.getvalue().encode("utf-8-sig")
