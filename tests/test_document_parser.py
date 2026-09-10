import io
import unittest
import zipfile
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend.main import app
from backend.document_parser import (
    DocumentParseError,
    extract_document,
)


def make_docx(*paragraphs):
    body = "".join(
        f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>" for paragraph in paragraphs
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def make_pdf(text):
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
    })
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 40 220 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class DocumentParserTests(unittest.TestCase):
    def test_extracts_utf8_text_without_saving_source_file(self):
        parsed = extract_document("job.md", "高级产品经理\n负责 AI 产品".encode())

        self.assertEqual(parsed["file_type"], "md")
        self.assertIn("负责 AI 产品", parsed["text"])
        self.assertEqual(parsed["char_count"], len(parsed["text"]))

    def test_extracts_docx_paragraphs_in_order(self):
        parsed = extract_document("resume.docx", make_docx("小林", "AI 产品经理"))

        self.assertEqual(parsed["file_type"], "docx")
        self.assertEqual(parsed["text"], "小林\nAI 产品经理")

    def test_extracts_text_from_pdf(self):
        parsed = extract_document("resume.pdf", make_pdf("Product Manager 2026"))

        self.assertEqual(parsed["file_type"], "pdf")
        self.assertIn("Product Manager 2026", parsed["text"])

    def test_routes_image_to_local_ocr(self):
        with patch("backend.document_parser._extract_image", return_value="图片简历") as ocr:
            parsed = extract_document("resume.png", b"fake-image", content_type="image/png")

        ocr.assert_called_once()
        self.assertEqual(parsed["text"], "图片简历")
        self.assertEqual(parsed["file_type"], "png")

    def test_rejects_unsupported_and_oversized_files(self):
        with self.assertRaises(DocumentParseError) as unsupported:
            extract_document("payload.html", b"<script>alert(1)</script>")
        self.assertEqual(unsupported.exception.code, "UNSUPPORTED_FILE_TYPE")

        with self.assertRaises(DocumentParseError) as oversized:
            extract_document("resume.txt", b"a" * (12 * 1024 * 1024 + 1))
        self.assertEqual(oversized.exception.code, "FILE_TOO_LARGE")


class DocumentUploadEndpointTests(unittest.TestCase):
    def test_upload_route_accepts_word_and_pdf_files(self):
        with TestClient(app) as client:
            word = client.post(
                "/api/documents/extract",
                files={"file": ("resume.docx", make_docx("张三", "AI 产品经理"))},
            )
            pdf = client.post(
                "/api/documents/extract",
                files={"file": ("job.pdf", make_pdf("Senior Product Manager"), "application/pdf")},
            )

        self.assertEqual(word.status_code, 200)
        self.assertIn("AI 产品经理", word.json()["text"])
        self.assertEqual(pdf.status_code, 200)
        self.assertIn("Senior Product Manager", pdf.json()["text"])


if __name__ == "__main__":
    unittest.main()
