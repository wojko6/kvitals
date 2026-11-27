#!/usr/bin/env python3
"""KVitals PL Update Manager 0.2 — verified staging, transactional deployment and rollback."""
import argparse
import ctypes
import subprocess
import time
import fcntl
import gettext
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import urllib.request
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import zipfile

REPO = 'wojko6/kvitals'
PLUGIN = 'org.kde.plasma.kvitals'
INITIAL = 'v3.4.0-pl.2'
INITIAL_DIGEST = '1bcea33bd566fe48eda4f268a78f9d8b0f71741bfec346936fbce44ac419c58d'
MO = f'contents/locale/pl/LC_MESSAGES/plasma_applet_{PLUGIN}.mo'
TAG_RE = re.compile(r'v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)-pl\.([1-9]\d*)\Z')
HEX_RE = re.compile(r'[0-9a-f]{64}\Z')
MAX_ASSET = 16 * 1024 * 1024
MAX_UNPACKED = 64 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def version(tag):
    m = TAG_RE.fullmatch(tag)
    require(m is not None, 'Unsupported release tag: ' + str(tag))
    return tuple(map(int, m.groups()))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def request(url, limit):
    require(urlparse(url).scheme == 'https', 'HTTPS required')
    req = urllib.request.Request(url, headers={'User-Agent': 'KVitals-PL-Update/0.2', 'Accept': 'application/vnd.github+json' if '/api.github.com/' in url else 'application/octet-stream'})
    with urllib.request.urlopen(req, timeout=30) as response:
        require(urlparse(response.geturl()).scheme == 'https', 'Non-HTTPS redirect')
        data = response.read(limit + 1)
    require(len(data) <= limit, 'Download exceeds size limit')
    return data


def release(tag=None):
    suffix = 'tags/' + tag if tag else 'latest'
    if tag:
        version(tag)
    info = json.loads(request(f'https://api.github.com/repos/{REPO}/releases/{suffix}', 2 * 1024 * 1024))
    found = info['tag_name']
    version(found)
    require(not info['draft'] and not info['prerelease'], 'Not a stable release')
    require(tag is None or tag == found, 'Unexpected release tag')
    return info


def asset(info, name, fetch=request):
    matches = [a for a in info['assets'] if a['name'] == name]
    require(len(matches) == 1, 'Asset missing or ambiguous: ' + name)
    a = matches[0]
    expected_url = f'https://github.com/{REPO}/releases/download/{info["tag_name"]}/{name}'
    require(a['browser_download_url'] == expected_url, 'Unexpected asset URL')
    require(a['state'] == 'uploaded' and 0 < a['size'] <= MAX_ASSET, 'Invalid asset state/size')
    expected = a.get('digest', '').removeprefix('sha256:')
    require(HEX_RE.fullmatch(expected) is not None and a['digest'].startswith('sha256:'), 'Missing GitHub SHA-256 digest')
    data = fetch(expected_url, MAX_ASSET)
    require(len(data) == a['size'] and digest(data) == expected, 'Asset integrity failure: ' + name)
    return data


def unpack(data, tag, destination):
    import io
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        seen = set()
        total = 0
        for e in z.infolist():
            raw = e.filename.rstrip('/')
            p = PurePosixPath(raw)
            require(raw and not p.is_absolute() and '\\' not in raw and all(x not in ('', '.', '..') for x in raw.split('/')), 'Unsafe ZIP path')
            require(raw not in seen, 'Duplicate ZIP path')
            seen.add(raw)
            mode = stat.S_IFMT(e.external_attr >> 16)
            require(mode in (0, stat.S_IFREG, stat.S_IFDIR), 'Unsupported ZIP file type')
            require(mode != stat.S_IFDIR or e.is_dir(), 'Invalid ZIP directory')
            require(p.parts[0] in ('metadata.json', 'contents', 'LICENSE', 'README.md'), 'Unexpected package path')
            total += e.file_size
            require(total <= MAX_UNPACKED and len(seen) <= 2048, 'ZIP exceeds limits')
        require(z.testzip() is None, 'ZIP CRC failure')
        z.extractall(destination)
    meta = json.loads((destination / 'metadata.json').read_bytes())['KPlugin']
    require(meta['Id'] == PLUGIN and meta['Version'] == '.'.join(map(str, version(tag)[:3])), 'Package metadata mismatch')
    require(bool(meta.get('Description[pl]')), 'Polish metadata missing')
    require((destination / 'contents/ui/main.qml').is_file(), 'Missing main.qml')
    with (destination / MO).open('rb') as f:
        tr = gettext.GNUTranslations(f)
    require(tr.gettext('Memory') == 'Pamięć' and tr.gettext('Processor') == 'Procesor', 'Polish catalog test failed')


def inventory(root):
    require(root.is_dir() and not root.is_symlink(), 'Invalid widget directory')
    meta = root / 'metadata.json'
    contents = root / 'contents'
    require(meta.is_file() and not meta.is_symlink() and contents.is_dir() and not contents.is_symlink(), 'Missing package files')
    result = {'metadata.json': digest(json.dumps(json.loads(meta.read_bytes()), sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode())}
    for p in sorted(contents.rglob('*')):
        require(not p.is_symlink(), 'Symlink in widget: ' + str(p))
        if p.is_file():
            result[p.relative_to(root).as_posix()] = digest(p.read_bytes())
        else:
            require(p.is_dir(), 'Unexpected file type: ' + str(p))
    return result


def registry_clear(reg):
    if reg.exists():
        root = ET.parse(reg).getroot()
        require(not any(e.findtext('id') == '2347917' for e in root.iter('stuff')), 'KDE Store registration detected; stopping')


def atomic_json(path, value):
    with tempfile.NamedTemporaryFile(dir=path.parent, mode='w', encoding='utf-8', delete=False) as f:
        temp = Path(f.name)
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def package(info, directory, fetch=request):
    tag = info['tag_name']
    name = f'{PLUGIN}-{tag}.plasmoid'
    data = asset(info, name, fetch)
    manifest = asset(info, 'SHA256SUMS', fetch).decode('ascii').strip().splitlines()
    require(len(manifest) == 1 and manifest[0] == f'{digest(data)}  {name}', 'SHA256SUMS mismatch')
    if tag == INITIAL:
        require(digest(data) == INITIAL_DIGEST, 'Initial release differs from pinned digest')
    unpack(data, tag, directory / 'candidate')
    (directory / name).write_bytes(data)
    return digest(data)


def plasma_stopped():
    result = subprocess.run(['systemctl', '--user', 'show',
        'plasma-plasmashell.service', '--property=ActiveState', '--value'],
        capture_output=True, text=True, check=True)
    require(result.stdout.strip() == 'inactive', 'Stop plasma-plasmashell.service before applying or rolling back')


def exchange(a, b):
    # Linux atomic directory exchange; fail closed if filesystem lacks support.
    libc = ctypes.CDLL(None, use_errno=True)
    fn = libc.renameat2
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    if fn(-100, os.fsencode(a), -100, os.fsencode(b), 2):
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))


def syncdir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def core(state):
    return {k: state[k] for k in ('installed', 'package_sha256', 'files')}


def backup_path(base, relative):
    parts = PurePosixPath(relative).parts
    require(len(parts) == 3 and parts[0] == 'backups' and re.fullmatch(r'manager-[A-Za-z0-9_]+', parts[1]) and parts[2] == 'previous', 'Invalid manager backup path')
    path = base.joinpath(*parts)
    require(not path.parent.is_symlink() and not path.parent.parent.is_symlink(), 'Symlink in backup path')
    return path


def recover(base, widget):
    journal = base / 'transaction.json'
    if not journal.exists(): return
    tx = json.loads(journal.read_bytes())
    old, new = tx['old'], tx['new']
    candidate = backup_path(base, tx['backup'])
    live = inventory(widget)
    stored = json.loads((base / 'state.json').read_bytes())
    require(stored in (old, new), 'Transaction state mismatch; manual recovery required')
    if live == new['files'] and inventory(candidate) == old['files']:
        syncdir(widget.parent); syncdir(candidate.parent)
        atomic_json(base / 'state.json', new)
        print('TRANSACTION_RECOVERY=COMMITTED')
    elif live == old['files'] and inventory(candidate) == new['files'] and stored == old:
        print('TRANSACTION_RECOVERY=NOT_APPLIED')
    else:
        raise RuntimeError('Ambiguous transaction; no changes made, inspect backups')
    syncdir(base)
    journal.unlink()
    syncdir(base)


def transition(base, widget, registry, state, newcore, candidate, action):
    plasma_stopped()
    registry_clear(registry)
    require(inventory(widget) == state['files'], 'Local files changed before transaction')
    require(inventory(candidate) == newcore['files'], 'Candidate differs from expected inventory')
    require(candidate.stat().st_dev == widget.stat().st_dev, 'Atomic exchange requires same filesystem')
    backup = candidate.relative_to(base).as_posix()
    event = {'action': action, 'from': state['installed'], 'to': newcore['installed'],
             'time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'backup': backup}
    new = dict(newcore, history=state.get('history', []) + [event], previous=core(state), backup=backup)
    journal = base / 'transaction.json'
    require(not journal.exists(), 'Unresolved transaction')
    atomic_json(journal, {'old': state, 'new': new, 'backup': backup})
    syncdir(base)
    exchange(widget, candidate)
    syncdir(widget.parent); syncdir(candidate.parent)
    recover(base, widget)
    print(action.upper() + '=PASS')
    print('INSTALLED=' + new['installed'])
    print('BACKUP=' + str(candidate))
    print('WIDGET_MODIFIED=YES')


def transaction_dir(base):
    backups = base / 'backups'
    backups.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix='manager-', dir=backups))


def apply_update(base, widget, registry, state):
    plasma_stopped()
    pending = base / 'staged/pending.json'
    require(pending.is_file(), 'No staged update; run check first')
    item = json.loads(pending.read_bytes())
    tag = item['tag']; sha = item['sha256']
    require(version(tag) > version(state['installed']), 'Staged version is not newer')
    name = f'{PLUGIN}-{tag}.plasmoid'
    require(item['asset'] == name and HEX_RE.fullmatch(sha), 'Invalid staging metadata')
    info = release()
    require(info['tag_name'] == tag, 'Staged release no longer latest; run check again')
    matches = [a for a in info['assets'] if a['name'] == name]
    require(len(matches) == 1 and matches[0].get('digest') == 'sha256:' + sha, 'Staged release digest changed')
    path = base / 'staged' / name
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_ASSET, 'Invalid staged asset')
    data = path.read_bytes()
    require(digest(data) == sha, 'Staged asset integrity failure')
    candidate = transaction_dir(base) / 'previous'
    unpack(data, tag, candidate)
    # Make staged files durable before directory exchange.
    for f in candidate.rglob('*'):
        if f.is_file():
            with f.open('rb') as fd: os.fsync(fd.fileno())
    for d in sorted((d for d in candidate.rglob('*') if d.is_dir()), key=lambda d: len(d.parts), reverse=True): syncdir(d)
    syncdir(candidate); syncdir(candidate.parent); syncdir(candidate.parent.parent)
    transition(base, widget, registry, state, {'installed': tag, 'package_sha256': sha, 'files': inventory(candidate)}, candidate, 'apply')
    pending.unlink(missing_ok=True)


def rollback(base, widget, registry, state):
    plasma_stopped()
    require('previous' in state and 'backup' in state, 'No manager rollback available')
    previous = state['previous']
    source = backup_path(base, state['backup'])
    require(inventory(source) == previous['files'], 'Rollback backup integrity failure')
    candidate = transaction_dir(base) / 'previous'
    shutil.copytree(source, candidate)
    for f in candidate.rglob('*'):
        if f.is_file():
            with f.open('rb') as fd: os.fsync(fd.fileno())
    for d in sorted((d for d in candidate.rglob('*') if d.is_dir()), key=lambda d: len(d.parts), reverse=True): syncdir(d)
    syncdir(candidate); syncdir(candidate.parent); syncdir(candidate.parent.parent)
    transition(base, widget, registry, state, previous, candidate, 'rollback')


def run(command, base, widget, registry):
    base.mkdir(parents=True, exist_ok=True)
    statefile = base / 'state.json'
    with (base / 'update.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        registry_clear(registry)
        recover(base, widget)
        if command == 'adopt':
            require(not statefile.exists(), 'Already adopted; refusing to reset version')
            with tempfile.TemporaryDirectory(dir=base) as tmp:
                tmp = Path(tmp)
                sha = package(release(INITIAL), tmp)
                baseline = inventory(tmp / 'candidate')
                require(inventory(widget) == baseline, 'Installed widget differs from initial release')
                atomic_json(statefile, {'installed': INITIAL, 'package_sha256': sha, 'files': baseline})
            print('ADOPT=PASS')
        require(statefile.is_file(), 'Run adopt first')
        state = json.loads(statefile.read_bytes())
        installed = state['installed']
        version(installed)
        require(inventory(widget) == state['files'], 'Local files changed; automatic updates blocked')
        print('INSTALLED=' + installed)
        print('LOCAL_INTEGRITY=PASS')
        print('KDE_STORE_DETACH=PASS')
        if command == 'apply':
            apply_update(base, widget, registry, state)
            return
        if command == 'rollback':
            rollback(base, widget, registry, state)
            return
        if command == 'history':
            print(json.dumps(state.get('history', []), ensure_ascii=False, indent=2))
        if command == 'check':
            info = release()
            tag = info['tag_name']
            print('LATEST=' + tag)
            if version(tag) <= version(installed):
                if tag == installed:
                    name = f'{PLUGIN}-{tag}.plasmoid'
                    matches = [a for a in info['assets'] if a['name'] == name]
                    require(len(matches) == 1 and matches[0].get('digest') == 'sha256:' + state['package_sha256'], 'Published asset changed under installed tag')
                (base / 'staged/pending.json').unlink(missing_ok=True)
                print('UPDATE=NONE' if tag == installed else 'UPDATE=OLDER_RELEASE_IGNORED')
                print('WIDGET_MODIFIED=NO')
                return
            with tempfile.TemporaryDirectory(dir=base) as tmp:
                tmp = Path(tmp)
                sha = package(info, tmp)
                staged = base / 'staged'
                staged.mkdir(exist_ok=True)
                name = f'{PLUGIN}-{tag}.plasmoid'
                target = staged / name
                os.replace(tmp / name, target)
                atomic_json(staged / 'pending.json', {'tag': tag, 'sha256': sha, 'asset': name})
            print('UPDATE=STAGED')
            print('APPLY=MANUAL_PLASMA_STOP_REQUIRED')
        print('WIDGET_MODIFIED=NO')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['adopt', 'status', 'check', 'apply', 'rollback', 'history'])
    args = parser.parse_args()
    home = Path.home()
    try:
        run(args.command, home / '.local/share/kvitals-pl', home / '.local/share/plasma/plasmoids' / PLUGIN, home / '.local/share/knewstuff3/plasmoids.knsregistry')
    except Exception as e:
        print('FAIL: ' + str(e), file=__import__('sys').stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
