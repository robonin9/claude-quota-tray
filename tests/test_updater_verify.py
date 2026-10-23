"""Signed updates: nothing is installed unless signature, version and hash pass."""

import base64
import hashlib
import importlib.util
import io
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import _path  # noqa: F401

import update_keys
import updater

try:
    from Crypto.PublicKey import ECC
    from Crypto.Signature import eddsa
except ImportError:  # pragma: no cover
    ECC = None

OWNER, REPO = "owner", "repo"
BASE = f"https://github.com/{OWNER}/{REPO}/releases/download"


def _zip_source(version: str, marker: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        root = f"claude-quota-tray-v{version}"
        zf.writestr(f"{root}/src/main.py", f"# {marker}\n")
        zf.writestr(f"{root}/requirements.txt", "")
    return buf.getvalue()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@unittest.skipIf(ECC is None, "pycryptodome not installed")
class SignedUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = ECC.generate(curve="ed25519")
        cls.public_hex = cls.key.public_key().export_key(format="raw").hex()
        cls.other_key = ECC.generate(curve="ed25519")

    def setUp(self):
        self._keys = mock.patch.object(update_keys, "UPDATE_PUBLIC_KEYS", [self.public_hex])
        self._keys.start()
        self.addCleanup(self._keys.stop)
        # Fixture releases are v0.4.0; pin the "installed" version so these
        # tests don't depend on the real APP_VERSION.
        self._ver = mock.patch.object(updater.config, "APP_VERSION", "0.3.0")
        self._ver.start()
        self.addCleanup(self._ver.stop)
        self.tmp = Path(tempfile.mkdtemp(prefix="cqt-upd-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = self.tmp / "install"
        (self.project / "src").mkdir(parents=True)
        (self.project / "src" / "main.py").write_text("# old\n", encoding="utf-8")
        self.files: dict = {}
        self.release_json: dict = {}
        self.make_release()

    # --- fixture helpers --------------------------------------------------

    def sign(self, data: bytes, key=None) -> bytes:
        sig = eddsa.new(key or self.key, "rfc8032").sign(data)
        return base64.b64encode(sig) + b"\n"

    def make_release(self, tag="v0.4.0", sums_version="0.4.0", *, sign_with=None,
                     include_sig=True, include_sums=True, skip_entry=None,
                     drop_version_line=False, owner=OWNER, repo=REPO):
        version = tag.lstrip("v")
        zip_name = f"claude-quota-tray-v{version}.zip"
        exe_name = "ClaudeQuotaTray.exe"
        payload = {
            zip_name: _zip_source(version, f"new {version}"),
            exe_name: b"MZ new exe " + version.encode(),
        }
        lines = [] if drop_version_line else [f"version: {sums_version}"]
        for name, data in payload.items():
            if name != skip_entry:
                lines.append(f"{_sha(data)}  {name}")
        sums = ("\n".join(lines) + "\n").encode()
        base = f"https://github.com/{owner}/{repo}/releases/download/{tag}"
        assets = {name: data for name, data in payload.items()}
        if include_sums:
            assets["SHA256SUMS"] = sums
        if include_sig:
            assets["SHA256SUMS.sig"] = self.sign(sums, sign_with)
        self.files = {f"{base}/{n}": d for n, d in assets.items()}
        self.release_json = {
            "tag_name": tag,
            "html_url": f"https://github.com/{OWNER}/{REPO}/releases/tag/{tag}",
            "zipball_url": f"https://api.github.com/repos/{OWNER}/{REPO}/zipball/{tag}",
            "assets": [{"name": n, "browser_download_url": f"{base}/{n}"} for n in assets],
        }

    def tamper(self, name: str, data: bytes):
        for url in self.files:
            if url.endswith("/" + name):
                self.files[url] = data

    def network(self):
        def fake_json(url, timeout=30.0):
            return self.release_json

        def fake_download(url, dest, timeout=120.0):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(self.files[url])

        def fake_small(url, timeout=30.0):
            return self.files[url]

        return mock.patch.multiple(
            updater, _http_get_json=fake_json, _download=fake_download,
            _download_small=fake_small,
        )

    def apply(self, prefer_exe=False):
        with self.network():
            return updater.apply_update(self.project, f"{OWNER}/{REPO}", prefer_exe=prefer_exe)

    def check(self, current="0.3.0"):
        with self.network():
            return updater.check_for_update(f"{OWNER}/{REPO}", current_version=current)

    def installed(self) -> str:
        return (self.project / "src" / "main.py").read_text(encoding="utf-8")

    # --- happy path -------------------------------------------------------

    def test_valid_release_is_reported_and_installed(self):
        result = self.check()
        self.assertTrue(result.update_available, result)
        self.assertEqual(self.apply(), "Updated to v0.4.0")
        self.assertIn("new 0.4.0", self.installed())

    def test_valid_exe_update_saves_verified_file(self):
        msg = self.apply(prefer_exe=True)
        self.assertIn("ClaudeQuotaTray.exe", msg)
        self.assertEqual((self.project / "ClaudeQuotaTray.exe").read_bytes(),
                         b"MZ new exe 0.4.0")

    # --- tampering --------------------------------------------------------

    def test_tampered_source_zip_is_refused_and_nothing_changes(self):
        self.tamper("claude-quota-tray-v0.4.0.zip", _zip_source("0.4.0", "evil"))
        # The message names the release asset, not the temp download file.
        with self.assertRaisesRegex(updater.UpdateVerificationError,
                                    r"^claude-quota-tray-v0\.4\.0\.zip does not match"):
            self.apply()
        self.assertEqual(self.installed(), "# old\n")

    def test_tampered_exe_is_refused_and_staging_removed(self):
        self.tamper("ClaudeQuotaTray.exe", b"MZ evil")
        with self.assertRaisesRegex(updater.UpdateVerificationError,
                                    r"^ClaudeQuotaTray\.exe does not match"):
            self.apply(prefer_exe=True)
        self.assertFalse((self.project / "ClaudeQuotaTray.exe.new").exists())
        self.assertFalse((self.project / "ClaudeQuotaTray.exe").exists())

    def test_tampered_sums_fails_signature(self):
        sums_url = next(u for u in self.files if u.endswith("/SHA256SUMS"))
        self.files[sums_url] = self.files[sums_url].replace(b"version: 0.4.0", b"version: 0.4.1")
        result = self.check()
        self.assertFalse(result.update_available)
        self.assertIn("signature", result.verify_error)
        with self.assertRaises(updater.UpdateVerificationError):
            self.apply()

    def test_signature_from_untrusted_key_is_refused(self):
        self.make_release(sign_with=self.other_key)
        with self.assertRaises(updater.UpdateVerificationError):
            self.apply()

    def test_no_trusted_keys_refuses_everything(self):
        with mock.patch.object(update_keys, "UPDATE_PUBLIC_KEYS", []):
            with self.assertRaises(updater.UpdateVerificationError):
                self.apply()
            self.assertIsNotNone(self.check().verify_error)

    # --- SUMS content -------------------------------------------------------

    def test_asset_missing_from_sums_is_refused(self):
        self.make_release(skip_entry="claude-quota-tray-v0.4.0.zip")
        with self.assertRaisesRegex(updater.UpdateVerificationError, "not listed"):
            self.apply()
        self.assertEqual(self.installed(), "# old\n")

    def test_missing_version_line_is_refused(self):
        self.make_release(drop_version_line=True)
        with self.assertRaisesRegex(updater.UpdateVerificationError, "version line"):
            self.apply()

    def test_signed_version_must_match_tag(self):
        self.make_release(tag="v0.4.0", sums_version="0.5.0")
        with self.assertRaisesRegex(updater.UpdateVerificationError, "does not match"):
            self.apply()
        self.assertIsNotNone(self.check().verify_error)

    # --- versions -----------------------------------------------------------

    def test_same_version_is_not_newer(self):
        self.make_release(tag="v0.3.0", sums_version="0.3.0")
        self.assertFalse(self.check().update_available)
        with self.assertRaises(updater.UpdateNotNewerError):
            self.apply()

    def test_signed_downgrade_is_refused(self):
        self.make_release(tag="v0.2.0", sums_version="0.2.0")
        with self.assertRaises(updater.UpdateNotNewerError):
            self.apply()
        self.assertEqual(self.installed(), "# old\n")

    def test_prerelease_tag_is_never_an_update(self):
        self.make_release(tag="v0.4.0-rc1", sums_version="0.4.0-rc1")
        result = self.check()
        self.assertFalse(result.update_available)
        with self.assertRaises(updater.UpdateVerificationError):
            self.apply()

    def test_strict_version_parsing(self):
        self.assertEqual(updater._parse_version("v0.4.0"), (0, 4, 0))
        for bad in ("0.4.0-rc1", "0.4", "0.4.0+build", "latest", ""):
            with self.assertRaises(ValueError, msg=bad):
                updater._parse_version(bad)

    # --- missing files / sources -----------------------------------------

    def test_release_without_signature_is_unsigned(self):
        self.make_release(include_sig=False)
        result = self.check()
        self.assertFalse(result.update_available)
        self.assertTrue(result.unsigned)
        with self.assertRaises(updater.UpdateVerificationError):
            self.apply()

    def test_zipball_only_release_is_refused(self):
        self.make_release(include_sig=False, include_sums=False)
        self.release_json["assets"] = []
        with self.network():
            info = updater.fetch_latest_release(OWNER, REPO)
        self.assertIsNone(info.source_zip_url)  # zipball is never used
        with self.assertRaises(updater.UpdateVerificationError):
            self.apply()
        self.assertEqual(self.installed(), "# old\n")

    # --- URLs -------------------------------------------------------------

    def test_assets_from_another_repo_are_refused(self):
        self.make_release(owner="attacker", repo="repo")
        with self.assertRaisesRegex(updater.UpdateVerificationError, "release download"):
            self.apply()

    def test_check_asset_url(self):
        updater._check_asset_url(f"{BASE}/v1.0.0/x.zip", OWNER, REPO)
        for bad in (f"http://github.com/{OWNER}/{REPO}/releases/download/v1/x",
                    f"https://example.com/{OWNER}/{REPO}/releases/download/v1/x",
                    f"https://github.com/{OWNER}/other/releases/download/v1/x"):
            with self.assertRaises(updater.UpdateVerificationError, msg=bad):
                updater._check_asset_url(bad, OWNER, REPO)

    def test_redirect_to_http_is_refused(self):
        handler = updater._HttpsOnlyRedirects()
        req = updater.Request(f"{BASE}/v1.0.0/x.zip")
        with self.assertRaises(updater.UpdateVerificationError):
            handler.redirect_request(req, None, 302, "Found", {},
                                     "http://objects.githubusercontent.com/x")
        ok = handler.redirect_request(req, None, 302, "Found", {},
                                      "https://objects.githubusercontent.com/x")
        self.assertEqual(ok.full_url, "https://objects.githubusercontent.com/x")

    # --- the CI signing script and the app agree ----------------------------

    def test_release_script_output_verifies_in_the_app(self):
        """scripts/sign_release.py (what release.yml runs) → updater accepts it."""
        script = Path(_path.SRC).parent / "scripts" / "sign_release.py"
        spec = importlib.util.spec_from_file_location("sign_release", script)
        sign_release = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sign_release)

        out = self.tmp / "release"
        out.mkdir()
        (out / "ClaudeQuotaTray.exe").write_bytes(b"MZ built")
        (out / "claude-quota-tray-v0.4.0.zip").write_bytes(_zip_source("0.4.0", "ci"))
        files = [out / "ClaudeQuotaTray.exe", out / "claude-quota-tray-v0.4.0.zip"]
        pem_b64 = base64.b64encode(self.key.export_key(format="PEM").encode()).decode()
        with mock.patch.dict(os.environ, {"UPDATE_SIGNING_KEY": pem_b64}):
            self.assertEqual(sign_release.cmd_sums("0.4.0", out / "SHA256SUMS", files), 0)
            self.assertEqual(sign_release.cmd_sign(out / "SHA256SUMS", out / "SHA256SUMS.sig"), 0)
        self.assertEqual(sign_release.cmd_verify(out / "SHA256SUMS", out / "SHA256SUMS.sig"), 0)

        release = updater.ReleaseInfo(
            version="0.4.0", tag="v0.4.0", html_url="", source_zip_url=None,
            exe_url=None, exe_name=None,
        )
        entries = updater.verify_release(
            (out / "SHA256SUMS").read_bytes(), (out / "SHA256SUMS.sig").read_bytes(),
            release, current_version="0.3.0",
        )
        self.assertEqual(entries["ClaudeQuotaTray.exe"], _sha(b"MZ built"))
        updater.verify_asset(out / "ClaudeQuotaTray.exe", entries["ClaudeQuotaTray.exe"])

    def test_release_script_refuses_key_not_in_update_keys(self):
        script = Path(_path.SRC).parent / "scripts" / "sign_release.py"
        spec = importlib.util.spec_from_file_location("sign_release", script)
        sign_release = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sign_release)
        out = self.tmp / "release2"
        out.mkdir()
        (out / "a.zip").write_bytes(b"x")
        pem = self.other_key.export_key(format="PEM")
        with mock.patch.dict(os.environ, {"UPDATE_SIGNING_KEY": pem}):
            sign_release.cmd_sums("0.4.0", out / "SHA256SUMS", [out / "a.zip"])
            sign_release.cmd_sign(out / "SHA256SUMS", out / "SHA256SUMS.sig")
        self.assertEqual(sign_release.cmd_verify(out / "SHA256SUMS", out / "SHA256SUMS.sig"), 1)


if __name__ == "__main__":
    unittest.main()
