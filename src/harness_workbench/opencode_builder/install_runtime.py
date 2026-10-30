#!/usr/bin/env python3
"""Explicit opt-in download of the pinned official runtime into NEW runtime/."""
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import tarfile
import urllib.request
import zipfile


def main():
    root = Path(__file__).resolve().parent
    target = root / 'runtime'
    if target.exists() or target.is_symlink():
        raise ValueError('runtime already exists; keep its evidence or choose a new workspace')
    pin = json.loads((root / 'pin.json').read_text())
    key = platform.system().lower() + '-' + platform.machine().lower()
    if key not in pin['platforms']:
        raise ValueError('unsupported runtime platform: ' + key)
    asset = pin['platforms'][key]
    # Official assets are below 100 MiB at this pin. Bound before unpacking.
    with urllib.request.urlopen(asset['url'], timeout=60) as response:
        raw = response.read(134_217_729)
    if len(raw) > 134_217_728 or hashlib.sha256(raw).hexdigest() != asset['archive_sha256']:
        raise ValueError('runtime archive digest/size mismatch')
    if asset['url'].endswith('.zip'):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            member = archive.getinfo('opencode')
            if member.file_size > 400_000_000:
                raise ValueError('runtime binary exceeds size bound')
            binary = archive.read(member)
    else:
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
            member = archive.getmember('opencode')
            if not member.isfile() or member.size > 400_000_000:
                raise ValueError('runtime binary is not a bounded regular member')
            binary = archive.extractfile(member).read()
    if hashlib.sha256(binary).hexdigest() != asset['binary_sha256']:
        raise ValueError('runtime binary digest mismatch')
    target.mkdir(mode=0o755)
    path = target / 'opencode'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o755)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(binary)
    print(f'Installed OpenCode {pin["version"]}: {path}')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, tarfile.TarError, zipfile.BadZipFile) as error:
        raise SystemExit(str(error))
