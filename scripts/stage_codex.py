"""Stage the approved public Codex package; never execute it on the host."""

import argparse
import base64
import hashlib
import json
import os
import tarfile
import time
import signal
from pathlib import Path, PurePosixPath
from urllib.request import urlopen

from agent_evals.codex_smoke import PACKAGE_INTEGRITY
from agent_evals.store import private_directory, write_new

URL = "https://registry.npmjs.org/@openai/codex/-/codex-0.162.1-linux-x64.tgz"
MAX_ARCHIVE = 256 * 1024 * 1024
MAX_CONTENT = 512 * 1024 * 1024


def stage(destination: Path):
    private_directory(destination.parent)
    destination.mkdir(mode=0o700)  # Exclusive; incomplete attempts are preserved.
    archive = destination / "package.tgz"
    sha512 = hashlib.sha512()
    total = 0
    deadline = time.monotonic() + 120
    with urlopen(URL, timeout=30) as response, archive.open("xb") as output:
        archive.chmod(0o600)
        # Cross-origin redirects are not part of the pinned package acquisition.
        if response.geturl() != URL:
            raise ValueError("unexpected package download redirect")
        while chunk := response.read1(1024 * 1024):
            if time.monotonic() >= deadline:
                raise ValueError("public package acquisition deadline exceeded")
            total += len(chunk)
            if total > MAX_ARCHIVE:
                raise ValueError("public package exceeds archive limit")
            sha512.update(chunk)
            output.write(chunk)
    observed = "sha512-" + base64.b64encode(sha512.digest()).decode()
    if observed != PACKAGE_INTEGRITY:
        raise ValueError("public Codex package integrity mismatch; refuse unpacking")
    content = destination / "content"
    content.mkdir(mode=0o700)
    entries = []
    total_content = 0
    seen = set()
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        if len(members) > 1000:
            raise ValueError("too many public package entries")
        for member in members:
            name = PurePosixPath(member.name)
            if (name.is_absolute() or ".." in name.parts or str(name) != member.name.rstrip("/")
                    or "\\" in member.name or any(ord(c) < 32 for c in member.name)
                    or not name.parts or name.parts[0] != "package" or member.name in seen
                    or not (member.isfile() or member.isdir())):
                raise ValueError("unsafe public package entry")
            seen.add(member.name)
            total_content += member.size
            if member.size < 0 or total_content > MAX_CONTENT:
                raise ValueError("public package exceeds content limit")
        for member in members:
            target = content / member.name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if member.isdir():
                target.mkdir(exist_ok=True, mode=0o700)
                continue
            stream = tar.extractfile(member)
            if stream is None:
                raise ValueError("missing public package content")
            sha = hashlib.sha256()
            written = 0
            with target.open("xb") as output:
                while chunk := stream.read(1024 * 1024):
                    written += len(chunk)
                    if written > member.size:
                        raise ValueError("public package member-size mismatch")
                    sha.update(chunk)
                    output.write(chunk)
            if written != member.size:
                raise ValueError("truncated public package member")
            target.chmod(0o700 if member.mode & 0o111 else 0o600)
            entries.append({"path": member.name, "size": written, "executable": bool(member.mode & 0o111),
                            "digest": "sha256:" + sha.hexdigest()})
    metadata = json.loads((content / "package/package.json").read_bytes())
    if metadata.get("name") != "@openai/codex" or metadata.get("version") != "0.162.1-linux-x64":
        raise ValueError("public package identity mismatch")
    write_new(destination / "receipt.json", {"url": URL, "integrity": observed, "compressed_bytes": total,
                                            "content_bytes": total_content, "entries": entries,
                                            "status": "staged-not-executed", "inference_requests": 0})
    return destination


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--approve-public-download", action="store_true")
    args = parser.parse_args()
    if not args.approve_public_download:
        parser.error("public setup download requires explicit approval")
    signal.alarm(120)  # Independent CLI-process deadline, even for a progressing response.
    try:
        print(stage(args.out))
    finally:
        signal.alarm(0)
