"""Copy intended-public installer bytes using expiring URLs; never publish."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request
from urllib.parse import urlsplit
REPO = 'tensor-dynamics-tech/Sentinel-Releases'
def gh(*args):
    return subprocess.check_output(['gh', *args], text=True)
def main():
    assert os.environ['GITHUB_REPOSITORY'] == REPO
    payload = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())['client_payload']
    tag = payload['tag']
    assert re.fullmatch(r'v0\.37\.0-beta\.[12]', tag) or tag == 'v0.38.0'
    version = tag[1:]
    names = {f'Tensor-Dynamics-{version}-arm64-pilot.dmg': 'Sentinel-Monitor-macOS-arm64.dmg', f'Tensor-Dynamics-Setup-{version}.exe': 'Sentinel-Monitor-Windows.exe', f'Tensor-Dynamics-{version}.AppImage': 'Sentinel-Monitor-Linux.AppImage'}
    if tag == 'v0.38.0':
        names = {name: name for name in names}
    assets = payload['assets']
    assert len(assets) == 3 and {a['name'] for a in assets} == set(names)
    root = Path('verified-manual-installers')
    root.mkdir()
    expected = {}
    for asset in assets:
        parsed = urlsplit(asset['url'])
        assert parsed.scheme == 'https' and parsed.hostname == 'release-assets.githubusercontent.com'
        assert not parsed.username and not parsed.password and not parsed.fragment
        assert re.fullmatch('[a-f0-9]{64}', asset['sha256'])
        assert isinstance(asset['size'], int) and 100_000_000 < asset['size'] < 2_000_000_000
        name, digest, size = names[asset['name']], hashlib.sha256(), 0
        try:
            with urllib.request.urlopen(asset['url'], timeout=120) as response, (root / name).open('wb') as target:
                assert urlsplit(response.url).hostname == parsed.hostname
                while chunk := response.read(1024 * 1024):
                    target.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                    assert size <= asset['size']
        except Exception:
            raise RuntimeError(f'Download failed for {name}; request fresh transfer URLs') from None
        assert size == asset['size'] and digest.hexdigest() == asset['sha256'], name
        expected[name] = {'digest': 'sha256:' + digest.hexdigest(), 'size': size}
        print(f'Verified {name} ({size} bytes)')
    manifest = root / 'SHA256SUMS.txt'
    manifest.write_text(''.join(f"{expected[name]['digest'][7:]}  {name}\n" for name in sorted(expected)))
    expected[manifest.name] = {'digest': 'sha256:' + hashlib.sha256(manifest.read_bytes()).hexdigest(), 'size': manifest.stat().st_size}
    pages = json.loads(gh('api', '--paginate', '--slurp', f'repos/{REPO}/releases?per_page=100'))
    matches = [r for page in pages for r in page if r['tag_name'] == tag]
    assert len(matches) <= 1
    if not matches:
        gh('release', 'create', tag, '--repo', REPO, '--target', 'main', '--draft', '--prerelease', '--title', f'Watch {tag} — Manual Beta', '--notes', 'Manual beta. Signing and production acceptance remain pending. Automatic updates excluded.')
    for name in sorted(expected):
        release = json.loads(gh('release', 'view', tag, '--repo', REPO, '--json', 'isDraft,assets'))
        assert release['isDraft'], 'Refusing to modify a published release'
        existing = [a for a in release['assets'] if a['name'] == name]
        assert len(existing) <= 1
        if existing:
            a = existing[0]
            assert a['digest'] == expected[name]['digest'] and a['size'] == expected[name]['size'] and a['state'] == 'uploaded', name
        else:
            gh('release', 'upload', tag, str(root / name), '--repo', REPO)
    print(f'Verified manual installers ready in draft {tag}; publication is separate.')
if __name__ == '__main__':
    main()
