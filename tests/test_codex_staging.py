import base64
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import stage_codex


class Response(io.BytesIO):
    def geturl(self):
        return stage_codex.URL


def package(extra=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        content = json.dumps({"name": "@openai/codex", "version": "0.162.1-linux-x64"}).encode()
        member = tarfile.TarInfo("package/package.json")
        member.size = len(content)
        tar.addfile(member, io.BytesIO(content))
        if extra:
            tar.addfile(extra)
    return output.getvalue()


class StagingTests(unittest.TestCase):
    def test_approved_public_staging_never_executes_and_refuses_overwrite(self):
        archive = package()
        integrity = "sha512-" + base64.b64encode(hashlib.sha512(archive).digest()).decode()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(stage_codex, "urlopen", return_value=Response(archive)), \
             patch.object(stage_codex, "PACKAGE_INTEGRITY", integrity), \
             patch("subprocess.Popen", side_effect=AssertionError("must not execute")):
            destination = Path(directory) / "package"
            stage_codex.stage(destination)
            self.assertEqual(json.loads((destination / "receipt.json").read_bytes())["status"], "staged-not-executed")
            self.assertEqual((destination / "content/package/package.json").stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                stage_codex.stage(destination)

    def test_integrity_mismatch_never_unpacks(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(stage_codex, "urlopen", return_value=Response(package())):
            destination = Path(directory) / "package"
            with self.assertRaisesRegex(ValueError, "integrity mismatch"):
                stage_codex.stage(destination)
            self.assertFalse((destination / "content").exists())

    def test_archive_links_and_escape_paths_rejected_without_extraction(self):
        for name, kind in (("../escaped", tarfile.REGTYPE), ("package/link", tarfile.SYMTYPE),
                           ("/absolute", tarfile.REGTYPE), ("package/hardlink", tarfile.LNKTYPE)):
            member = tarfile.TarInfo(name)
            member.type, member.linkname = kind, "/etc/passwd"
            archive = package(member)
            integrity = "sha512-" + base64.b64encode(hashlib.sha512(archive).digest()).decode()
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory, \
                 patch.object(stage_codex, "urlopen", return_value=Response(archive)), \
                 patch.object(stage_codex, "PACKAGE_INTEGRITY", integrity):
                destination = Path(directory) / "package"
                with self.assertRaisesRegex(ValueError, "unsafe"):
                    stage_codex.stage(destination)
                self.assertEqual(list((destination / "content").iterdir()), [])
