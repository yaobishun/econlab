"""Build a source-only GitHub package without touching local research data.

Run from any directory: python econlab/tools/package_github.py
Use --replace only to replace a previous generated package, never a Git checkout.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / 'dist'
OUTPUT = DIST / 'github'
ARCHIVE = DIST / 'econlab-github-source.zip'
KIND = 'econlab-github-source'
ROOT_FILES = ('.gitignore', 'README.md', 'THIRD_PARTY_NOTICES.md')
APP_FILES = (
    '.gitignore', 'requirements.txt', 'start.cmd', 'start.ps1', 'stop.ps1',
    'app.py', 'catalog.py', 'codex_bridge.py', 'engine.py', 'pdf_visual.py',
    'provider_bridge.py', 'runner.py', 'store.py', 'materials.py',
    'reproduction.py', 'environments.py', 'execution_agent.py',
)
TOOL_FILES = (
    'bootstrap_pilot.py', 'current_forms_smoke.mjs', 'current_ui_smoke.mjs',
    'live_workflow_probe.py', 'prepare_ui_preview.py', 'package_github.py',
    'multi_paper_validation.py', 'reproduction_ui_smoke.mjs', 'agent_capability_probe.py',
    'agent_workflow_validation.py', 'agent_case_verify.py', 'agent_case_solve.py', 'agent_case_resume.py', 'agent_ui_contract_test.mjs',
)
PILOT_FILES = ('README.md', 'econlab_qse.m', 'provenance.json')
BLOCKED_PARTS = {'.local', '.venv', 'venv', '__pycache__', '.git', '.codex', '.aws', 'node_modules', 'dist'}
BLOCKED_SUFFIXES = {
    '.pdf', '.mat', '.sqlite', '.sqlite3', '.db', '.log', '.zip', '.rar', '.7z',
    '.tar', '.gz', '.pem', '.key', '.pfx', '.p12', '.dpapi', '.pyc', '.pyo',
    '.csv', '.dta', '.xlsx', '.xls', '.parquet', '.rds', '.rdata', '.rda', '.pkl', '.pickle', '.npy', '.npz', '.h5', '.hdf5',
}
TEXT_SUFFIXES = {'.py', '.m', '.js', '.mjs', '.json', '.md', '.txt', '.cmd', '.ps1', '.html', '.css'}
SECRET_PATTERNS = {
    'API key': re.compile(r'\bsk-[A-Za-z0-9_-]{16,}\b'),
    'GitHub token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b'),
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
}


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def acceptable(relative: Path) -> bool:
    parts = {part.lower() for part in relative.parts}
    name = relative.name.lower()
    return not (
        parts & BLOCKED_PARTS
        or relative.suffix.lower() in BLOCKED_SUFFIXES
        or relative.as_posix().lower().startswith('econlab/pilot/author/')
        or name.startswith('.env')
        or name == 'auth.json'
        or name.startswith(('credentials', 'secrets'))
    )


def sources() -> list[Path]:
    paths = [ROOT / name for name in ROOT_FILES]
    paths += [ROOT / 'econlab' / name for name in APP_FILES]
    paths += [ROOT / 'econlab' / 'tools' / name for name in TOOL_FILES]
    paths += [ROOT / 'econlab' / 'tests' / 'test_current.py',ROOT / 'econlab' / 'tests' / 'test_reproduction.py']
    paths += [ROOT / 'econlab' / 'pilot' / name for name in PILOT_FILES]
    paths += [p for p in (ROOT / 'econlab' / 'static').rglob('*') if p.is_file()]
    paths += [p for p in ROOT.glob('LICENSE*') if p.is_file()]
    result = []
    for path in sorted(set(paths)):
        if not path.is_file():
            raise ValueError('Required source file is missing: ' + str(path.relative_to(ROOT)))
        canonical = path.resolve()
        if not canonical.is_relative_to(ROOT) or path.is_symlink():
            raise ValueError('Refusing an external or symbolic source path: ' + str(path))
        relative = path.relative_to(ROOT)
        if not acceptable(relative) or not acceptable(canonical.relative_to(ROOT)):
            raise ValueError('Excluded material appeared in the source list: ' + str(relative))
        if path.stat().st_size > 5 * 1024 * 1024:
            raise ValueError('Unexpectedly large source file requires manual review: ' + str(relative))
        if path.suffix.lower() in TEXT_SUFFIXES:
            text = path.read_text(encoding='utf-8-sig', errors='replace')
            for label, pattern in SECRET_PATTERNS.items():
                match = pattern.search(text)
                if match:
                    line = text[:match.start()].count('\n') + 1
                    # Never print credential values.
                    raise ValueError(f'Possible {label} in {relative}:{line}; package was not built')
        result.append(path)
    return result


def checked_output(path: Path) -> None:
    if not DIST.resolve().is_relative_to(ROOT) or path.resolve() != DIST.resolve() / path.name:
        raise ValueError('Unexpected package output location')


def build(replace: bool = False) -> dict:
    inputs = sources()  # Inspect before writing or replacing any output.
    checked_output(OUTPUT)
    checked_output(ARCHIVE)
    if OUTPUT.exists() or ARCHIVE.exists():
        if not replace:
            raise ValueError('Package already exists. Use --replace to replace generated output only.')
        if OUTPUT.exists():
            marker = OUTPUT / 'SOURCE_MANIFEST.json'
            if (OUTPUT / '.git').exists() or not marker.is_file() or json.loads(marker.read_text(encoding='utf-8')).get('kind') != KIND:
                raise ValueError('Refusing to replace a Git checkout or an unrecognized directory')
            # Both resolved targets were checked above; only our marked build folder is removed.
            shutil.rmtree(OUTPUT)
        ARCHIVE.unlink(missing_ok=True)
    OUTPUT.mkdir(parents=True)
    records = []
    for path in inputs:
        relative = path.relative_to(ROOT)
        content = path.read_bytes()
        target = OUTPUT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        records.append({'path': relative.as_posix(), 'bytes': len(content), 'sha256': digest(content)})
    manifest = {
        'kind': KIND,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'license_status': 'project LICENSE supplied' if any(p.name.startswith('LICENSE') for p in inputs if p.parent == ROOT) else 'project LICENSE not selected',
        'files': records,
    }
    manifest_path = OUTPUT / 'SOURCE_MANIFEST.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    archive_paths = [OUTPUT / r['path'] for r in records] + [manifest_path]
    with zipfile.ZipFile(ARCHIVE, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in archive_paths:
            archive.write(path, path.relative_to(OUTPUT).as_posix())
    with zipfile.ZipFile(ARCHIVE) as archive:
        expected = {r['path'] for r in records} | {'SOURCE_MANIFEST.json'}
        if set(archive.namelist()) != expected:
            raise ValueError('Archive member list differs from the inspected source list')
        for record in records:
            if not acceptable(Path(record['path'])) or digest(archive.read(record['path'])) != record['sha256']:
                raise ValueError('Archive source integrity check failed: ' + record['path'])
    checksum = digest(ARCHIVE.read_bytes())
    (DIST / 'econlab-github-source.sha256').write_text(checksum + '  ' + ARCHIVE.name + '\n', encoding='ascii')
    report = {
        'source_directory': 'dist/github', 'archive': 'dist/' + ARCHIVE.name,
        'source_files': len(records), 'archive_bytes': ARCHIVE.stat().st_size,
        'archive_sha256': checksum, 'license_status': manifest['license_status'],
        'excluded': ['econlab/.local/**', 'econlab/.venv/**', '**/__pycache__/**', 'econlab/pilot/author/**', 'econlab/pilot/inputs.mat', 'econlab/pilot/reference.mat', 'credentials and generated archives'],
        'checks': {'explicit_source_allowlist': True, 'secret_pattern_scan': True, 'archive_member_hashes': True},
        'local_research_data': 'not changed',
    }
    (DIST / 'PACKAGE_REPORT.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replace', action='store_true', help='Replace only a previous generated package, refusing Git checkouts')
    try:
        print(json.dumps(build(parser.parse_args().replace), ensure_ascii=False, indent=2))
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from error
