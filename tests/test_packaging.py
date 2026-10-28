"""Dedicated test suite for Windows packaging, SHA-256 verification, and license compliance."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FETCH_SCRIPT = _REPO_ROOT / "packaging" / "fetch_ffmpeg_windows.py"
_SPEC = importlib.util.spec_from_file_location("fetch_ffmpeg_windows", _FETCH_SCRIPT)
assert _SPEC and _SPEC.loader
_fetch_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_fetch_mod)

compute_sha256 = _fetch_mod.compute_sha256
extract_verified_archive = _fetch_mod.extract_verified_archive
verify_checksum = _fetch_mod.verify_checksum


class WindowsPackagingTestSuite(unittest.TestCase):
    """Tests Windows packaging scripts, SHA-256 verification, and spec/Inno configurations."""

    def setUp(self) -> None:
        self.repo_root = _REPO_ROOT
        self.temp_dir = Path(tempfile.mkdtemp(prefix="pocketvideo_pkg_test_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_sha256_verification_accepts_valid_and_rejects_tampered_archive(self) -> None:
        dummy_zip = self.temp_dir / "ffmpeg-dummy-win64-gpl.zip"
        with zipfile.ZipFile(dummy_zip, "w") as zf:
            zf.writestr("ffmpeg-n7.1/bin/ffmpeg.exe", b"MZ_DUMMY_FFMPEG_BINARY")
            zf.writestr("ffmpeg-n7.1/bin/ffprobe.exe", b"MZ_DUMMY_FFPROBE_BINARY")
            zf.writestr("ffmpeg-n7.1/LICENSE.txt", b"GNU GENERAL PUBLIC LICENSE Version 3")

        expected_hash = hashlib.sha256(dummy_zip.read_bytes()).hexdigest()
        self.assertEqual(compute_sha256(dummy_zip), expected_hash)

        # Valid checksum passes
        verify_checksum(dummy_zip, expected_hash)

        # Tampered checksum raises ValueError
        with self.assertRaises(ValueError):
            verify_checksum(dummy_zip, "0" * 64)

        # Extract and verify layout
        vendor_out = self.temp_dir / "vendor_ffmpeg"
        extract_verified_archive(dummy_zip, vendor_out)
        self.assertTrue((vendor_out / "bin" / "ffmpeg.exe").exists())
        self.assertTrue((vendor_out / "bin" / "ffprobe.exe").exists())
        self.assertTrue((vendor_out / "LICENSE.txt").exists())

    def test_02_pyinstaller_spec_and_inno_setup_files_valid(self) -> None:
        spec_path = self.repo_root / "packaging" / "pocketvideo.spec"
        iss_path = self.repo_root / "packaging" / "installer.iss"
        ps1_path = self.repo_root / "packaging" / "build_windows.ps1"

        self.assertTrue(spec_path.exists())
        self.assertTrue(iss_path.exists())
        self.assertTrue(ps1_path.exists())

        spec_content = spec_path.read_text(encoding="utf-8")
        self.assertIn("ffmpeg.exe", spec_content)
        self.assertIn("ffprobe.exe", spec_content)
        self.assertIn("THIRD_PARTY_NOTICES.md", spec_content)

        iss_content = iss_path.read_text(encoding="utf-8")
        self.assertIn("PocketVideo-Setup-", iss_content)
        self.assertIn("ArchitecturesInstallIn64BitMode=x64compatible", iss_content)

    def test_03_windows_ffmpeg_download_is_pinned_to_audited_release_asset(self) -> None:
        """Prevent republishing the stale 404 asset or an unaudited digest.

        Asset URL and SHA-256 are from the BtbN GitHub release asset metadata
        (autobuild-2026-10-27-18-38, asset ID 682510708).
        """
        self.assertEqual(
            _fetch_mod.PINNED_FFMPEG_URL,
            "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
            "autobuild-2026-10-27-18-38/ffmpeg-N-128121-g86444dd368-win64-gpl.zip",
        )
        self.assertEqual(
            _fetch_mod.PINNED_FFMPEG_SHA256,
            "1eb70a550388567bc2abbb2d164814f722f36a034fd927dcdfb217bc7e94930c",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
