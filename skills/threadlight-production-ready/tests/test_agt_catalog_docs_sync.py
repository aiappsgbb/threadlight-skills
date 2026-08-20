import importlib.util
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'production_ready.py'
PILLAR_MD = ROOT / 'references' / 'pillars' / '02-agent-governance.md'
SKILL_MD = ROOT / 'SKILL.md'

_EXPECTED_AGT_IDS = {
    'AGT-001', 'AGT-002', 'AGT-003', 'AGT-004', 'AGT-005', 'AGT-006', 'AGT-007', 'AGT-008',
    'AGT-101', 'AGT-102', 'AGT-103',
    'AGT-V4-001', 'AGT-V4-002', 'AGT-V4-003', 'AGT-V4-006', 'AGT-V4-007', 'AGT-V4-101',
}


def _load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


production_ready = _load_module('production_ready_catalog_sync', SCRIPT)



def _plain_catalog() -> dict[str, dict[str, object]]:
    return {fid: meta for fid, meta in dict.items(production_ready.FINDING_CATALOG)}



def _agent_governance_rows() -> tuple[dict[str, str], list[str]]:
    rows: dict[str, str] = {}
    malformed: list[str] = []
    for line in PILLAR_MD.read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if not stripped.startswith('| `AGT'):
            continue
        cells = [cell.strip() for cell in stripped.split('|')[1:-1]]
        if len(cells) != 4:
            malformed.append(stripped)
            continue
        finding_id, catalog_title, _check, _default_status = cells
        rows[finding_id.strip('`')] = catalog_title
    return rows, malformed



def test_agent_governance_pillar_titles_match_catalog() -> None:
    catalog = _plain_catalog()
    expected = {fid: catalog[fid]['title'] for fid in sorted(_EXPECTED_AGT_IDS)}
    rows, malformed = _agent_governance_rows()

    assert not malformed, (
        'agent-governance tables must use columns '
        '`ID | Catalog title | Check | Default status`: ' + '; '.join(malformed)
    )
    assert set(rows) == set(expected), 'agent-governance tables drifted from AGT catalog ids'
    assert rows == expected, 'agent-governance catalog titles must exactly match FINDING_CATALOG'



def test_skill_summary_tracks_current_agt_surface() -> None:
    skill_text = SKILL_MD.read_text(encoding='utf-8')

    assert 'AGT module imported in app code' not in skill_text
    assert 'contract-probe evidence' in skill_text

    match = re.search(r'scores it against (\d+) findings', skill_text)
    assert match is not None, 'SKILL.md must advertise the production_ready finding count'
    assert int(match.group(1)) == len(_plain_catalog())
