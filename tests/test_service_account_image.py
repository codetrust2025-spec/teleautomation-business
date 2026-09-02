"""Service account screenshots live on the volume, not in the vault row.

`credentials.json` is read whole on every Data Room request and rewritten whole
on every edit. A base64 screenshot inside a row would make every unrelated save
slower and the file unreadable, so the bytes go to DATA_DIR the way offer-letter
PDFs already do and the row keeps a reference.

The type check is the other half. A browser's content-type and a filename are
both chosen by whoever uploads, so the format is decided from the bytes.
"""

from __future__ import annotations

import pytest

from features import data_room_credentials_store as creds


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 64


class TestTheFormatIsDecidedFromTheBytes:
    @pytest.mark.parametrize("data,extension,media_type", [
        (PNG, "png", "image/png"),
        (JPEG, "jpg", "image/jpeg"),
        (WEBP, "webp", "image/webp"),
    ])
    def test_supported_images_are_recognised(self, data, extension, media_type):
        assert creds._account_image_kind(data) == (extension, media_type)

    @pytest.mark.parametrize("data,why", [
        (b"%PDF-1.7 fake", "a PDF renamed .png is still a PDF"),
        (b"GIF89a" + b"\x00" * 32, "GIF is not in the supported set"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "SVG can carry script"),
        (b"MZ\x90\x00", "a Windows executable"),
        (b"", "nothing at all"),
        (b"RIFF\x00\x00\x00\x00AVI ", "RIFF, but not WebP"),
    ])
    def test_everything_else_is_refused(self, data, why):
        assert creds._account_image_kind(data) is None, why

    def test_a_truncated_webp_header_is_not_accepted(self):
        """RIFF with fewer than twelve bytes cannot carry the WEBP marker, and
        slicing past the end would silently compare empty to empty."""
        assert creds._account_image_kind(b"RIFF\x00\x00\x00\x00") is None


class TestSavingRefusesWhatItShould:
    def test_an_unknown_format_is_rejected(self, monkeypatch):
        monkeypatch.setattr(creds, "find_service_account", lambda _id: {"id": "acct"})
        with pytest.raises(ValueError, match="PNG, JPG, JPEG and WebP"):
            creds.save_service_account_image("acct", b"GIF89a" + b"\x00" * 32)

    def test_an_empty_upload_is_rejected(self, monkeypatch):
        monkeypatch.setattr(creds, "find_service_account", lambda _id: {"id": "acct"})
        with pytest.raises(ValueError, match="Empty upload"):
            creds.save_service_account_image("acct", b"")

    def test_an_oversized_image_is_rejected_before_it_is_written(self, monkeypatch):
        monkeypatch.setattr(creds, "find_service_account", lambda _id: {"id": "acct"})
        too_big = PNG + b"\x00" * creds._MAX_ACCOUNT_IMAGE_BYTES
        with pytest.raises(ValueError, match="too large"):
            creds.save_service_account_image("acct", too_big)

    def test_an_unknown_account_is_rejected(self, monkeypatch):
        monkeypatch.setattr(creds, "find_service_account", lambda _id: None)
        with pytest.raises(FileNotFoundError):
            creds.save_service_account_image("nope", PNG)


class TestTheRowKeepsAReferenceNotTheBytes:
    def test_only_metadata_is_written_to_the_vault(self, monkeypatch, tmp_path):
        written = {}
        monkeypatch.setattr(creds, "_ACCOUNT_IMAGE_DIR", str(tmp_path))
        monkeypatch.setattr(creds, "find_service_account", lambda _id: {"id": "acct"})
        monkeypatch.setattr(
            creds, "update_vault_item",
            lambda section, item_id, updates: written.update(updates) or {},
        )

        creds.save_service_account_image("acct", PNG, "screenshot.png")

        assert written["has_image"] is True
        assert written["image_filename"] == "screenshot.png"
        assert written["image_type"] == "image/png"
        assert written["image_size_kb"] >= 1
        # The bytes are on disk, and nothing resembling them is in the row.
        assert (tmp_path / "acct.png").read_bytes() == PNG
        for value in written.values():
            assert not (isinstance(value, str) and len(value) > 400)

    def test_replacing_with_another_format_leaves_no_orphan(self, monkeypatch, tmp_path):
        """A PNG replaced by a WebP must not leave both files behind, or the
        next read could resolve the stale one."""
        monkeypatch.setattr(creds, "_ACCOUNT_IMAGE_DIR", str(tmp_path))
        monkeypatch.setattr(creds, "find_service_account", lambda _id: {"id": "acct"})
        monkeypatch.setattr(creds, "update_vault_item", lambda *a, **k: {})

        creds.save_service_account_image("acct", PNG, "a.png")
        assert (tmp_path / "acct.png").exists()

        creds.save_service_account_image("acct", WEBP, "a.webp")
        assert (tmp_path / "acct.webp").exists()
        assert not (tmp_path / "acct.png").exists()

    def test_removing_clears_the_file_and_the_reference(self, monkeypatch, tmp_path):
        cleared = {}
        monkeypatch.setattr(creds, "_ACCOUNT_IMAGE_DIR", str(tmp_path))
        monkeypatch.setattr(creds, "find_service_account", lambda _id: {"id": "acct"})
        monkeypatch.setattr(
            creds, "update_vault_item",
            lambda section, item_id, updates: cleared.update(updates) or {},
        )

        creds.save_service_account_image("acct", PNG, "a.png")
        creds.delete_service_account_image("acct")

        assert not (tmp_path / "acct.png").exists()
        assert cleared["has_image"] is False
        # update_vault_item deletes a key sent as None, so the reference goes
        # rather than lingering as an empty string.
        assert cleared["image_filename"] is None
        assert cleared["image_type"] is None
