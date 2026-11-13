"""Read-only validation of supplied tables and atomistic files."""
import csv
import hashlib
import io
import json
from collections import Counter
from pathlib import Path, PurePosixPath

import numpy as np
from ase.io import read

from backend.domain import STATES, finite

ENERGY_FILE = "01_relaxed_four_states.csv"
INDEX_FILE = "structure_index.csv"
ARCHIVE_FILE = "relaxed_four_states.tar.gz"
G_FILES = ("02_corrected_dG_with_step4.csv", "02_corrected_dG_with_overpotential.csv", "02_corrected_dG_overpotential.csv")
ACCEPTED_FILES = (ENERGY_FILE, INDEX_FILE, ARCHIVE_FILE, *G_FILES, "method.json", "manifest.json")


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def json_file(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("顶层必须为 JSON 对象")
        return value
    except (ValueError, UnicodeError) as error:
        raise ValueError(f"{Path(path).name} 不是有效 UTF-8 JSON：{error}") from None


def csv_rows(path):
    if Path(path).stat().st_size > 256 * 1024 * 1024:
        raise ValueError("单张 CSV 超过 256 MB，请拆分导入批次")
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError(f"{Path(path).name} 表头为空或重复")
        rows = list(reader)
    if any(None in row for row in rows):
        raise ValueError(f"{Path(path).name} 有列数不匹配的记录")
    return rows


def identity(row):
    site = (row.get("site_id") or "").strip()
    variant = (row.get("variant") or "").strip()
    if not site:
        raise ValueError("site_id 不能为空")
    if len(site) > 200 or len(variant) > 200:
        raise ValueError("site_id 或 variant 过长")
    return site, variant


def table_inputs(directory):
    directory = Path(directory)
    for name in (ENERGY_FILE, INDEX_FILE, ARCHIVE_FILE):
        if not (directory / name).is_file():
            raise ValueError(f"缺少 {name}")
    energies, by_row, keys = csv_rows(directory / ENERGY_FILE), {}, set()
    if not energies:
        raise ValueError("能量表没有构型记录")
    for r in energies:
        key = identity(r)
        number = (r.get("row") or "").strip()
        if not number or number in by_row or key in keys:
            raise ValueError("能量表的 row 或 site_id + variant 为空/重复")
        for s in STATES:
            finite(r.get(f"E_{s}_eV"), f"第 {number} 行 {s} 总能量")
        by_row[number] = r
        keys.add(key)
    indices = {}
    for r in csv_rows(directory / INDEX_FILE):
        number, state = r.get("row"), r.get("state")
        if number not in by_row or state not in STATES:
            raise ValueError("结构索引包含未知 row 或吸附态")
        erow = by_row[number]
        if r.get("site_id") != erow["site_id"] or ("variant" in r and (r.get("variant") or "") != (erow.get("variant") or "")):
            raise ValueError(f"第 {number} 行结构索引的位点标识不一致")
        name = r.get("archive_member", "")
        pure = PurePosixPath(name)
        if not name or pure.is_absolute() or ".." in pure.parts or "\\" in name or ":" in name:
            raise ValueError("结构包内路径无效")
        if name in indices:
            raise ValueError("结构索引包内路径重复")
        indices[name] = r
    indexed_pairs = [(r["row"], r["state"]) for r in indices.values()]
    if len(set(indexed_pairs)) != len(indexed_pairs):
        raise ValueError("结构索引 row + state 重复")
    expected = {(r, s) for r in by_row for s in STATES}
    if set(indexed_pairs) != expected:
        raise ValueError("每条构型必须有 bare、O、OH、OOH 四态结构索引")
    g_tables = {}
    energy_by_key = {identity(r): (number, r) for number, r in by_row.items()}
    for name in G_FILES:
        if (directory / name).is_file():
            gs = csv_rows(directory / name)
            g_rows = {identity(r): r for r in gs}
            if len(gs) != len(g_rows) or set(g_rows) != keys:
                raise ValueError(f"{name} 与能量表的位点/变体集合不一致")
            for key, row in g_rows.items():
                number, _ = energy_by_key[key]
                if (row.get("row") or "").strip() != number:
                    raise ValueError(f"{name} 的 row 与能量表的位点/变体对应关系不一致")
            g_tables[name] = g_rows
    return by_row, indices, g_tables


def geometry_bytes(data, state, energy, sha):
    try:
        frames = read(io.StringIO(data.decode("utf-8")), format="extxyz", index=":")
    except Exception as error:
        raise ValueError(f"无法读取 extxyz 结构：{error}") from None
    if len(frames) != 1:
        raise ValueError("每个结构文件必须恰好包含一帧")
    atoms = frames[0]
    if len(atoms) < 1 or len(atoms) > 10000:
        raise ValueError("结构原子数必须在 1 到 10000 之间")
    if not np.isfinite(atoms.positions).all() or not np.isfinite(atoms.cell.array).all():
        raise ValueError("坐标/晶胞存在非有限数值")
    if atoms.pbc.any() and atoms.cell.rank != 3:
        raise ValueError("周期结构缺少完整晶胞")
    symbols = atoms.get_chemical_symbols()
    if "X" in symbols:
        raise ValueError("结构包含未知元素")
    result = {"state": state, "atoms": [{"elem": s, "x": float(p[0]), "y": float(p[1]), "z": float(p[2])} for s, p in zip(symbols, atoms.positions)],
              "cell": atoms.cell.array.tolist(), "pbc": atoms.pbc.tolist(), "sha256": sha,
              "n_atoms": len(atoms), "energy_eV": energy}
    return atoms, result


def check_state_composition(base, counts, state):
    expected = Counter(base)
    expected.update({"bare": {}, "O": {"O": 1}, "OH": {"O": 1, "H": 1}, "OOH": {"O": 2, "H": 1}}[state])
    if dict(expected) != dict(counts):
        raise ValueError(f"{state} 态原子组成与裸基底及指定吸附物不一致")
