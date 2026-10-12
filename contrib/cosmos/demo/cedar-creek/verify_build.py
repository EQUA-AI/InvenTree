"""Check a Cedar Creek demo build against its manifest and write build-report.json.

Usage: verify_build.py <manifest.json> <output dir> <git sha> <app image>
Exits 1 when any expectation fails; the report is written either way.
"""

import datetime
import hashlib
import json
import re
import sys
from pathlib import Path


def load_command_json(path: Path) -> dict:
    """Parse command output, skipping any log lines printed before the JSON."""
    text = path.read_text(encoding='utf-8')
    start = text.find('\n{')
    start = 0 if text.startswith('{') else start + 1
    if start < 0 or not text[start:].lstrip().startswith('{'):
        raise ValueError(f'{path.name}: no JSON object found')
    return json.JSONDecoder().raw_decode(text[start:])[0]


def sha256(path: Path) -> str:
    """Hex digest of a file."""
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def main(manifest_path: str, out_dir: str, git_sha: str, image: str) -> int:
    """Compare apply, replay and QA results with the manifest."""
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    out = Path(out_dir)
    expected = manifest['expected']
    checks: list[tuple[str, bool, str]] = []

    def check(name, actual, want):
        checks.append((name, actual == want, f'{actual} (want {want})'))

    apply = load_command_json(out / 'apply.json')
    replay = load_command_json(out / 'replay.json')

    check(
        'workbook sha256',
        apply['workbook_sha256'],
        manifest['source_package']['workbook']['sha256'],
    )
    check('client', apply['client']['code'], manifest['population']['client_code'])
    check('locations', apply['locations']['created'], expected['locations'])
    check('pumps', apply['pumps']['created'], expected['pumps'])
    check('equipment', apply['equipment']['created'], expected['equipment'])
    check(
        'profiles',
        apply['details']['updated'] + apply['details']['unchanged'],
        expected['profiles'],
    )
    check('placements', apply['placements']['moved'], expected['profiles'])
    check(
        'part categories', apply['categories']['created'], expected['part_categories']
    )
    check('parts', apply['parts']['created'], expected['parts'])
    check('components', apply['components']['created'], expected['components'])
    check('part links', apply['part_links']['created'], expected['part_links'])
    check(
        'stock locations',
        apply['stock_locations']['created'],
        expected['stock_locations'],
    )
    check('companies', apply['companies']['created'], expected['companies'])
    check(
        'manufacturer parts',
        apply['manufacturer_parts']['created'],
        expected['manufacturer_parts'],
    )
    check(
        'supplier parts', apply['supplier_parts']['created'], expected['supplier_parts']
    )
    check('stock items', apply['stock_items']['created'], expected['stock_items'])
    check('history', apply['history']['created'], expected['history'])
    check('work orders', apply['work_orders']['created'], expected['work_orders'])
    check(
        'work orders planned',
        apply['work_orders']['planned'],
        expected['work_orders_planned'],
    )

    created_on_replay = {
        key: value['created']
        for key, value in replay.items()
        if isinstance(value, dict) and value.get('created')
    }
    check('replay creates nothing', created_on_replay, {})
    check('replay transfers', replay['placements']['transfers'], 0)

    qa_text = (out / 'qa-gates.txt').read_text(encoding='utf-8')
    match = re.search(r'summary:\s*(\d+)\s*pass\s*/\s*(\d+)\s*fail', qa_text)
    qa = (int(match.group(1)), int(match.group(2))) if match else None
    want_qa = (expected['qa_gates']['pass'], expected['qa_gates']['fail'])
    check('QA gates pass/fail', qa, want_qa)

    dump = out / 'cedar-creek-demo.dump'
    ok = all(passed for _, passed, _ in checks) and dump.stat().st_size > 0
    report = {
        'dataset': manifest['dataset'],
        'version': manifest['version'],
        'built_at': datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds'),
        'git_sha': git_sha,
        'app_image': image,
        'workbook_sha256': apply['workbook_sha256'],
        'dump': {
            'file': dump.name,
            'bytes': dump.stat().st_size,
            'sha256': sha256(dump),
        },
        'warnings': apply.get('warnings', []),
        'manual': apply.get('manual', {}),
        'checks': [
            {'check': name, 'ok': passed, 'detail': detail}
            for name, passed, detail in checks
        ],
        'ok': ok,
    }
    (out / 'build-report.json').write_text(
        json.dumps(report, indent=2, default=str) + '\n', encoding='utf-8'
    )

    for name, passed, detail in checks:
        print(f'{"ok  " if passed else "FAIL"}  {name:<22} {detail}')
    print('BUILD', 'OK' if ok else 'FAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(*sys.argv[1:5]))
