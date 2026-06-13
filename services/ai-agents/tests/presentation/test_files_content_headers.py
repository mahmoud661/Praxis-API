"""content_disposition_for builds a header-safe Content-Disposition."""

from app.presentation.controllers.files_controller import content_disposition_for


def test_images_render_inline():
    header = content_disposition_for("image/png", "photo.png")
    assert header.startswith("inline; ")
    assert 'filename="photo.png"' in header


def test_non_images_download_as_attachment():
    for mime in ("application/pdf", "text/plain", "application/json"):
        assert content_disposition_for(mime, "doc.pdf").startswith("attachment; ")


def test_filename_cannot_break_out_of_the_header():
    # CRLF + quote injection collapses to underscores in the ASCII fallback.
    header = content_disposition_for("text/plain", 'evil"\r\nX-Injected: 1')
    assert "\r" not in header
    assert "\n" not in header
    assert 'filename="evil___X-Injected: 1"' in header


def test_unicode_filename_survives_via_rfc5987_form():
    header = content_disposition_for("application/pdf", "résumé.pdf")
    assert "filename*=UTF-8''r%C3%A9sum%C3%A9.pdf" in header
    assert 'filename="r_sum_.pdf"' in header


def test_empty_filename_falls_back():
    assert 'filename="file"' in content_disposition_for("text/plain", "")
