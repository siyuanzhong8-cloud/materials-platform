"""Compute silicon-end groups and atom-count linker descriptors from catalog SMILES."""
from __future__ import annotations

import argparse
from collections import Counter, deque
import json
from pathlib import Path

from rdkit import Chem

# Each query describes the functional group itself. Mapped atoms, when supplied,
# exclude context atoms used only to recognize the group.
FUNCTIONS = {
    "氨基": ("[N;X3;!$(N-[C,S,P]=[O,S]);!$(N-[Si]);!$(N-[N]=*):1]", "[N:1]-C(=N)N"),
    "氨基甲酸酯": ("[N][C](=[O])[O]",),
    "丙烯酸酯": ("[CH2]=[CH][C](=[O])[O]",),
    "丙烯酰胺": ("[CH2]=[CH][C](=[O])[N]",),
    "甲基丙烯酰胺": ("[CH2]=[C]([CH3])[C](=[O])[N]",),
    "乙烯基": ("[Si]-[CH:1]=[CH2:2]",),
    "烯丙基": ("[Si]-[CH2]-[CH:1]=[CH2:2]", "[N]-[CH2]-[CH:1]=[CH2:2]"),
    "其他烯基": ("[C]=[C]",),
    "二硫/多硫": ("[S][S][S][S]", "[S][S][S]", "[S][S]"),
    "芳基/芳烷基": ("[a;r]",),
    "含氟有机基": ("[#6][F:1]",),
    "卤代有机基": ("[#6][Cl,Br,I:1]",),
    "卤代酯": ("[O][C](=[O])[CX4]([Br,Cl,I])",),
    "环氧": ("[O;r3]1[C;r3][C;r3]1",),
    "甲基丙烯酸酯": ("[CH2]=[C]([CH3])[C](=[O])[O]",),
    "磷功能基": ("[P](=[O])([O])[O]", "[P]"),
    "硫醚": ("[#6][S;X2:1][#6]",),
    "硫氰酸酯": ("[S][C]#[N]",),
    "硫代羧酸酯": ("[S][C](=[O])",),
    "硫脲": ("[N][C](=[S])[N]",),
    "醚基": ("[#6][O;X2:1][#6]",),
    "硅醚": ("[Si][O:1][#6]",),
    "脲基": ("[N][C](=[O])[N]",),
    "羟基": ("[CX4][O;H1:1]",),
    "氰基": ("[C]#[N]",),
    "巯基": ("[S;H1]",),
    "醛基": ("[CH1]=[O]",),
    "酸酐": ("[C](=[O])[O][C](=[O])",),
    "酰胺": ("[N][C](=[O])",),
    "异氰酸酯": ("[N]=[C]=[O]",),
    "不饱和二羧酸酯": ("[O][C](=[O])[C]=[C][C](=[O])[O]",),
    "磺酰叠氮": ("[S](=[O])(=[O])[#7]~[#7+]~[#7-]",),
    "叠氮": ("[#7]~[#7+]~[#7-]",),
    "亚胺": ("[N;X2]=[C;X3]",),
    "酮基": ("[#6][CX3:1](=[O:2])[#6]",),
    "酯基": ("[CX3](=[O])[O;H0][#6]",),
    "磺酰氯": ("[S](=[O])(=[O])[Cl]",),
}
FUNCTIONS = {name: tuple(Chem.MolFromSmarts(pattern) for pattern in patterns)
             for name, patterns in FUNCTIONS.items()}
assert all(pattern is not None for patterns in FUNCTIONS.values() for pattern in patterns)

# Correct a small number of broad legacy labels where a specific group is present.
FUNCTION_OVERRIDES = {
    9857164: "甲基丙烯酰胺", 118497: "酰胺", 6436929: "不饱和二羧酸酯",
    16744148: "卤代酯", 53230135: "卤代酯", 118718: "硫氰酸酯",
    12135713: "硫代羧酸酯", 3036295: "硫脲", 22045740: "磺酰叠氮",
    110353: "磺酰叠氮", 18519390: "叠氮", 46779110: "叠氮",
    53422637: "硅醚", 11324143: "亚胺", 58986855: "烯丙基",
    11217949: "环状氮硅烷", 71225000: "环状氮硅烷",
}
OXYGEN_GROUPS = tuple((label, Chem.MolFromSmarts(smarts)) for label, smarts in (
    ("甲氧基", "[Si]-[O;X2]-[CH3]"),
    ("乙氧基", "[Si]-[O;X2]-[CH2]-[CH3]"),
    ("异丙氧基", "[Si]-[O;X2]-[CH]([CH3])[CH3]"),
    ("叔丁氧基", "[Si]-[O;X2]-[C;H0]([CH3])([CH3])[CH3]"),
    ("2-甲氧基乙氧基", "[Si]-[O;X2]-[CH2]-[CH2]-[O;X2]-[CH3]"),
    ("2-(2-甲氧基乙氧基)乙氧基", "[Si]-[O;X2]-[CH2]-[CH2]-[O;X2]-[CH2]-[CH2]-[O;X2]-[CH3]"),
    ("乙酰氧基", "[Si]-[O;X2]-[C](=[O])-[CH3]"),
    ("酰氧基", "[Si]-[O;X2]-[C](=[O])-[#6]"),
    ("芳氧基", "[Si]-[O;X2]-[c]"),
    ("烯氧基", "[Si]-[O;X2]-[C]=[C]"),
    ("肟氧基", "[Si]-[O;X2]-[N]=[C]"),
))

def silicon_sites(mol):
    specific = {}
    for label, pattern in OXYGEN_GROUPS:
        for match in mol.GetSubstructMatches(pattern):
            specific.setdefault(match[:2], label)
    sites = []
    blocked = set()
    for atom in mol.GetAtoms():
        if atom.GetAtomicNum() != 14:
            continue
        index = atom.GetIdx()
        groups = Counter()
        for neighbor in atom.GetNeighbors():
            other = neighbor.GetIdx()
            element = neighbor.GetSymbol()
            label = specific.get((index, other))
            bond = mol.GetBondBetweenAtoms(index, other)
            if not label and element in {"Cl", "Br", "F", "I"}:
                label = "Si–" + element
            if not label and element == "N" and bond.IsInRing():
                label = "Si–N（环状）"
            if not label and element == "O" and any(a.GetAtomicNum() == 6 for a in neighbor.GetNeighbors()):
                label = "其他烷氧基"
            if label:
                groups[label] += 1
                blocked.add(frozenset((index, other)))
        sites.append((index, groups))
    return sites, blocked

def functional_atoms(mol, group):
    if group == "芳基/芳烷基":
        return [set(ring) for ring in mol.GetRingInfo().AtomRings()
                if all(mol.GetAtomWithIdx(i).GetIsAromatic() for i in ring)]
    if group == "含氟有机基":
        fluorinated = set()
        for atom in mol.GetAtoms():
            if atom.GetAtomicNum() == 9:
                fluorinated.add(atom.GetIdx())
                fluorinated.update(a.GetIdx() for a in atom.GetNeighbors() if a.GetAtomicNum() == 6)
        for ring in mol.GetRingInfo().AtomRings():
            if fluorinated.intersection(ring) and all(mol.GetAtomWithIdx(i).GetIsAromatic() for i in ring):
                fluorinated.update(ring)
        groups = []
        while fluorinated:
            component = {fluorinated.pop()}
            queue = list(component)
            for idx in queue:
                for neighbor in mol.GetAtomWithIdx(idx).GetNeighbors():
                    other = neighbor.GetIdx()
                    if other in fluorinated:
                        fluorinated.remove(other)
                        component.add(other)
                        queue.append(other)
            groups.append(component)
        return groups
    for pattern in FUNCTIONS.get(group, ()):
        mapped = [atom.GetIdx() for atom in pattern.GetAtoms() if atom.GetAtomMapNum()]
        matches = mol.GetSubstructMatches(pattern)
        if matches:
            return [set(match[i] for i in mapped) if mapped else set(match) for match in matches]
    return []

def shortest_paths(mol, start, blocked, stopping_sites):
    paths = {start: (start,)}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        if current != start and current in stopping_sites:
            continue
        for neighbor in mol.GetAtomWithIdx(current).GetNeighbors():
            idx = neighbor.GetIdx()
            if idx in paths or frozenset((idx, current)) in blocked:
                continue
            paths[idx] = (*paths[current], idx)
            queue.append(idx)
    return paths

def linker_record(mol, path, target, target_site=None):
    atoms = [mol.GetAtomWithIdx(idx) for idx in path[1:-1]]
    symbols = [atom.GetSymbol() for atom in atoms]
    elements = dict(Counter(symbols))
    tags = []
    if not symbols:
        tags.append("直接相连")
    elif set(symbols) == {"C"}:
        tags.append("纯碳骨架")
        if all(not atom.IsInRing() for atom in atoms):
            internal_bonds = [mol.GetBondBetweenAtoms(a, b) for a, b in zip(path[1:-1], path[2:-1])]
            tags.append("不饱和碳链" if any(b.GetBondType() != Chem.BondType.SINGLE for b in internal_bonds) else "饱和碳链")
    for element in ("O", "N", "S"):
        if element in elements:
            tags.append("含" + element)
    if any(e not in {"C", "O", "N", "S"} for e in elements):
        tags.append("含其他元素")
    if any(atom.GetIsAromatic() for atom in atoms):
        tags.append("含芳环")
    if any(atom.IsInRing() and not atom.GetIsAromatic() for atom in atoms):
        tags.append("含非芳香环")
    if target == "silicon_bridge":
        tags.append("硅–硅桥接")
    return {
        "target": target, "target_site": target_site, "length": len(atoms),
        "elements": elements, "tags": tags,
        "path": [mol.GetAtomWithIdx(i).GetSymbol() for i in path],
    }

def annotate(molecule):
    mol = Chem.MolFromSmiles(molecule["smiles"])
    if mol is None:
        raise ValueError("Invalid SMILES: " + molecule["id"])
    if len(Chem.GetMolFrags(mol)) != 1:
        raise ValueError("Multiple covalent components: " + molecule["id"])
    sites, blocked = silicon_sites(mol)
    if not sites:
        raise ValueError("No silicon center: " + molecule["id"])
    active = {index for index, groups in sites if groups}
    group = FUNCTION_OVERRIDES.get(molecule.get("pubchem_cid"), molecule.get("functional_group"))
    targets = functional_atoms(mol, group)
    numbered = {index: position for position, (index, _) in enumerate(sites, 1)}
    no_independent_group = group in {"烷基/其他", "环状氮硅烷"}
    annotated = []
    for index, groups in sites:
        paths = shortest_paths(mol, index, blocked, active if targets else set(numbered))
        linkers = []
        if not active or index in active:
            seen_paths = set()
            for target in targets:
                reachable = [paths[i] for i in target if i in paths and i != index]
                if reachable:
                    path = min(reachable, key=lambda p: (len(p), p))
                    if path not in seen_paths:
                        linkers.append(linker_record(mol, path, "organic"))
                        seen_paths.add(path)
            if not linkers and no_independent_group:
                for other in sorted(active - {index}):
                    if other in paths:
                        linkers.append(linker_record(mol, paths[other], "silicon_bridge", numbered[other]))
        annotated.append({
            "site": numbered[index],
            "groups": [{"group": label, "count": n} for label, n in groups.items()],
            "count": sum(groups.values()), "linkers": linkers,
        })
    total = sum(site["count"] for site in annotated)
    with_linkers = any(site["linkers"] for site in annotated)
    state = "defined" if with_linkers else "no_function" if no_independent_group else "unresolved"
    return {
        "functional_group": group,
        "hydrolyzable": total > 0,
        "hydrolyzable_count": total,
        "sites": annotated,
        "linker_status": state,
    }

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    molecules = json.loads(args.catalog.read_text(encoding="utf-8"))
    for molecule in molecules:
        molecule["coupling_structure"] = annotate(molecule)
    args.output.write_text(json.dumps(molecules, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
