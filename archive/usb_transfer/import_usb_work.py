from pathlib import Path
import hashlib, json, shutil, sys
from datetime import datetime

root = Path(__file__).resolve().parent
source = Path('D:/Capstone-20260923')
baseline_path = root / '.usb_export/Capstone-20260923/TRANSFER_MANIFEST.json'
baseline = {x['path']: x['sha256'] for x in json.loads(baseline_path.read_text(encoding='utf-8'))}

def digest(p):
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

rows = []
excluded = {'.git', '.solver_deps', '_runtime', '__pycache__', '.usb_export'}
for p in sorted(source.rglob('*')):
    rel = p.relative_to(source)
    if any(part in excluded or part.startswith('.docx_qa') for part in rel.parts):
        continue
    if p.is_symlink():
        raise RuntimeError(f'Symlink requires review: {rel}')
    if not p.is_file():
        continue
    target = root / rel
    incoming = digest(p)
    current = digest(target) if target.exists() else None
    old = baseline.get(rel.as_posix())
    if current == incoming:
        status = 'same'
    elif current is None:
        status = 'added'
    elif current == old:
        status = 'updated'
    elif incoming == old:
        status = 'local_preserved'
    else:
        status = 'conflict'
    rows.append(dict(path=rel.as_posix(), status=status, incoming=incoming, current=current))
summary = {s: sum(r['status'] == s for r in rows) for s in ('same','added','updated','local_preserved','conflict')}
usb_manifest = json.loads((source/'TRANSFER_MANIFEST.json').read_text(encoding='utf-8'))
by_path = {r['path']:r for r in rows}
for entry in usb_manifest:
    if entry['path'] not in by_path or by_path[entry['path']]['incoming'] != entry['sha256']:
        raise RuntimeError('USB manifest mismatch: ' + entry['path'])
print(json.dumps(dict(summary=summary,changes=[r for r in rows if r['status']!='same']), ensure_ascii=False, indent=2))
if '--apply' not in sys.argv:
    sys.exit(0)

backup = root / 'usb_import_backups' / datetime.now().strftime('%Y%m%d-%H%M%S')
backup.mkdir(parents=True, exist_ok=False)
for r in rows:
    rel = Path(r['path'])
    p, target = source / rel, root / rel
    if digest(p) != r['incoming']:
        raise RuntimeError('USB file changed during import: ' + str(rel))
    actual_current = digest(target) if target.exists() else None
    if actual_current != r['current']:
        raise RuntimeError('Local file changed during import: ' + str(rel))
    if r['status'] in ('updated','conflict'):
        saved = backup / 'before' / rel
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, saved)
        assert digest(saved) == r['current']
    if r['status'] in ('added','updated'):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        assert digest(target) == r['incoming']
    elif r['status'] == 'conflict':
        saved = backup / 'incoming_conflicts' / rel
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, saved)
        assert digest(saved) == r['incoming']
report = dict(source=str(source),backup=str(backup),summary=summary,files=rows,
              note='No local or USB files deleted. Conflicts preserved separately.')
(backup/'import_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('IMPORT_COMPLETE ' + str(backup))
