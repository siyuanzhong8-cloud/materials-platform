"""Portable data and lossless original-structure downloads."""
import csv
import io
import json
import zipfile


def csv_export(store, filters):
    rows = store.query(**{**filters, "offset": 0, "limit": None})["items"]
    fields = ["config_id", "site_id", "variant", "family_key", "formula", "formula_full", "n_atoms",
              "DeltaG_step1_eV", "DeltaG_step2_eV", "DeltaG_step3_eV", "DeltaG_step4_eV", "overpotential_V",
              "limiting_steps", "method_id", "batch_name", "review_status"]
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    for r in rows:
        writer.writerow({"config_id": r["id"], **{k: r[k] for k in ("site_id", "variant", "family_key", "formula", "formula_full", "n_atoms", "method_id", "batch_name", "review_status")},
                         **{f"DeltaG_step{i}_eV": r[f"g{i}"] for i in range(1, 5)}, "overpotential_V": r["eta"],
                         "limiting_steps": ",".join(str(s) for s in r["limiting_steps"])})
    return out.getvalue().encode("utf-8-sig")


def structures_export(store, ids):
    ids = list(dict.fromkeys(ids))
    if not ids or len(ids) > 10000:
        raise ValueError("请选择 1 至 10000 条构型")
    out = io.BytesIO()
    index, records, methods = [], [], {}
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for config_id in ids:
            detail = store.detail(config_id)
            methods[detail["method_id"]] = detail["method"]
            records.append({k: v for k, v in detail.items() if k != "structures"})
            for state, metadata in detail["structures"].items():
                _, raw = store.structure(config_id, state)
                member = f"structures/{config_id}/{state}.extxyz"
                archive.writestr(member, raw)
                index.append({"config_id": config_id, "site_id": detail["site_id"], "variant": detail["variant"],
                              "state": state, "archive_member": member, **metadata})
        # Explicitly replace the source archive locator with the locator in this ZIP.
        for r in index:
            r["source_archive_member"] = r["archive_member"]
            r["archive_member"] = f"structures/{r['config_id']}/{r['state']}.extxyz"
        buf = io.StringIO(newline="")
        writer = csv.DictWriter(buf, fieldnames=list(index[0]))
        writer.writeheader()
        writer.writerows(index)
        archive.writestr("structure_index.csv", buf.getvalue().encode("utf-8-sig"))
        archive.writestr("records.json", json.dumps(records, ensure_ascii=False, indent=2).encode())
        archive.writestr("methods.json", json.dumps(methods, ensure_ascii=False, indent=2).encode())
        archive.writestr("README.txt", "结构为原始 extxyz，坐标/晶胞/属性未改写。编号和方法对应关系见索引及 records.json。结果为 UMA + 固定修正估算，请保留原始复核状态。".encode("utf-8"))
    return out.getvalue()
