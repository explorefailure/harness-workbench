#!/usr/bin/env python3
"""Explicit opt-in download of the pinned official runtime into NEW runtime/."""
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import tarfile
import urllib.error
import urllib.request
import zipfile

ARCHIVE_LIMIT = 134_217_728
_RELEASE_URL = re.compile(
    r'https://github\.com/([^/]+)/([^/]+)/releases/download/([^/]+)/([^/]+)')


def _read_bounded(request):
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read(ARCHIVE_LIMIT + 1)


def _api_request(url, accept):
    """A GitHub API request, authenticated when GITHUB_TOKEN or GH_TOKEN is set.

    Unauthenticated API calls share a 60-per-hour budget per IP address, which
    busy CI runners exhaust (HTTP 403 "rate limit exceeded"). The token is an
    unredirected header: the asset endpoint redirects to a separate storage
    host, and the token must reach api.github.com only.
    """
    request = urllib.request.Request(url, headers={'Accept': accept})
    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if token:
        request.add_unredirected_header('Authorization', f'Bearer {token}')
    return request


def _api_asset_request(url):
    """The same release asset through GitHub's API download route.

    github.com/<o>/<r>/releases/download/... has been observed answering 504
    while the API asset endpoint serves identical bytes. The pinned archive and
    binary digests are still checked, so the route changes nothing trusted.
    """
    match = _RELEASE_URL.fullmatch(url)
    if match is None:
        return None
    owner, repo, tag, name = match.groups()
    listing = _api_request(
        f'https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}',
        'application/vnd.github+json')
    with urllib.request.urlopen(listing, timeout=60) as response:
        release = json.loads(response.read(4_194_305)[:4_194_304])
    for item in release.get('assets', []) if isinstance(release, dict) else []:
        if isinstance(item, dict) and item.get('name') == name and isinstance(item.get('id'), int):
            return _api_request(
                f'https://api.github.com/repos/{owner}/{repo}/releases/assets/{item["id"]}',
                'application/octet-stream')
    return None


def download(url):
    """Fetch an official release archive, falling back to the API route on a 5xx."""
    try:
        return _read_bounded(url)
    except urllib.error.HTTPError as error:
        if error.code < 500:
            raise
        fallback = _api_asset_request(url)
        if fallback is None:
            raise
        print(f'Release download answered HTTP {error.code}; retrying via the GitHub API')
        return _read_bounded(fallback)


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
    raw = download(asset['url'])
    if len(raw) > ARCHIVE_LIMIT or hashlib.sha256(raw).hexdigest() != asset['archive_sha256']:
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
