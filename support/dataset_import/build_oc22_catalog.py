"""Build data/oc22_catalog.sqlite from the official OC22 IS2RE-Total LMDBs + oc22_metadata.pkl.

Run with an isolated venv that has torch(cpu)+lmdb:  python build_oc22_catalog.py RAW_DIR OUT_SQLITE
Data class: computed (DFT, VASP PBE+U). Never mixed with UMA predictions or experimental values.
LMDB pickles are loaded with an allow-list unpickler (torch tensors + a stub Data class only).
"""
import glob, io, json, os, pickle, sqlite3, sys, collections
import lmdb, torch

ELEM = "X H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi".split()

class Data:
    def __setstate__(self, s): self.__dict__.update(s if isinstance(s, dict) else {})
ALLOW = {('torch_geometric.data.data', 'Data'), ('torch._utils', '_rebuild_tensor_v2'), ('torch.storage', '_load_from_bytes'), ('collections', 'OrderedDict')}
class U(pickle.Unpickler):
    def find_class(self, m, n):
        if (m, n) not in ALLOW: raise pickle.UnpicklingError(f"blocked {m}.{n}")
        if n == 'Data': return Data
        if n == '_load_from_bytes': return lambda b: torch.load(io.BytesIO(b), weights_only=True)
        return super().find_class(m, n)

def ads_class(sym, nads):
    if not nads: return "clean"
    return {"O": "O", "OH": "OH", "HO": "OH", "HO2": "OOH", "O2H": "OOH", "OOH": "OOH"}.get(sym or "", "other")

def main(raw, out):
    meta = pickle.load(open(os.path.join(raw, "oc22_metadata.pkl"), "rb"))
    if os.path.exists(out): raise SystemExit("refusing to overwrite existing " + out)
    con = sqlite3.connect(out)
    con.executescript("""
    CREATE TABLE datasets(id TEXT PRIMARY KEY, title TEXT, data_class TEXT NOT NULL, data_class_label TEXT NOT NULL,
      code TEXT, functional TEXT, hubbard_u TEXT, dispersion TEXT, labels TEXT, license TEXT, attribution TEXT,
      url TEXT, paper_doi TEXT, download_url TEXT, md5 TEXT, downloaded TEXT, n_structures INTEGER, elements TEXT, notes TEXT);
    CREATE TABLE systems(sid INTEGER NOT NULL, split TEXT NOT NULL, dataset_id TEXT NOT NULL REFERENCES datasets(id),
      data_class TEXT NOT NULL DEFAULT 'computed', functional TEXT NOT NULL,
      bulk_id TEXT, bulk_formula TEXT, miller_index TEXT, adsorbate TEXT, adsorbate_class TEXT, nads INTEGER,
      slab_sid INTEGER, traj_id TEXT, natoms INTEGER, elements TEXT, formula TEXT,
      total_energy_eV REAL, energy_basis TEXT, lmdb_file TEXT, lmdb_key TEXT, PRIMARY KEY(dataset_id, split, sid));
    CREATE INDEX sys_ads ON systems(adsorbate_class); CREATE INDEX sys_bulk ON systems(bulk_id); CREATE INDEX sys_slab ON systems(slab_sid);
    """)
    rows, allel = [], set()
    base = os.path.join(raw, "is2res_total_train_val_test_lmdbs/data/oc22/is2re-total")
    for split in sorted(os.listdir(base)):
        for f in sorted(glob.glob(os.path.join(base, split, "*.lmdb"))):
            env = lmdb.open(f, subdir=False, readonly=True, lock=False, readahead=False)
            with env.begin() as t:
                for k, v in t.cursor():
                    if k == b"length": continue
                    d = U(io.BytesIO(v)).load().__dict__
                    z = d["atomic_numbers"].long().tolist()
                    cnt = collections.Counter(ELEM[i] for i in z)
                    els = sorted(cnt); allel |= set(els)
                    formula = "".join(f"{e}{cnt[e] if cnt[e] > 1 else ''}" for e in sorted(cnt))
                    sid = int(d["sid"]); m = meta.get(sid, {})
                    nads = m.get("nads", d.get("nads"))
                    e = d.get("y_relaxed")
                    rows.append((sid, split, "oc22_is2re_total", "computed", "PBE+U", m.get("bulk_id"), m.get("bulk_symbols"),
                                 json.dumps(list(m["miller_index"])) if m.get("miller_index") else None,
                                 m.get("ads_symbols"), ads_class(m.get("ads_symbols"), nads), nads, m.get("slab_sid"), m.get("traj_id"),
                                 int(d["natoms"]), "+".join(els), formula, float(e) if e is not None else None,
                                 "DFT total energy of relaxed structure (eV), not adsorption energy",
                                 os.path.relpath(f, raw), k.decode()))
    con.executemany("INSERT INTO systems VALUES (" + ",".join("?" * 20) + ")", rows)
    con.execute("INSERT INTO datasets VALUES (" + ",".join("?" * 19) + ")", (
        "oc22_is2re_total", "Open Catalyst 2022 (OC22) IS2RE-Total / IS2RS", "computed", "计算数据",
        "VASP", "PBE+U (spin-polarized; Hubbard U per Materials Project)", "yes (MP U values for transition-metal oxides)", "none",
        "relaxed total energy (y_relaxed), initial + relaxed positions; no forces in IS2RE set",
        "CC-BY-4.0", "Tran, R. et al. The Open Catalyst 2022 (OC22) Dataset and Challenges for Oxide Electrocatalysts. ACS Catal. 2023, 13, 3066-3084. Data: Meta FAIR Chemistry, CC BY 4.0.",
        "https://fair-chem.github.io/catalysts/datasets/oc22.html", "10.1021/acscatal.2c05426",
        "https://dl.fbaipublicfiles.com/opencatalystproject/data/oc22/is2res_total_train_val_test_lmdbs.tar.gz",
        "b35dc24e99ef3aeaee6c5c949903de94", "2026-11-13", len(rows), "+".join(sorted(allel)),
        "Test splits have no public energies. Energies are total DFT energies; adsorption energies need consistent gas references and are not computed here. Not combined with UMA predictions or experimental data."))
    con.commit(); con.close()
    print(len(rows))

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
