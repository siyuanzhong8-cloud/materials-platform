"""CHE energy calculations and content-derived method identities."""
import hashlib
import json
import math
import re

STATES = ("bare", "O", "OH", "OOH")
INITIAL_METHOD = {
    "name": "UMA CHE · 固定热力学修正（8997 首批）",
    "temperature_K": 298.15,
    "potential_V_RHE": 0.0,
    "correction_eV": {"OH": 0.35, "O": 0.05, "OOH": 0.40},
    "uma_gas_reference_eV": {"H2": -6.957121024657680, "H2O": -14.110342539548466},
    "reference_checkpoint_sha256": "b2673b85037b075674c25f55c34ffe1ff1e15db924be977b10a184765df0d5ce",
    "scope": "UMA 能量 + 文献固定 ΔZPE−TΔS 修正估算；非逐结构振动计算；不含显式溶剂；科学验收及最终几何检查未完成。",
    "source": "https://doi.org/10.1038/s41467-020-15231-x",
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def finite(value, name="数值"):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name}必须是有效数字") from None
    if not math.isfinite(result):
        raise ValueError(f"{name}不能是 NaN 或无穷大")
    return result


def normalize_method(value):
    if not isinstance(value, dict):
        raise ValueError("method.json 必须是 JSON 对象")
    try:
        params = {
            "temperature_K": finite(value["temperature_K"], "温度"),
            "potential_V_RHE": finite(value.get("potential_V_RHE", 0.0), "电位"),
            "correction_eV": {s: finite(value["correction_eV"][s], f"{s} 修正") for s in ("OH", "O", "OOH")},
            "uma_gas_reference_eV": {s: finite(value["uma_gas_reference_eV"][s], f"{s} 参考能") for s in ("H2", "H2O")},
            "reference_checkpoint_sha256": value["reference_checkpoint_sha256"].lower(),
        }
    except (KeyError, AttributeError) as error:
        raise ValueError(f"method.json 缺少有效的计算口径字段：{error}") from None
    if params["temperature_K"] != 298.15 or params["potential_V_RHE"] != 0.0:
        raise ValueError("首版仅支持 298.15 K、U=0 V 相对 RHE 的四步 4.92 eV 口径")
    if not re.fullmatch(r"[0-9a-f]{64}", params["reference_checkpoint_sha256"]):
        raise ValueError("请提供能量模型的 64 位 checkpoint SHA256")
    return {**value, **params, "id": digest(params)[:24],
            "name": str(value.get("name") or "CHE 固定修正"),
            "scope": str(value.get("scope") or "固定热力学修正估算；非逐结构振动计算；不含显式溶剂。")}


def adsorption_energies(energies, method):
    energies = {s: finite(energies[s], f"{s} 总能量") for s in STATES}
    h2 = method["uma_gas_reference_eV"]["H2"]
    water = method["uma_gas_reference_eV"]["H2O"]
    bare = energies["bare"]
    return {"OH": energies["OH"] - bare - water + 0.5 * h2,
            "O": energies["O"] - bare - water + h2,
            "OOH": energies["OOH"] - bare - 2 * water + 1.5 * h2}


def thermodynamics(delta_e, method):
    gs = {s: finite(delta_e[s], f"ΔE({s})") + finite(method["correction_eV"][s]) for s in ("OH", "O", "OOH")}
    steps = [gs["OH"], gs["O"] - gs["OH"], gs["OOH"] - gs["O"], 4.92 - gs["OOH"]]
    if not all(math.isfinite(v) for v in steps):
        raise ValueError("计算自由能超出有效数值范围")
    top = max(steps)
    return {"intermediates": gs, "steps": steps, "eta": top - 1.23,
            "limiting_steps": [i + 1 for i, v in enumerate(steps) if abs(v - top) < 1e-10]}
