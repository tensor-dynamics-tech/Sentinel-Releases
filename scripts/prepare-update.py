"""Mirror exact Windows/Linux update bytes using expiring per-asset URLs.

The operator's GitHub token never leaves their computer. This public repository
uses its own short-lived Actions token. Nothing is published by this workflow.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request
from urllib.parse import urlsplit
import yaml

REPO = "tensor-dynamics-tech/Sentinel-Releases"


def gh(*args):
    return subprocess.check_output(["gh", *args], text=True)


def main():
    assert os.environ["GITHUB_REPOSITORY"] == REPO
    payload = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())["client_payload"]
    tag = payload["tag"]
    assert re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)-beta\.(0|[1-9]\d*)", tag)
    version = tag[1:]
    expected_names = {
        "beta.yml": "win-x64",
        f"Tensor-Dynamics-Setup-{version}.exe": "win-x64",
        f"Tensor-Dynamics-Setup-{version}.exe.blockmap": "win-x64",
        "beta-linux.yml": "linux-x64",
        f"Tensor-Dynamics-{version}.AppImage": "linux-x64",
    }
    files = payload["assets"]
    assert len(files) == len(expected_names)
    assert {item["name"] for item in files} == set(expected_names)
    root = Path("verified-assets")
    root.mkdir()
    verified = {}
    for item in files:
        name, url = item["name"], item["url"]
        parsed = urlsplit(url)
        assert parsed.scheme == "https" and parsed.hostname == "release-assets.githubusercontent.com"
        assert not parsed.username and not parsed.password and not parsed.fragment
        assert re.fullmatch(r"[a-f0-9]{64}", item["sha256"])
        assert isinstance(item["size"], int) and 0 < item["size"] < 2_000_000_000
        sha256, sha512, size = hashlib.sha256(), hashlib.sha512(), 0
        # Never print expiring URLs, including through HTTP exception messages.
        try:
            with urllib.request.urlopen(url, timeout=120) as response, (root / name).open("wb") as target:
                assert urlsplit(response.url).hostname == parsed.hostname
                while chunk := response.read(1024 * 1024):
                    target.write(chunk)
                    sha256.update(chunk)
                    sha512.update(chunk)
                    size += len(chunk)
                    assert size <= item["size"]
        except Exception:
            raise RuntimeError(f"Download failed for {name}; request fresh asset URLs") from None
        assert size == item["size"] and sha256.hexdigest() == item["sha256"], f"Digest mismatch: {name}"
        import base64
        verified[name] = {"sha256": sha256.hexdigest(), "sha512": base64.b64encode(sha512.digest()).decode(), "size": size}
        print(f"Verified {name} ({size} bytes)")
    for feed, extension, platform in [("beta.yml", ".exe", "win-x64"), ("beta-linux.yml", ".AppImage", "linux-x64")]:
        info = yaml.safe_load((root / feed).read_text())
        assert info["version"] == version and info["files"] and not info.get("packages")
        for entry in info["files"]:
            name = entry["url"]
            assert expected_names.get(name) == platform and name.endswith(extension)
            assert entry["sha512"] == verified[name]["sha512"] and entry["size"] == verified[name]["size"]
        assert info["path"] in verified and info["sha512"] == verified[info["path"]]["sha512"]
        manifest = root / f"SHA256SUMS-{platform}.txt"
        manifest.write_text("".join(f"{verified[name]['sha256']}  {name}\n" for name in sorted(verified) if expected_names[name] == platform))
    expected = {p.name: {"digest": "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest(), "size": p.stat().st_size} for p in root.iterdir()}
    releases = json.loads(gh("api", "--paginate", "--slurp", f"repos/{REPO}/releases?per_page=100"))
    matches = [r for page in releases for r in page if r["tag_name"] == tag]
    assert len(matches) <= 1
    if not matches:
        gh("release", "create", tag, "--repo", REPO, "--target", "main", "--draft", "--prerelease", "--title", f"Watch {tag} — Windows and Linux Beta", "--notes", "Windows x64 and Linux x64 only. macOS is excluded. Unsigned Windows builds may show SmartScreen warnings. Installer acceptance is required before publication. Stable users are not enrolled.")

    def check():
        release = json.loads(gh("release", "view", tag, "--repo", REPO, "--json", "isDraft,assets,url"))
        assert release["isDraft"], "Never overwrite a published release"
        seen = set()
        for asset in release["assets"]:
            name = asset["name"]
            assert name not in seen and name in expected
            seen.add(name)
            assert asset["digest"] == expected[name]["digest"] and asset["size"] == expected[name]["size"] and asset["state"] == "uploaded", f"Existing draft differs: {name}"
        return release, seen

    for name in sorted(expected):
        _, seen = check()
        if name not in seen:
            gh("release", "upload", tag, str(root / name), "--repo", REPO)
    release, seen = check()
    assert seen == set(expected)
    report = {"tag": tag, "assets": len(seen), "draft": True, "platforms": ["win-x64", "linux-x64"], "url": release["url"]}
    print(json.dumps(report))
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write("Verified draft ready; native installer acceptance and explicit publication remain required.\n\n```json\n" + json.dumps(report, indent=2) + "\n```\n")


if __name__ == "__main__":
    main()
