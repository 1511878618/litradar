"""Build a local XPI and validate mandatory Zotero loader fields."""
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import urlsplit, unquote

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'manifest.json').read_text())
settings = manifest['applications']['zotero']
# Local custom distribution: reserved .invalid domain; updates are installed manually.
# Zotero rejects file:// update URLs as insecure.
settings['update_url'] = 'https://litradar.invalid/updates.json'
for key in ('id', 'update_url', 'strict_max_version'):
    if not settings.get(key):
        raise ValueError(f'Zotero loader requires applications.zotero.{key}')
if not re.fullmatch(r'[A-Za-z0-9._-]+@[A-Za-z0-9._-]+', settings['id']):
    raise ValueError('Invalid extension id')
assert urlsplit(settings['update_url']).scheme == 'https'
assert settings['strict_min_version'] == '10.0'
assert settings['strict_max_version'] == '10.0.*'
(root / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
artifact = root / f"litradar-pdf-bridge-{manifest['version']}.xpi"
with zipfile.ZipFile(artifact, 'w', zipfile.ZIP_DEFLATED) as archive:
    for name in ('manifest.json', 'bootstrap.js', 'reading-list.js'):
        archive.write(root / name, name)
with zipfile.ZipFile(artifact) as archive:
    assert archive.testzip() is None
    assert json.loads(archive.read('manifest.json')) == manifest
    assert archive.read('bootstrap.js') == (root / 'bootstrap.js').read_bytes()
print(f'Built and validated: {artifact}')
