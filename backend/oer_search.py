"""OER natural-language interpretation and a unified literature + predicted-config search."""
from __future__ import annotations

import math
import os
import random
import re
import sqlite3
import unicodedata
from functools import lru_cache

from fastapi import HTTPException, Query

from backend.literature import DATA_CLASS_LABELS, data_class_of, measurement_basis_of, normalize_reactions, reaction_label


def _reactions(value):
    try:
        return normalize_reactions(value or 'OER')
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

N = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'

ELEMENT_NAMES = {
    '镍': 'Ni', '铁': 'Fe', '钌': 'Ru', '铱': 'Ir', '钴': 'Co', '锰': 'Mn', '铂': 'Pt',
    '钯': 'Pd', '金': 'Au', '银': 'Ag', '铜': 'Cu', '锌': 'Zn', '钛': 'Ti', '锆': 'Zr',
    '钼': 'Mo', '钨': 'W', '钒': 'V', '铬': 'Cr', '铌': 'Nb', '钽': 'Ta', '镧': 'La',
    '铈': 'Ce', '锶': 'Sr', '钡': 'Ba', '钙': 'Ca', '镁': 'Mg', '铝': 'Al', '锡': 'Sn',
    '铋': 'Bi', '铅': 'Pb', '硒': 'Se', '硫': 'S', '氧': 'O', '氢': 'H', '钾': 'K',
    '锂': 'Li', '钠': 'Na', '铑': 'Rh', '锇': 'Os', '铼': 'Re', '铪': 'Hf', '钪': 'Sc',
    '钇': 'Y', '镓': 'Ga', '铟': 'In', '锑': 'Sb', '碲': 'Te', '碳': 'C', '氮': 'N',
    '磷': 'P', '氟': 'F', '氯': 'Cl',
}

# Common catalyst formulae kept as keywords; oxygen/hydrogen are not auto-added as element filters.
FORMULA_ALIASES = {
    'niooh': 'NiOOH', '羟基氧化镍': 'NiOOH',
    'ruo2': 'RuO2', '二氧化钌': 'RuO2',
    'iro2': 'IrO2', '二氧化铱': 'IrO2',
    'pdo2': 'PdO2', '二氧化钯': 'PdO2',
    'pt': 'Pt', 'iru': 'Ir',
}

THEORY_ALIASES = ('理论过电位', '计算过电位', '预测过电位', '理论η', '理论eta')
EXPERIMENT_ALIASES = ('实验过电位', '实测过电位', '实验η', '实测η', '实验eta', '过电位实测')
GENERIC_ALIASES = ('过电位', '过电势', 'overpotential', 'eta', 'η')


def _finite(value, name='数值'):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise HTTPException(400, f'{name}必须是有限数值')
    return float(value)


def parse_oer_intent(text, known_elements):
    if len(text) > 500:
        raise HTTPException(400, '搜索内容最多500字')
    t = unicodedata.normalize('NFKC', text).translate(str.maketrans('₀₁₂₃₄₅₆₇₈₉', '0123456789'))
    t = t.replace('−', '-').replace('～', '~').replace('：', ':').replace('η', 'η')
    work = t
    out = {
        'elements': [], 'exclude_elements': [], 'match': 'contains', 'q': '',
        'status': 'all', 'electrolyte': '', 'corpus': 'all',
        'eta_min': None, 'eta_max': None, 'exp_eta_min': None, 'exp_eta_max': None,
        'pH_min': None, 'pH_max': None, 'source_id': '', 'warnings': [],
        'exp_j': None, 'data_class': '',
    }

    def take(pattern, flags=re.I):
        nonlocal work
        m = re.search(pattern, work, flags)
        if not m:
            return None
        work = work[:m.start()] + ' ' + work[m.end():]
        return m

    if take(r'仅包含所选元素|仅含所选元素|精确组成|组成完全一致|仅包含这些元素'):
        out['match'] = 'exact'
    # 数据类型: 实验数据 / 计算数据 (DFT) / 预测数据 (UMA)
    if take(r'预测数据'):
        out['corpus'] = 'computed'
    elif take(r'实验数据'):
        out['data_class'] = 'experimental'
        out['corpus'] = 'literature'
    elif take(r'计算数据|DFT数据|DFT计算'):
        out['data_class'] = 'computed'
        out['corpus'] = 'literature'
    # Measurement basis of experimental η: η10 / η@10 / @3 mA/cm² -> exp_j (current density, mA/cm²)
    j = re.search(r'(?:η|eta)(?:\s*@\s*(' + N + r')|_?(\d+)(?![\d.]))(?:\s*mA\s*(?:/|每|·)?\s*cm\s*(?:2|²|\^2|-2|−2)?)?', work, re.I)
    if j:
        # η10 / η@3 imply an experimental overpotential; keep that alias so a following range still parses.
        work = work[:j.start()] + ' 实验过电位 ' + work[j.end():]
    if not j:
        j = take(r'(?:@|在|于)?\s*(' + N + r')\s*mA\s*(?:/|每|·)?\s*cm\s*(?:2|²|\^2|-2|−2)?(?:\s*(?:下|时|处))?', re.I)
    if j:
        out['exp_j'] = float(next(g for g in j.groups() if g is not None))
        if out['corpus'] == 'all':
            out['corpus'] = 'literature'
    if take(r'预测构型|计算构型|UMA|预测材料'):
        out['corpus'] = 'computed'
    elif take(r'文献材料|实验材料|公开文献'):
        out['corpus'] = 'literature'
    if take(r'实验报道|实验验证|实验测得|实测材料|experimentally'):
        out['status'] = 'experimentally_verified'
        if out['corpus'] == 'all':
            out['corpus'] = 'literature'
    elif take(r'文献计算|仅计算|computational'):
        out['status'] = 'computational_only'
    if take(r'碱性|alkaline|KOH|NaOH'):
        out['electrolyte'] = 'alkaline'
        if out['corpus'] == 'all':
            out['corpus'] = 'literature'
    elif take(r'酸性|acidic|H2SO4|HClO4|HClO₄|硫酸|高氯酸'):
        out['electrolyte'] = 'acidic'
        if out['corpus'] == 'all':
            out['corpus'] = 'literature'
    elif take(r'中性电解液|中性条件|neutral electrolyte'):
        out['electrolyte'] = 'neutral'
        if out['corpus'] == 'all':
            out['corpus'] = 'literature'

    ph = take(r'pH\s*(?:为|是|=|:)?\s*(' + N + r')\s*(?:~|至|到|—|–|-)\s*(' + N + r')')
    if ph:
        out['pH_min'], out['pH_max'] = float(ph[1]), float(ph[2])
    else:
        ph = take(r'pH\s*(?:>=|≥|不小于|不低于|高于|大于)?\s*(' + N + r')')
        if ph:
            out['pH_min'] = float(ph[1])
        ph = take(r'pH\s*(?:<=|≤|不高于|不超过|低于|小于)?\s*(' + N + r')')
        if ph:
            out['pH_max'] = float(ph[1])

    def parse_range(tail):
        tail = re.sub(r'^\s*(?:在|为|是|约|范围|介于|:|=)\s*', '', tail)
        ran = re.match(r'\s*(' + N + r')\s*(mV|毫伏|V|伏)?\s*(?:~|至|到|—|–|-)\s*(' + N + r')\s*(mV|毫伏|V|伏)?', tail, re.I)
        if ran:
            lo, hi = float(ran[1]), float(ran[3])
            unit = ran[4] or ran[2] or 'V'
            scale = 0.001 if unit.lower() in ('mv', '毫伏') else 1
            return lo * scale, hi * scale, True, True, ran.end()
        one = re.match(
            r'\s*(<=|>=|≤|≥|<|>|不高于|不超过|不大于|不低于|不少于|至少|最高|最低|小于等于|大于等于|小于|大于|低于|高于|等于)?'
            r'\s*(' + N + r')\s*(mV|毫伏|V|伏)?\s*(以下|以内|以上)?',
            tail, re.I)
        if not one:
            return None
        op = one[1] or ('<=' if one[4] in ('以下', '以内') else '>=' if one[4] == '以上' else '=')
        value = float(one[2])
        unit = one[3] or 'V'
        scale = 0.001 if (unit or '').lower() in ('mv', '毫伏') else 1
        value *= scale
        lo = hi = None
        li = ui = True
        if op in ['<', '小于', '低于']:
            hi, ui = value, False
        elif op in ['<=', '≤', '不高于', '不超过', '不大于', '最高', '小于等于']:
            hi = value
        elif op in ['>', '大于', '高于']:
            lo, li = value, False
        elif op in ['>=', '≥', '不低于', '不少于', '至少', '最低', '大于等于']:
            lo = value
        else:
            lo = hi = value
        return lo, hi, li, ui, one.end()

    def apply_eta(kind, lo, hi):
        if kind == 'exp':
            out['exp_eta_min'], out['exp_eta_max'] = lo, hi
            if out['corpus'] == 'all':
                out['corpus'] = 'literature'
            if out['status'] == 'all':
                out['status'] = 'experimentally_verified'
        else:
            out['eta_min'], out['eta_max'] = lo, hi

    spans = []
    alias_groups = [
        ('exp', EXPERIMENT_ALIASES),
        ('theory', THEORY_ALIASES),
        ('generic', GENERIC_ALIASES),
    ]
    for kind, aliases in alias_groups:
        pat = re.compile('|'.join(re.escape(a) for a in sorted(aliases, key=len, reverse=True)), re.I)
        for m in list(pat.finditer(work)):
            if any(s <= m.start() < e or s < m.end() <= e for s, e in spans):
                continue
            tail = work[m.end():]
            parsed = parse_range(tail)
            if not parsed:
                out['warnings'].append('“过电位”缺少可识别的范围，请在高级筛选中设置。')
                spans.append((m.start(), m.end()))
                continue
            lo, hi, _li, _ui, used = parsed
            if lo is not None and hi is not None and lo > hi:
                raise HTTPException(400, '过电位的最小值不能大于最大值')
            apply_eta(kind if kind != 'generic' else 'theory', lo, hi)
            spans.append((m.start(), m.end() + used))
    for start, end in sorted(spans, reverse=True):
        work = work[:start] + ' ' + work[end:]

    for name, symbol in ELEMENT_NAMES.items():
        if name in work:
            out['elements'].append(symbol)
            work = work.replace(name, ' ')

    symbols = sorted({s for s in known_elements if re.fullmatch(r'[A-Z][a-z]?', s)}, key=len, reverse=True)
    for symbol in symbols:
        pat = re.compile(r'(?<![A-Za-z0-9])' + re.escape(symbol) + r'(?![A-Za-z0-9])')
        matches = list(pat.finditer(work))
        if not matches:
            continue
        for m in reversed(matches):
            pre = work[max(0, m.start() - 5):m.start()]
            target = 'exclude_elements' if re.search(r'(?:不含|排除|不要)\s*$', pre) else 'elements'
            if symbol in {'C', 'K', 'P', 'S', 'N', 'O', 'H', 'I', 'V', 'W', 'Y'} and not re.search(
                    r'(?:含有|包含|含|和|与|及|加上)\s*$', pre):
                # Short symbols are too ambiguous unless explicitly introduced.
                if not re.search(r'(?:^|[,，;；\s])$', pre) or symbol in work[m.end():m.end() + 3]:
                    continue
                if symbol in {'O', 'H'} and not re.search(r'(?:含有|包含|含)\s*$', pre):
                    continue
            out[target].append(symbol)
            work = work[:m.start()] + ' ' + work[m.end():]

    for alias, formula in FORMULA_ALIASES.items():
        if alias in work.casefold() or formula.casefold() in work.casefold():
            out['q'] = (out['q'] + ' ' + formula).strip()
            work = re.sub(re.escape(formula), ' ', work, flags=re.I)
            work = work.replace(alias, ' ') if alias in work else work
            # If the alias itself is Chinese, remove it.
            for token in (k for k, v in FORMULA_ALIASES.items() if v == formula and not k.isascii()):
                work = work.replace(token, ' ')

    work = re.sub(
        r'(?:帮我|给我|我想要|我需要|想找|查找|搜索|寻找|找到|找一下|找|请|一种|一些|具有|'
        r'材料|催化剂|催化|构型|的|并且|而且|同时|以及|包含|含有|含|不含|排除|不要|'
        r'和|与|在|为|是|约|之间|范围内|范围|之内|电解液|条件|OER|析氧)',
        ' ', work)
    work = re.sub(r'[,，;；。:：、/\s]+', ' ', work).strip()
    if work:
        out['q'] = (out['q'] + ' ' + work).strip()
    out['elements'] = sorted(set(out['elements']))
    out['exclude_elements'] = sorted(set(out['exclude_elements']))
    return out


def method_summary(method):
    if not method:
        return ''
    if isinstance(method, str):
        return method
    if isinstance(method, dict):
        parts = [method.get('type'), method.get('code'), method.get('functional'), method.get('environment')]
        return ' · '.join(str(x) for x in parts if x)
    return str(method)


STATUS_LABELS = {
    'experimentally_verified': '实验报道',
    'computational_only': '文献计算',
    'unknown': '待核对',
}


def literature_card(record):
    source = record.get('source') or {}
    exp_cond = record.get('experimental_conditions') if isinstance(record.get('experimental_conditions'), dict) else {}
    temperature = exp_cond.get('temperature_K')
    if temperature is None:
        temperature = record.get('temperature_K')
    states = record.get('states') or {}
    return {
        'kind': 'literature',
        'id': record.get('id'),
        'name': record.get('name'),
        'reaction': record.get('reaction') or 'OER',
        'reaction_label': reaction_label(record.get('reaction') or 'OER'),
        'formula': record.get('formula'),
        'elements': record.get('elements') or [],
        'family_key': record.get('family_key'),
        'experimental_status': record.get('experimental_status'),
        'status_label': STATUS_LABELS.get(record.get('experimental_status'), record.get('experimental_status') or '待核对'),
        'overpotential_V': record.get('overpotential_V'),
        'experimental_overpotential_V': record.get('experimental_overpotential_V'),
        'experimental_current_density_mA_cm2': record.get('experimental_current_density_mA_cm2'),
        'experimental_overpotential_raw': record.get('experimental_overpotential_raw'),
        'experimental_activity': record.get('experimental_activity'),
        'measurement_basis': measurement_basis_of(record),
        'data_class': data_class_of(record),
        'data_class_label': DATA_CLASS_LABELS[data_class_of(record)],
        'theory_value_class': 'computed',
        'high_throughput': bool(record.get('high_throughput')),
        'metal_composition': record.get('metal_composition'),
        'electrolyte': record.get('electrolyte'),
        'electrolyte_conditions': record.get('electrolyte_conditions') or [],
        'pH_min': record.get('pH_min'),
        'pH_max': record.get('pH_max'),
        'pH_qualifier': record.get('pH_qualifier'),
        'temperature_K': temperature,
        'method_summary': method_summary(record.get('method')),
        'method': record.get('method'),
        'source': {
            'id': source.get('id'),
            'title': source.get('title'),
            'doi': source.get('doi'),
            'dataset_doi': source.get('dataset_doi'),
            'url': source.get('url'),
            'license': source.get('license'),
        },
        'completeness': record.get('completeness'),
        'data_missing': record.get('data_missing'),
        'data_status_label': record.get('data_status_label'),
        'experimental_conditions': exp_cond or None,
        'condition_basis': record.get('condition_basis'),
        'notes': record.get('notes'),
        'structure_states': [s for s, st in states.items() if isinstance(st, dict) and st.get('available')],
    }


def computed_card(record, method=None):
    method = method or {}
    return {
        'kind': 'computed',
        'id': record.get('id'),
        'name': record.get('formula'),
        'formula': record.get('formula'),
        'formula_full': record.get('formula_full'),
        'elements': record.get('elements') or [],
        'family_key': record.get('family_key'),
        'site_id': record.get('site_id'),
        'variant': record.get('variant'),
        'overpotential_V': record.get('eta'),
        'steps_dG_eV': [record.get('g1'), record.get('g2'), record.get('g3'), record.get('g4')],
        'limiting_steps': record.get('limiting_steps'),
        'review_status': record.get('review_status'),
        'batch_name': record.get('batch_name'),
        'n_atoms': record.get('n_atoms'),
        'method_id': record.get('method_id'),
        'method_name': method.get('name'),
        'method_scope': method.get('scope'),
        'temperature_K': method.get('temperature_K'),
        'potential_V_RHE': method.get('potential_V_RHE'),
        'method_source': method.get('source'),
        'status_label': '预测构型',
        'data_class': 'predicted',
        'data_class_label': DATA_CLASS_LABELS['predicted'],
    }


def known_elements(store, literature):
    symbols = set(store.summary().get('elements') or [])
    try:
        symbols.update(literature.summary('OER').get('elements') or [])
    except Exception:
        pass
    return sorted(symbols)


def _file_key(path):
    st = os.stat(path)
    return str(path), st.st_mtime_ns, st.st_size


@lru_cache(maxsize=4)
def _structure_ids(path, mtime_ns, size, table, column):
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=60)
    try:
        return frozenset(r[0] for r in con.execute(f'SELECT DISTINCT {column} FROM {table}'))
    finally:
        con.close()


def literature_structure_ids(literature):
    try:
        return _structure_ids(*_file_key(literature.path), 'structures', 'material_id')
    except (OSError, sqlite3.Error):
        return frozenset()


def computed_structure_ids(store):
    try:
        return _structure_ids(*_file_key(store.path), 'structures', 'config_id')
    except (OSError, sqlite3.Error):
        return frozenset()


OUTLIER_DG_EV = 6.0
VOLCANO_CAP = 3000


def register(app, store, literature):
    def search_params(
        q: str = Query('', max_length=200),
        elements: str = '',
        exclude_elements: str = '',
        match: str = 'contains',
        status: str = 'all',
        electrolyte: str = '',
        source_id: str = '',
        completeness: str = 'all',
        corpus: str = 'all',
        eta_min: float | None = None,
        eta_max: float | None = None,
        exp_eta_min: float | None = None,
        exp_eta_max: float | None = None,
        pH_min: float | None = None,
        pH_max: float | None = None,
        sort: str = 'eta',
        order: str = 'asc',
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
        method_id: str | None = None,
        reaction: str = 'OER',
        exp_j: float | None = None,
        data_class: str = '',
    ):
        _finite(exp_j, '实验电流密度')
        if data_class not in ('', 'experimental', 'computed', 'predicted'):
            raise HTTPException(400, '数据类型无效')
        for name, value in [
            ('理论过电位下限', eta_min), ('理论过电位上限', eta_max),
            ('实验过电位下限', exp_eta_min), ('实验过电位上限', exp_eta_max),
            ('pH下限', pH_min), ('pH上限', pH_max),
        ]:
            _finite(value, name)
        if eta_min is not None and eta_max is not None and eta_min > eta_max:
            raise HTTPException(400, '理论过电位下限不能大于上限')
        if exp_eta_min is not None and exp_eta_max is not None and exp_eta_min > exp_eta_max:
            raise HTTPException(400, '实验过电位下限不能大于上限')
        if corpus not in ('all', 'literature', 'computed'):
            raise HTTPException(400, '检索范围无效')
        return {
            'q': q, 'elements': [s.strip() for s in elements.split(',') if s.strip()],
            'exclude_elements': [s.strip() for s in exclude_elements.split(',') if s.strip()],
            'match': match, 'status': status, 'electrolyte': electrolyte, 'source_id': source_id,
            'completeness': completeness, 'corpus': corpus, 'eta_min': eta_min, 'eta_max': eta_max,
            'exp_eta_min': exp_eta_min, 'exp_eta_max': exp_eta_max, 'pH_min': pH_min, 'pH_max': pH_max,
            'sort': sort, 'order': order, 'offset': offset, 'limit': limit,
            'method_id': method_id or None,
            'reactions': _reactions(reaction),
            'reaction': ','.join(_reactions(reaction)),
            'exp_j': exp_j, 'data_class': data_class,
        }

    @app.get('/api/oer/interpret')
    def interpret(text: str = Query('', max_length=500)):
        return parse_oer_intent(text, known_elements(store, literature))

    @app.get('/api/literature/interpret')
    def literature_interpret(text: str = Query('', max_length=500)):
        parsed = parse_oer_intent(text, known_elements(store, literature))
        parsed['corpus'] = 'literature' if parsed['corpus'] == 'all' else parsed['corpus']
        return parsed

    @app.get('/api/oer/summary')
    def oer_summary(reaction: str = 'OER'):
        reactions = _reactions(reaction)
        published = literature.summary(reactions)
        computed = store.summary() if 'OER' in reactions else {'configurations': 0, 'elements': [], 'methods': []}
        return {
            'literature': published,
            'computed': computed,
            'reactions': reactions,
            'elements': sorted(set(published.get('elements') or []) | set(computed.get('elements') or [])),
            'status': published.get('status'),
        }

    def run_search(params):
        literature_result = {'total': 0, 'items': [], 'offset': params['offset'], 'limit': params['limit']}
        computed_result = {'total': 0, 'items': [], 'offset': params['offset'], 'limit': params['limit']}
        if params['corpus'] in ('all', 'literature') and params['data_class'] != 'predicted':
            raw = literature.query(
                reaction=params['reactions'],
                elements=params['elements'],
                exclude_elements=params['exclude_elements'],
                match=params['match'],
                offset=params['offset'],
                limit=params['limit'],
                q=params['q'],
                completeness=params['completeness'],
                status=params['status'],
                electrolyte=params['electrolyte'],
                source_id=params['source_id'],
                eta_min=params['eta_min'],
                eta_max=params['eta_max'],
                exp_eta_min=params['exp_eta_min'],
                exp_eta_max=params['exp_eta_max'],
                pH_min=params['pH_min'],
                pH_max=params['pH_max'],
                exp_j=params['exp_j'],
                data_class=params['data_class'],
            )
            lit_ids = literature_structure_ids(literature)
            literature_result = {
                'total': raw['total'],
                'items': [{**literature_card(item), 'has_structure': item.get('id') in lit_ids} for item in raw['items']],
                'offset': params['offset'],
                'limit': params['limit'],
            }
        if params['corpus'] in ('all', 'computed') and 'OER' in params['reactions']:
            # Predicted configurations in oer.sqlite are OER-only. Skip them for other reactions.
            # Experimental-only or electrolyte/pH/source filters do not apply to the predicted cohort.
            skip_computed = (
                params['status'] == 'experimentally_verified'
                or params['electrolyte']
                or params['source_id']
                or params['exp_eta_min'] is not None
                or params['exp_eta_max'] is not None
                or params['pH_min'] is not None
                or params['pH_max'] is not None
                or params['completeness'] not in ('all', '', None)
                or params['exp_j'] is not None
                or params['data_class'] in ('experimental', 'computed')
            )
            if not skip_computed:
                methods = {m['id']: m for m in store.summary().get('methods') or []}
                raw = store.query(
                    elements=params['elements'],
                    exclude_elements=params['exclude_elements'],
                    match='contains' if params['match'] not in ('exact', 'contains') else params['match'],
                    eta_min=params['eta_min'],
                    eta_max=params['eta_max'],
                    sort=params['sort'] if params['sort'] in ('eta', 'g1', 'g2', 'g3', 'g4', 'formula') else 'eta',
                    order=params['order'] if params['order'] in ('asc', 'desc') else 'asc',
                    offset=params['offset'],
                    limit=params['limit'],
                    method_id=params['method_id'],
                    q=params['q'],
                )
                comp_ids = computed_structure_ids(store)
                computed_result = {
                    'total': raw['total'],
                    'items': [{**computed_card(item, methods.get(item.get('method_id'))), 'has_structure': item.get('id') in comp_ids}
                              for item in raw['items']],
                    'offset': params['offset'],
                    'limit': params['limit'],
                }
        return {
            'literature': literature_result,
            'computed': computed_result,
            'applied': {k: v for k, v in params.items() if k not in ('offset', 'limit', 'sort', 'order', 'method_id')},
        }

    @app.get('/api/oer/search')
    def oer_search(
        q: str = Query('', max_length=200),
        elements: str = '',
        exclude_elements: str = '',
        match: str = 'contains',
        status: str = 'all',
        electrolyte: str = '',
        source_id: str = '',
        completeness: str = 'all',
        corpus: str = 'all',
        eta_min: float | None = None,
        eta_max: float | None = None,
        exp_eta_min: float | None = None,
        exp_eta_max: float | None = None,
        pH_min: float | None = None,
        pH_max: float | None = None,
        sort: str = 'eta',
        order: str = 'asc',
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
        method_id: str | None = None,
        reaction: str = 'OER',
        exp_j: float | None = None,
        data_class: str = '',
    ):
        return run_search(search_params(
            q, elements, exclude_elements, match, status, electrolyte, source_id,
            completeness, corpus, eta_min, eta_max, exp_eta_min, exp_eta_max,
            pH_min, pH_max, sort, order, offset, limit, method_id, reaction, exp_j, data_class,
        ))

    @app.get('/api/oer/volcano')
    def oer_volcano(
        q: str = Query('', max_length=200),
        elements: str = '',
        exclude_elements: str = '',
        match: str = 'contains',
        eta_min: float | None = None,
        eta_max: float | None = None,
        method_id: str | None = None,
        limit: int = Query(VOLCANO_CAP, ge=100, le=10000),
    ):
        """Computed configurations as (ΔG_O*−ΔG_OH*, η) points; any |ΔG step| > 6 eV is always excluded."""
        _finite(eta_min, '理论过电位下限')
        _finite(eta_max, '理论过电位上限')
        if eta_min is not None and eta_max is not None and eta_min > eta_max:
            raise HTTPException(400, '理论过电位下限不能大于上限')
        symbols = sorted({s.strip() for s in elements.split(',') if s.strip()})
        excluded_el = sorted({s.strip() for s in exclude_elements.split(',') if s.strip()})
        if any(not re.fullmatch(r'[A-Z][a-z]?', s) for s in symbols + excluded_el):
            raise HTTPException(400, '元素符号无效，请使用 Cu、Cl 等标准符号')
        clauses, values = [], []
        if symbols:
            if match == 'exact':
                clauses.append('c.family_key=?')
                values.append('+'.join(symbols))
            else:
                for symbol in symbols:
                    clauses.append('EXISTS (SELECT 1 FROM elements e WHERE e.config_id=c.id AND e.symbol=?)')
                    values.append(symbol)
        for symbol in excluded_el:
            clauses.append('NOT EXISTS (SELECT 1 FROM elements e WHERE e.config_id=c.id AND e.symbol=?)')
            values.append(symbol)
        term = (q or '').strip()
        if term:
            like = '%' + term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            clauses.append("(c.formula LIKE ? ESCAPE '\\' OR c.family_key LIKE ? ESCAPE '\\' OR c.id LIKE ? ESCAPE '\\' "
                           "OR c.site_id LIKE ? ESCAPE '\\' OR c.variant LIKE ? ESCAPE '\\')")
            values.extend([like] * 5)
        for column, op, value in [('eta', '>=', eta_min), ('eta', '<=', eta_max), ('method_id', '=', method_id or None)]:
            if value is not None:
                clauses.append(f'c.{column}{op}?')
                values.append(value)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        outlier = ' OR '.join(f'abs(c.g{i})>{OUTLIER_DG_EV}' for i in range(1, 5))
        with store.connect() as con:
            rows = con.execute(
                f'SELECT c.id,c.formula,c.variant,c.g1,c.g2,c.g3,c.g4,c.eta,({outlier}) AS bad FROM configurations c{where}',
                values,
            ).fetchall()
        kept = [r for r in rows if not r['bad']]
        n_excluded = len(rows) - len(kept)
        sampled = len(kept) > limit
        shown = random.Random(20261113).sample(kept, limit) if sampled else kept
        shown.sort(key=lambda r: r['eta'], reverse=True)
        points = [{
            'id': r['id'], 'formula': r['formula'], 'variant': r['variant'],
            'x': round(r['g2'], 4), 'eta': round(r['eta'], 4), 'data_class': 'predicted',
        } for r in shown]
        xs = [p['x'] for p in points]
        etas = [p['eta'] for p in points]
        return {
            'descriptor': {'x': 'ΔG_O* − ΔG_OH* (= ΔG2, eV)', 'y': 'η = max(ΔG1..ΔG4) − 1.23 (V)'},
            # All volcano points are UMA inference (预测数据); DFT/experimental values are never plotted here.
            'data_classes': [{'data_class': 'predicted', 'label': DATA_CLASS_LABELS['predicted'], 'n': len(points),
                              'note': 'UMA 推理的预测构型'}],
            'matched': len(rows),
            'excluded_outliers': n_excluded,
            'outlier_rule': f'any |ΔG1..ΔG4| > {OUTLIER_DG_EV:g} eV',
            'eligible': len(kept),
            'shown': len(points),
            'sampled': sampled,
            'range': {'x': [min(xs), max(xs)] if xs else None, 'eta': [min(etas), max(etas)] if etas else None},
            'points': points,
        }
