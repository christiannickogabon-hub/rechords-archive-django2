import tempfile
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Document, DocumentType

User = get_user_model()


class ArchiveTestCase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            "admin1", password="pw-for-tests-123", role=User.Role.ADMIN
        )
        self.staff = User.objects.create_user(
            "staff1", password="pw-for-tests-123", role=User.Role.USER
        )

    def make(self, doc_type=DocumentType.MEMORANDUM, **kwargs):
        return Document.objects.create(
            doc_type=doc_type,
            series_year=kwargs.pop("series_year", 2026),
            subject=kwargs.pop("subject", "Test subject"),
            date_issued=date(2026, 1, 15),
            created_by=self.admin,
            **kwargs,
        )


class ControlNumberTests(ArchiveTestCase):
    def test_numbers_increment_per_type_and_year(self):
        a = self.make()
        b = self.make()
        c = self.make(doc_type=DocumentType.OFFICE_ORDER)
        d = self.make(series_year=2025)
        self.assertEqual((a.number, b.number), (1, 2))
        self.assertEqual(c.number, 1)
        self.assertEqual(d.number, 1)

    def test_control_number_format(self):
        doc = self.make()
        self.assertEqual(doc.control_number, "MEMORANDUM No. 001, s. 2026")

    def test_endorsement_prefix(self):
        doc = self.make(doc_type=DocumentType.INDORSEMENT, indorsement_level=2)
        self.assertEqual(doc.control_number, "2nd ENDORSEMENT No. 001, s. 2026")


class AccessControlTests(ArchiveTestCase):
    def test_anonymous_is_sent_to_sign_in(self):
        response = self.client.get(reverse("documents:document_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_staff_user_can_search_but_not_file(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("documents:document_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("documents:document_create")).status_code, 403)

    def test_staff_user_cannot_reach_admin_pages(self):
        self.client.force_login(self.staff)
        for name in ["documents:audit_trail", "documents:recycle_bin", "accounts:user_list"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_admin_can_file(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("documents:document_create")).status_code, 200)

    def test_confidential_records_are_hidden_from_staff(self):
        secret = self.make(classification=Document.Classification.CONFIDENTIAL)
        self.client.force_login(self.staff)
        response = self.client.get(secret.get_absolute_url())
        self.assertEqual(response.status_code, 404)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(secret.get_absolute_url()).status_code, 200)


class SearchTests(ArchiveTestCase):
    def test_keyword_and_type_filters(self):
        self.make(subject="Travel order guidelines")
        self.make(doc_type=DocumentType.EXECUTIVE_ORDER, subject="Budget realignment")
        self.client.force_login(self.staff)

        hits = self.client.get(reverse("documents:document_list"), {"q": "travel"})
        self.assertEqual(len(hits.context["documents"]), 1)

        hits = self.client.get(reverse("documents:document_list"), {"doc_type": "EO"})
        self.assertEqual(len(hits.context["documents"]), 1)


class SoftDeleteTests(ArchiveTestCase):
    def test_removed_record_leaves_search_but_keeps_its_number(self):
        doc = self.make()
        doc.soft_delete()
        self.assertFalse(Document.objects.visible_to(self.staff).filter(pk=doc.pk).exists())
        replacement = self.make(subject="Next one")
        self.assertEqual(replacement.number, 2)


class WatermarkDownloadTests(ArchiveTestCase):
    """The served copy carries the stamp; the stored original is untouched."""

    def _pdf_bytes(self):
        from io import BytesIO

        from reportlab.pdfgen import canvas

        buf = BytesIO()
        pdf = canvas.Canvas(buf)
        pdf.drawString(72, 720, "Original body text")
        pdf.showPage()
        pdf.save()
        return buf.getvalue()

    def test_downloaded_pdf_is_stamped_certified_true_copy(self):
        from io import BytesIO
        from tempfile import mkdtemp
        from unittest.mock import patch

        from django.core.files.storage import FileSystemStorage
        from django.core.files.uploadedfile import SimpleUploadedFile
        from pypdf import PdfReader

        local = FileSystemStorage(location=mkdtemp())
        with patch.object(Document._meta.get_field("file"), "storage", local):
            doc = self.make(file=SimpleUploadedFile("memo.pdf", self._pdf_bytes(), "application/pdf"))
            self.client.force_login(self.admin)
            response = self.client.get(reverse("documents:document_download", args=[doc.pk]))
            self.assertEqual(response.status_code, 200)

            served = PdfReader(BytesIO(b"".join(response.streaming_content)))
            text = " ".join(page.extract_text() for page in served.pages)
            self.assertIn("CERTIFIED TRUE COPY", text)
            self.assertIn("Original body text", text)

            doc.refresh_from_db()
            stored = PdfReader(BytesIO(doc.file.read()))
            self.assertNotIn("CERTIFIED TRUE COPY", stored.pages[0].extract_text())



class AuditTrailAccessTests(ArchiveTestCase):
    def setUp(self):
        super().setUp()
        self.superadmin = User.objects.create_user(
            "super1", password="pw-for-tests-123", role=User.Role.SUPERADMIN
        )

    def test_only_the_super_administrator_sees_the_audit_trail(self):
        url = reverse("documents:audit_trail")
        for who in (self.staff, self.admin):
            self.client.force_login(who)
            self.assertEqual(self.client.get(url).status_code, 403, who.username)
        self.client.force_login(self.superadmin)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_the_administrator_keeps_the_recycle_bin(self):
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(reverse("documents:recycle_bin")).status_code, 200)


class DocumentFolderTests(ArchiveTestCase):
    def setUp(self):
        super().setUp()
        from .models import DocumentFolder

        self.Folder = DocumentFolder

    def add(self, **extra):
        self.client.force_login(self.admin)
        payload = {"label": "Resolution", "code": "RES", "long_name": "RESOLUTION",
                   "icon": "icon-file-text", "tone": "rose"}
        payload.update(extra)
        return self.client.post(reverse("documents:folder_create"), payload)

    def test_builtin_folders_were_seeded(self):
        codes = set(self.Folder.objects.values_list("code", flat=True))
        self.assertEqual(codes, {"MEMO", "IND", "OO", "EO"})
        self.assertTrue(all(self.Folder.objects.values_list("is_builtin", flat=True)))

    def test_administrator_can_open_a_folder_and_file_under_it(self):
        self.assertEqual(self.add().status_code, 302)
        folder = self.Folder.objects.get(code="RES")
        self.assertFalse(folder.is_builtin)

        doc = self.make(doc_type="RES", subject="Adopting the budget")
        self.assertEqual(doc.control_number, f"RESOLUTION No. 001, s. {doc.series_year}")
        self.assertEqual(doc.type_label, "Resolution")

    def test_new_folder_appears_on_the_overview(self):
        self.add()
        self.client.force_login(self.admin)
        cards = self.client.get(reverse("documents:dashboard")).context["type_cards"]
        self.assertIn("Resolution", [c["label"] for c in cards])
        self.assertEqual(len(cards), 5)

    def test_numbering_is_separate_from_the_other_series(self):
        self.add()
        self.make(doc_type="RES", subject="First resolution")
        memo = self.make(subject="A memo")
        second = self.make(doc_type="RES", subject="Second resolution")
        self.assertEqual(second.number, 2)
        self.assertEqual(memo.number, 1)

    def test_duplicate_code_is_refused(self):
        self.add()
        response = self.add(label="Resolutions 2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.Folder.objects.filter(code="RES").count(), 1)

    def test_builtin_folder_cannot_be_removed(self):
        self.client.force_login(self.admin)
        memo = self.Folder.objects.get(code="MEMO")
        self.assertFalse(memo.can_be_removed)
        self.client.post(reverse("documents:folder_delete", args=[memo.pk]))
        self.assertTrue(self.Folder.objects.filter(code="MEMO").exists())

    def test_folder_holding_records_cannot_be_removed(self):
        self.add()
        folder = self.Folder.objects.get(code="RES")
        self.make(doc_type="RES", subject="Filed here")
        self.assertFalse(folder.can_be_removed)
        self.client.post(reverse("documents:folder_delete", args=[folder.pk]))
        self.assertTrue(self.Folder.objects.filter(code="RES").exists())

    def test_empty_folder_can_be_removed(self):
        self.add()
        folder = self.Folder.objects.get(code="RES")
        self.assertTrue(folder.can_be_removed)
        self.client.post(reverse("documents:folder_delete", args=[folder.pk]))
        self.assertFalse(self.Folder.objects.filter(code="RES").exists())

    def test_staff_user_cannot_open_a_folder(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("documents:folder_create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("documents:folder_list")).status_code, 403)

@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="archive-test-media-"))
class CertifiedDownloadTests(ArchiveTestCase):
    """Downloads leave as stamped PDFs, for every role that can see the record."""

    def make_with_file(self, name="scan.pdf", data=None, **kwargs):
        """Put the bytes under MEDIA_ROOT and point the record at them.

        The file field is backed by Cloudinary in this project, so the test
        writes to local media and lets the view's own fallback find it.
        """
        import os
        from reportlab.pdfgen import canvas as _canvas
        import io as _io
        from django.conf import settings

        if data is None:
            buffer = _io.BytesIO()
            pdf = _canvas.Canvas(buffer)
            pdf.drawString(80, 700, "Original page")
            pdf.save()
            data = buffer.getvalue()

        relative = f"archive/tests/{name}"
        target = os.path.join(settings.MEDIA_ROOT, *relative.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(data)

        doc = self.make(classification=Document.Classification.PUBLIC, **kwargs)
        Document.objects.filter(pk=doc.pk).update(file=relative)
        doc.refresh_from_db()
        return doc

    def read(self, response):
        return b"".join(response.streaming_content)

    def test_download_is_a_pdf_carrying_the_stamp(self):
        from pypdf import PdfReader
        import io as _io

        doc = self.make_with_file()
        self.client.force_login(self.admin)
        response = self.client.get(reverse("documents:document_download", args=[doc.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Disposition"].endswith('.pdf"'))

        body = self.read(response)
        self.assertTrue(body.startswith(b"%PDF-"))
        text = PdfReader(_io.BytesIO(body)).pages[0].extract_text()
        self.assertIn("CERTIFIED TRUE COPY", text)

    def test_a_staff_user_gets_the_same_stamped_pdf(self):
        from pypdf import PdfReader
        import io as _io

        doc = self.make_with_file()
        self.client.force_login(self.staff)
        response = self.client.get(reverse("documents:document_download", args=[doc.pk]))
        body = self.read(response)
        self.assertTrue(body.startswith(b"%PDF-"))
        text = PdfReader(_io.BytesIO(body)).pages[0].extract_text()
        self.assertIn("CERTIFIED TRUE COPY", text)

    def test_an_image_is_converted_to_pdf_before_stamping(self):
        from PIL import Image
        from pypdf import PdfReader
        import io as _io

        buffer = _io.BytesIO()
        Image.new("RGB", (600, 800), (250, 250, 250)).save(buffer, format="JPEG")
        doc = self.make_with_file(name="scan.jpg", data=buffer.getvalue())

        self.client.force_login(self.staff)
        response = self.client.get(reverse("documents:document_download", args=[doc.pk]))
        self.assertIn(".pdf", response["Content-Disposition"])
        body = self.read(response)
        text = PdfReader(_io.BytesIO(body)).pages[0].extract_text()
        self.assertIn("CERTIFIED TRUE COPY", text)

    def test_viewing_inline_is_not_stamped(self):
        doc = self.make_with_file()
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse("documents:document_download", args=[doc.pk]) + "?view=1"
        )
        self.assertNotIn("attachment", response["Content-Disposition"])

    def test_the_stamp_sits_in_the_lower_right_corner(self):
        """The stamp box must start past the middle of the page and near its foot."""
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas as _canvas
        from pypdf import PdfReader
        import io as _io

        from .watermark import _stamp_pdf

        buffer = _io.BytesIO()
        pdf = _canvas.Canvas(buffer, pagesize=A4)
        pdf.drawString(80, 700, "Original page")
        pdf.save()
        buffer.seek(0)

        stamped = _stamp_pdf(buffer, "TEST No. 001")
        page = PdfReader(_io.BytesIO(stamped.getvalue())).pages[0]
        width, height = float(page.mediabox.width), float(page.mediabox.height)

        places = []
        page.extract_text(visitor_text=lambda t, cm, tm, fd, fs: places.append(
            (t.strip(), tm[4], tm[5])) if t.strip() else None)
        marks = [p for p in places if "CERTIFIED TRUE COPY" in p[0]]
        self.assertTrue(marks, "the stamp text was not found on the page")
        x, y = marks[0][1], marks[0][2]
        self.assertGreater(x, width / 2, "the stamp is not on the right half")
        self.assertLess(y, height / 3, "the stamp is not near the foot of the page")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="archive-test-names-"))
class DownloadNameTests(CertifiedDownloadTests):
    """The file that lands in Downloads is named after the record."""

    def test_certified_pdf_is_named_after_the_control_number(self):
        doc = self.make_with_file()
        self.client.force_login(self.staff)
        response = self.client.get(reverse("documents:document_download", args=[doc.pk]))
        disposition = response["Content-Disposition"]
        self.assertIn(doc.control_number, disposition)
        self.assertIn("certified true copy", disposition)
        self.assertTrue(disposition.rstrip().endswith('.pdf"'))

    def test_an_image_download_keeps_the_pdf_name(self):
        from PIL import Image
        import io as _io

        buffer = _io.BytesIO()
        Image.new("RGB", (400, 500), (255, 255, 255)).save(buffer, format="JPEG")
        doc = self.make_with_file(name="shot.jpg", data=buffer.getvalue())
        self.client.force_login(self.staff)
        response = self.client.get(reverse("documents:document_download", args=[doc.pk]))
        self.assertIn(doc.control_number, response["Content-Disposition"])
        self.assertTrue(response["Content-Disposition"].rstrip().endswith('.pdf"'))

    def test_the_storage_key_never_reaches_the_user(self):
        doc = self.make_with_file(name="9f2c17ab4e5d4e0f.pdf")
        self.client.force_login(self.staff)
        response = self.client.get(reverse("documents:document_download", args=[doc.pk]))
        self.assertNotIn("9f2c17ab4e5d4e0f", response["Content-Disposition"])

    def test_name_is_safe_for_the_file_system(self):
        from documents.views import _download_name

        doc = self.make()
        name = _download_name(doc, ".pdf", 'Annex A: plan/draft*"v2"')
        for ch in '<>:"/' + chr(92) + '|?*':
            self.assertNotIn(ch, name)

    def test_an_uncertifiable_file_still_carries_the_record_name(self):
        doc = self.make_with_file(name="draft.docx", data=b"PK\x03\x04 not really a docx")
        self.client.force_login(self.staff)
        response = self.client.get(reverse("documents:document_download", args=[doc.pk]))
        disposition = response["Content-Disposition"]
        self.assertIn(doc.control_number, disposition)
        self.assertTrue(disposition.rstrip().endswith('.docx"'))
        self.assertNotIn("certified true copy", disposition)


class FolderYearTests(ArchiveTestCase):
    """?year= on a folder narrows it to one series year."""

    def setUp(self):
        super().setUp()
        self.old = self.make(subject="Older memo", series_year=2025,
                             classification=Document.Classification.PUBLIC)
        self.new = self.make(subject="Newer memo", series_year=2026,
                             classification=Document.Classification.PUBLIC)
        self.other = self.make(doc_type=DocumentType.OFFICE_ORDER, subject="An order",
                               series_year=2026,
                               classification=Document.Classification.PUBLIC)

    def folder(self, code="MEMO", **params):
        url = reverse("documents:document_folder", args=[code])
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        self.client.force_login(self.staff)
        return self.client.get(url)

    def test_opening_a_folder_shows_its_sub_folders_not_the_records(self):
        response = self.folder()
        html = response.content.decode()
        self.assertIsNone(response.context["folder_year"])
        self.assertIn("Open a series year", html)
        self.assertNotIn("<table class=\"registry\">", html)
        self.assertIn("2 records in this folder", html)

    def test_a_year_shows_only_that_series(self):
        response = self.folder(year=2025)
        subjects = [d.subject for d in response.context["documents"]]
        self.assertEqual(subjects, ["Older memo"])
        self.assertEqual(response.context["folder_year"], 2025)

    def test_the_year_does_not_leak_other_folders(self):
        response = self.folder(year=2026)
        subjects = [d.subject for d in response.context["documents"]]
        self.assertEqual(subjects, ["Newer memo"])

    def test_a_year_with_nothing_filed_is_empty_not_an_error(self):
        response = self.folder(year=1999)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["documents"]), 0)

    def test_rubbish_in_the_year_is_ignored(self):
        response = self.folder(year="banana")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["folder_year"])
        self.assertEqual(len(response.context["documents"]), 2)

    def test_the_folder_lists_a_sub_folder_for_each_series_year(self):
        subs = self.folder().context["series_folders"]
        self.assertEqual([s["year"] for s in subs], [2026, 2025])
        self.assertEqual({s["year"]: s["total"] for s in subs}, {2026: 1, 2025: 1})

    def test_the_sub_folders_stay_visible_inside_one_of_them(self):
        response = self.folder(year=2025)
        self.assertEqual(len(response.context["series_folders"]), 2)
        self.assertEqual(response.context["folder_year"], 2025)

    def test_sub_folder_counts_ignore_the_other_folders(self):
        subs = self.folder(code="OO").context["series_folders"]
        self.assertEqual(subs, [{"year": 2026, "total": 1}])

    def test_the_export_link_carries_the_year(self):
        response = self.folder(year=2025)
        self.assertIn("series_year=2025", response.context["querystring"])

    def test_the_sidebar_offers_a_link_for_each_year(self):
        response = self.folder()
        html = response.content.decode()
        self.assertIn("/records/folder/MEMO/?year=2026", html)
        self.assertIn("/records/folder/MEMO/?year=2025", html)


class FolderRemovalTests(ArchiveTestCase):
    """A folder can be deleted when empty, and hidden when it still holds records."""

    def setUp(self):
        super().setUp()
        from .models import DocumentFolder

        self.Folder = DocumentFolder
        self.empty = DocumentFolder.objects.create(
            code="RES", label="Resolution", long_name="RESOLUTION",
            icon="icon-file-text", tone="rose",
        )
        self.client.force_login(self.admin)

    def test_an_empty_folder_can_be_deleted(self):
        self.client.post(reverse("documents:folder_delete", args=[self.empty.pk]))
        self.assertFalse(self.Folder.objects.filter(code="RES").exists())

    def test_an_empty_builtin_can_be_deleted_too(self):
        order = self.Folder.objects.get(code="OO")
        self.assertTrue(order.can_be_removed)
        self.client.post(reverse("documents:folder_delete", args=[order.pk]))
        self.assertFalse(self.Folder.objects.filter(code="OO").exists())

    def test_a_folder_with_records_is_never_deleted(self):
        self.make(doc_type="RES", subject="Adopting the budget")
        self.client.post(reverse("documents:folder_delete", args=[self.empty.pk]))
        self.assertTrue(self.Folder.objects.filter(code="RES").exists())

    def test_hiding_takes_it_off_the_overview_but_keeps_the_records(self):
        self.make(doc_type="RES", subject="Adopting the budget")
        self.client.post(reverse("documents:folder_hide", args=[self.empty.pk]))
        self.empty.refresh_from_db()
        self.assertTrue(self.empty.is_hidden)

        cards = self.client.get(reverse("documents:dashboard")).context["type_cards"]
        self.assertNotIn("Resolution", [c["label"] for c in cards])

        folder = self.client.get(reverse("documents:document_folder", args=["RES"]))
        self.assertEqual(folder.status_code, 200)
        self.assertEqual(len(folder.context["documents"]), 1)

    def test_a_hidden_folder_cannot_be_filed_into(self):
        from .forms import DocumentForm

        self.client.post(reverse("documents:folder_hide", args=[self.empty.pk]))
        codes = [code for code, _ in DocumentForm().fields["doc_type"].choices]
        self.assertNotIn("RES", codes)

    def test_hiding_is_reversible(self):
        self.client.post(reverse("documents:folder_hide", args=[self.empty.pk]))
        self.client.post(reverse("documents:folder_hide", args=[self.empty.pk]))
        self.empty.refresh_from_db()
        self.assertFalse(self.empty.is_hidden)

    def test_a_hidden_folder_keeps_its_control_numbers(self):
        doc = self.make(doc_type="RES", subject="Adopting the budget")
        self.client.post(reverse("documents:folder_hide", args=[self.empty.pk]))
        doc.refresh_from_db()
        self.assertEqual(doc.control_number, f"RESOLUTION No. 001, s. {doc.series_year}")
        self.assertEqual(doc.type_label, "Resolution")

    def test_staff_cannot_delete_or_hide(self):
        self.client.force_login(self.staff)
        self.assertEqual(
            self.client.post(reverse("documents:folder_delete", args=[self.empty.pk])).status_code, 404)
        self.assertEqual(
            self.client.post(reverse("documents:folder_hide", args=[self.empty.pk])).status_code, 404)
        self.assertTrue(self.Folder.objects.filter(code="RES").exists())


class ControlNumberTintTests(ArchiveTestCase):
    """A record's control number carries its folder's colour in the results."""

    def setUp(self):
        super().setUp()
        self.make(subject="A memo", classification=Document.Classification.PUBLIC)
        self.make(doc_type=DocumentType.OFFICE_ORDER, subject="An order",
                  classification=Document.Classification.PUBLIC)
        self.client.force_login(self.staff)

    def test_each_listed_record_carries_its_folder_tone(self):
        documents = self.client.get(reverse("documents:document_list")).context["documents"]
        tones = {d.doc_type: d.tone for d in documents}
        self.assertEqual(tones["MEMO"], "green")
        self.assertEqual(tones["OO"], "amber")

    def test_the_folder_page_tags_them_too(self):
        documents = self.client.get(
            reverse("documents:document_folder", args=["MEMO"])).context["documents"]
        self.assertTrue(all(d.tone == "green" for d in documents))

    def test_the_control_number_is_a_chip_in_its_series_colour(self):
        html = self.client.get(reverse("documents:document_list")).content.decode()
        self.assertIn('class="chip chip--series tone-green"', html)
        self.assertIn('class="chip chip--series tone-amber"', html)

    def test_a_record_in_no_known_folder_falls_back_rather_than_breaking(self):
        from .models import DocumentFolder

        orphan = self.make(subject="Orphan", classification=Document.Classification.PUBLIC)
        Document.objects.filter(pk=orphan.pk).update(doc_type="GONE")
        documents = self.client.get(reverse("documents:document_list")).context["documents"]
        tone = {d.pk: d.tone for d in documents}[orphan.pk]
        self.assertEqual(tone, "slate")

    def test_a_new_folder_brings_its_own_colour(self):
        from .models import DocumentFolder

        DocumentFolder.objects.create(code="RES", label="Resolution",
                                      long_name="RESOLUTION", icon="icon-file-text", tone="rose")
        self.make(doc_type="RES", subject="A resolution",
                  classification=Document.Classification.PUBLIC)
        documents = self.client.get(reverse("documents:document_list")).context["documents"]
        tones = {d.doc_type: d.tone for d in documents}
        self.assertEqual(tones["RES"], "rose")
