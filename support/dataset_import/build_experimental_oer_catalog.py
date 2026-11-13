"""Build a literature-store import catalog (schema_version=1) for two experimental OER datasets.

ACE-I (TRI/Caltech, CC BY 4.0): overpotential at 3 mA/cm^2, pH 13.  -> experimental_current_density_mA_cm2 = 3
Caltech AcidOER Mn-Sb-Sn-Ti-Co (CC0): current density at 1.79 V vs RHE in 1 M H2SO4 -> experimental_activity (no eta)
Usage: python build_experimental_oer_catalog.py RAW_CATALYSIS_DIR OUT_JSON
The ACE-I pickle is read with an allow-list unpickler (numpy arrays only).
"""
import csv, hashlib, json, os, pickle, sys
import numpy as np

def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()

class NumpyOnly(pickle.Unpickler):
    def find_class(self, m, n):
        if m.startswith("numpy") and n in ("ndarray", "_reconstruct", "dtype"): return super().find_class(m, n)
        raise pickle.UnpicklingError(f"blocked {m}.{n}")

def frac_formula(comp):
    parts = [f"{e}{round(v, 3):g}" for e, v in sorted(comp.items(), key=lambda kv: (-kv[1], kv[0])) if v > 0]
    return "(" + "".join(parts) + ")Ox" if len(parts) > 1 else parts[0].rstrip("1").rstrip(".") + "Ox"

# Column -> element order of the comp vector = inkjet print-channel order A..F. Not stated inside the pickle;
# taken from the MEAD (Caltech HTE) record titles for each element set and checked against the paper (Table 1 counts).
ACEI_PLATES = {
    "3496": ("A", ["Ni", "Fe", "La", "Ce", "Co", "Mn"]),
    "3851": ("B", ["Ni", "Fe", "Cu", "Mn", "Co", "Ta"]),
    "3860": ("C", ["Sn", "Fe", "Cu", "Mn", "Co", "Ta"]),
    "4098": ("D", ["Sn", "Sb", "Mn", "Ni", "Co", "Ca"]),
}
ACEI_PAPER = "https://pubs.rsc.org/en/content/articlehtml/2020/sc/c9sc05999g"
ACID_PAPER = "https://pubs.rsc.org/en/content/articlehtml/2023/ta/d3ta04899c"

def main(raw, out):
    acei_file = os.path.join(raw, "ace-i", "tri_data_share.pck")
    acei_src = {
        "id": "tri_ace_i_2020", "data_class": "experimental", "data_class_label": "实验数据",
        "title": "OER catalyst data for active learning benchmarking study (ACE-I; Rohr et al., Chem. Sci. 2020)",
        "url": "https://data.caltech.edu/records/7b106-nf257", "data_url": "https://data.caltech.edu/records/7b106-nf257/files/tri_data_share.pck",
        "landing_url": "https://data.matr.io/oer-catalysis/", "dataset_doi": "10.22002/D1.1345", "doi": "10.1039/C9SC05999G",
        "license": "CC-BY-4.0",
        "attribution": "Gregoire, J.; Rohr, B.; Stein, H. S.; Guevarra, D.; Wang, Y. et al. OER catalyst data for active learning benchmarking study. CaltechDATA (2020), doi:10.22002/D1.1345; Rohr, B. et al. Chem. Sci. 2020, 11, 2696-2706, doi:10.1039/C9SC05999G. CC BY 4.0.",
        "method": "High-throughput inkjet-printed metal oxides (400 °C, 10 h), 2 h parallel conditioning, scanning droplet cell; FOM = overpotential at 3 mA/cm² (1 mm² geometric area), pH 13 (0.1 M NaOH + 0.25 M Na2SO4)",
        "downloaded_files": [{"path": "raw_datasets/catalysis/ace-i/tri_data_share.pck", "sha256": sha(acei_file)}],
        "notes": ["Measurement basis: η@3 mA/cm² — NOT comparable with η@10 mA/cm² values.",
                  "Plate 3875 (5th key in the pickle) is not described in the paper; its elements are unknown, so it was not imported.",
                  "Column→element mapping is not stored in the pickle; inferred from MEAD print-channel order and verified against the paper's Table 1 top-1% cation counts."],
        "downloaded": "2026-11-13", "high_throughput": True,
    }
    acid_dir = os.path.join(raw, "caltech-acidoer")
    acid_src = {
        "id": "caltech_acidoer_mnsbsntico_2023", "data_class": "experimental", "data_class_label": "实验数据",
        "title": "High throughput oxygen evolution reaction data for oxide catalysts containing Mn, Sb, Sn, Ti, and Co (Zhou & Gregoire 2023)",
        "url": "https://data.caltech.edu/records/tg041-j4g80", "data_url": "https://data.caltech.edu/records/tg041-j4g80/files/AcidOER-MnSbSnTiCo.zip",
        "dataset_doi": "10.22002/tg041-j4g80", "doi": "10.1039/d3ta04899c", "license": "CC0-1.0",
        "attribution": "Zhou, L.; Gregoire, J. M. (2023). High throughput oxygen evolution reaction data for oxide catalysts containing Mn, Sb, Sn, Ti, and Co. CaltechDATA, doi:10.22002/tg041-j4g80 (CC0); Zhou, L. et al. J. Mater. Chem. A 2023, 11, 25262-25267.",
        "method": "Reactive co-sputtered oxide libraries annealed 700 °C in air; scanning droplet cell in O2-saturated 1 M H2SO4; CV 1.23–1.79 V vs RHE at 20 mV/s, 200 s CA, CV; geometric area 0.02 cm²",
        "downloaded_files": [{"path": "raw_datasets/catalysis/caltech-acidoer/data/OER/oer_all_foms.csv", "sha256": sha(os.path.join(acid_dir, "data/OER/oer_all_foms.csv"))},
                             {"path": "raw_datasets/catalysis/caltech-acidoer/data/OER/oer_raw_dt.csv", "sha256": sha(os.path.join(acid_dir, "data/OER/oer_raw_dt.csv"))}],
        "notes": ["Activity metric is current density at fixed potential (mA/cm²), not an overpotential. No η, no Tafel slope stored.",
                  "Plate 5029 (Mn–Sb–Co) is the ESI library (Fig. S3); plates 5411–5414 are the 182 main-text compositions."],
        "downloaded": "2026-11-13", "high_throughput": True,
    }
    mats = []
    d = NumpyOnly(open(acei_file, "rb"), encoding="latin1").load()
    for plate, (label, els) in ACEI_PLATES.items():
        v = d[plate]
        for comp_row, fom, sno, run in zip(v["comp"], v["fom"], v["sno"], v["runint"]):
            comp = {e: round(float(x), 4) for e, x in zip(els, comp_row) if x > 1e-9}
            formula = frac_formula(comp)
            mats.append({
                "id": f"acei_{plate}_s{int(sno)}", "source_id": acei_src["id"],
                "name": f"{formula} | ACE-I {label}（plate {plate}）· sample {int(sno)}",
                "formula": formula, "elements": sorted(set(comp) | {"O"}), "metal_composition": comp,
                "element_basis": "Nominal metal atomic fractions of the inkjet-printed precursor (10 at% grid); oxide after calcination, O stoichiometry not measured",
                "composition_system": "–".join(els), "plate_id": plate, "plate_label": label, "sample_no": int(sno), "run_int": int(run),
                "experimental_status": "experimentally_verified", "experimental_evidence_url": ACEI_PAPER,
                "experimental_evidence_scope": "Every composition was measured in the source high-throughput experiment.",
                "experimental_outcome": "measurement_reported",
                "experimental_overpotential_V": round(float(fom), 6), "experimental_current_density_mA_cm2": 3,
                "experimental_overpotential_raw": {"source_key": f"data['{plate}']['fom']", "unit": "V", "value": float(fom),
                                                   "definition": "overpotential at 3 mA/cm² (geometric, 1 mm² sample)"},
                "measurement_basis": "η@3 mA/cm²", "data_class": "experimental", "data_class_label": "实验数据", "high_throughput": True,
                "electrolyte": "0.1 M NaOH + 0.25 M Na2SO4 (pH 13)", "electrolyte_conditions": ["alkaline"],
                "electrolyte_evidence_url": ACEI_PAPER, "pH_min": 13, "pH_max": 13,
                "experimental_conditions": {"electrolyte_identity": "0.1 M NaOH + 0.25 M Na2SO4", "geometric_area_mm2": 1,
                                            "conditioning_h": 2, "calcination": "400 °C, 10 h", "synthesis": "inkjet printing",
                                            "cell": "scanning droplet cell", "temperature_K": None},
                "condition_basis": "Rohr et al. Chem. Sci. 2020 (Experimental): η at 3 mA/cm² per 1 mm² geometric area, pH 13, after 2 h accelerated ageing.",
                "method": {"type": "high_throughput_experiment", "technique": "scanning droplet cell", "metric": "η@3 mA/cm²"},
                "completeness": "实验测量（无结构、无ΔG）",
                "notes": ["η@3 mA/cm²; do not compare directly with η@10 mA/cm².",
                          "Element order of the composition vector inferred from MEAD print-channel order (see source notes)."],
            })
    ca_E = {}
    import pandas as pd
    r = pd.read_csv(os.path.join(acid_dir, "data/OER/oer_raw_dt.csv"), usecols=["tech", "erhe", "plate", "t.s", "Ewe.V"])
    r = r[(r.tech == "CA") & (r["t.s"] > 5)]
    for plate, g in r.groupby("plate"):
        ca_E[str(plate)] = round(float((g["Ewe.V"] - g["erhe"]).median()), 3)
    with open(os.path.join(acid_dir, "data/OER/oer_all_foms.csv"), newline="") as f:
        for i, row in enumerate(csv.DictReader(f)):
            plate = row["plate"]
            comp = {e: float(row[e]) for e in ("Mn", "Sb", "Sn", "Ti", "Co") if float(row[e]) > 0}
            formula = frac_formula(comp)
            num = lambda k: (round(float(row[k]), 4) if row.get(k) not in (None, "", "nan", "NaN") else None)
            ca = ca_E.get(plate)
            act = [
                {"metric": "J@1.79 V vs RHE（首次 CV）", "key": "Jmax_pre", "value": num("Jmax_pre"), "unit": "mA/cm²", "potential_V_RHE": 1.79, "primary": True,
                 "definition": "Geometric current density at 1.79 V vs RHE in the initial CV (paper's activity metric)"},
                {"metric": "J@1.79 V vs RHE（CA 后 CV）", "key": "Jmax_post", "value": num("Jmax_post"), "unit": "mA/cm²", "potential_V_RHE": 1.79,
                 "definition": "Same, in the CV after the 200 s chronoamperometry"},
                {"metric": f"CA J@20 s（{ca} V vs RHE）", "key": "J_t20", "value": num("J_t20"), "unit": "mA/cm²", "potential_V_RHE": ca,
                 "definition": "Chronoamperometry current density averaged over 1 s at t=20 s"},
                {"metric": f"CA J@200 s（{ca} V vs RHE）", "key": "J_t200", "value": num("J_t200"), "unit": "mA/cm²", "potential_V_RHE": ca,
                 "definition": "Chronoamperometry current density averaged over the final 1 s at t=200 s"},
            ]
            mats.append({
                "id": f"acidoer_{plate}_row{i + 2}", "source_id": acid_src["id"],
                "name": f"{formula} | AcidOER plate {plate}（{row['elements']}）· csv row {i + 2}",
                "formula": formula, "elements": sorted(set(comp) | {"O"}), "metal_composition": comp,
                "element_basis": "Metal fractions from XRF interpolation (O not measured); rutile/oxide after 700 °C anneal",
                "composition_system": row["elements"], "plate_id": plate,
                "experimental_status": "experimentally_verified", "experimental_evidence_url": ACID_PAPER,
                "experimental_evidence_scope": "Each composition measured by scanning droplet cell in the source dataset.",
                "experimental_outcome": "measurement_reported",
                "experimental_overpotential_V": None, "experimental_current_density_mA_cm2": None,
                "experimental_activity": act, "measurement_basis": "J@1.79 V vs RHE", "data_class": "experimental", "data_class_label": "实验数据",
                "high_throughput": True, "electrolyte": "1 M H2SO4, O2-saturated (continuous flow)", "electrolyte_conditions": ["acidic"],
                "electrolyte_evidence_url": ACID_PAPER, "pH_min": None, "pH_max": None,
                "experimental_conditions": {"electrolyte_identity": "1 M H2SO4", "geometric_area_cm2": 0.02, "CV_range_V_RHE": "1.23–1.79",
                                            "CV_scan_rate_mV_s": 20, "CA_duration_s": 200, "CA_potential_V_RHE_from_raw": ca,
                                            "anneal": "700 °C in air", "synthesis": "reactive co-sputtering", "temperature_K": None},
                "condition_basis": "Zhou et al. J. Mater. Chem. A 2023 + ESI. Numerical pH not inferred from molarity. CA potential is read from the raw file (Ewe − erhe); paper states 1.72 V vs RHE, raw plate 5413 shows ≈1.79 V.",
                "method": {"type": "high_throughput_experiment", "technique": "scanning droplet cell", "metric": "J@1.79 V vs RHE"},
                "completeness": "实验测量（无结构、无ΔG）",
                "notes": ["Activity is a current density at fixed potential, not an overpotential; it is not entered into the overpotential filters.",
                          "Tafel slope not computed: CVs are uncorrected for iR and capacitive current; not straightforward."],
            })
    cat = {"schema_version": 1, "reaction": "OER", "sources": [acei_src, acid_src], "materials": mats}
    json.dump(cat, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    from collections import Counter
    print(Counter(m["source_id"] for m in mats))

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
