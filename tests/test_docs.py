import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lavati import config, docs, fileformats  # noqa: E402

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
T = 'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'

MINI_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj<</Length 44>>stream
BT /F1 18 Tf 20 100 Td (Hello PDF world) Tj ET
endstream
endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
trailer<</Root 1 0 R/Size 6>>
%%EOF
"""


class DocTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def p(self, name):
        return os.path.join(self.d, name)

    def test_docx(self):
        with zipfile.ZipFile(self.p("a.docx"), "w") as z:
            z.writestr("word/document.xml", f'<w:document {W}><w:body><w:p><w:r><w:t>First</w:t></w:r><w:r><w:tab/><w:t>tabbed</w:t></w:r></w:p>'
                                            f'<w:tbl><w:tr><w:tc><w:p><w:r><w:t>cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
        self.assertEqual(docs.extract(self.p("a.docx")), "First\ttabbed\ncell")

    def test_odt(self):
        with zipfile.ZipFile(self.p("a.odt"), "w") as z:
            z.writestr("content.xml", f'<office:document-content {T}><office:body><office:text>'
                                      f'<text:h>Title</text:h><text:p>a<text:s text:c="2"/>b<text:span>c</text:span></text:p>'
                                      f'</office:text></office:body></office:document-content>')
        self.assertEqual(docs.extract(self.p("a.odt")), "Title\na  bc")

    def test_pptx(self):
        with zipfile.ZipFile(self.p("a.pptx"), "w") as z:
            z.writestr("ppt/slides/slide1.xml", f'<p:sld {A} xmlns:p="x"><a:p><a:r><a:t>One</a:t></a:r></a:p></p:sld>')
            z.writestr("ppt/slides/slide2.xml", f'<p:sld {A} xmlns:p="x"><a:p><a:r><a:t>Two</a:t></a:r></a:p></p:sld>')
        self.assertEqual(docs.extract(self.p("a.pptx")), "--- Slide 1 ---\nOne\n--- Slide 2 ---\nTwo")

    def test_rtf(self):
        rtf = r"{\rtf1\ansi{\fonttbl{\f0 Arial;}}\f0 Hello \b bold\b0 \par Second \'e9 line \u8364?\par}"
        with open(self.p("a.rtf"), "w") as f:
            f.write(rtf)
        out = docs.extract(self.p("a.rtf"))
        self.assertIn("Hello bold", out)
        self.assertIn("Second é line €", out)
        self.assertNotIn("Arial", out)

    def test_pdf(self):
        with open(self.p("a.pdf"), "wb") as f:
            f.write(MINI_PDF)
        try:
            out = docs.extract(self.p("a.pdf"))
        except docs.ExtractError as e:
            self.skipTest(str(e))
        self.assertIn("Hello PDF world", out)

    def test_errors(self):
        with open(self.p("bad.docx"), "w") as f:
            f.write("not a zip")
        with self.assertRaises(docs.ExtractError):
            docs.extract(self.p("bad.docx"))

    def test_prepare_and_formats(self):
        cfg = config.Config(self.p("cfg.json"))
        with zipfile.ZipFile(self.p("a.docx"), "w") as z:
            z.writestr("word/document.xml", f'<w:document {W}><w:body><w:p><w:r><w:t>Hi</w:t></w:r></w:p></w:body></w:document>')
        pr = fileformats.prepare(self.p("a.docx"), cfg)
        self.assertTrue(pr.readonly)
        self.assertEqual(pr.lines, ["Hi"])
        with open(self.p("x.txt"), "w") as f:
            f.write("plain\n")
        pr = fileformats.prepare(self.p("x.txt"), cfg)
        self.assertFalse(pr.readonly)
        self.assertEqual(pr.lines, ["plain"])
        # user format with an external converter (python -c as a portable "converter")
        cfg["file_formats"] = [dict(name="Shout", patterns="*.shout", converter="external",
                                    command=f'"{sys.executable}" -c "import sys;print(open(sys.argv[1]).read().upper())" {{in}}')]
        with open(self.p("a.shout"), "w") as f:
            f.write("quiet words")
        pr = fileformats.prepare(self.p("a.shout"), cfg)
        self.assertEqual(pr.lines, ["QUIET WORDS"])
        self.assertTrue(pr.readonly)
        # rule overlay
        from lavati import engine
        fmt = dict(fileformats.blank_format(), ws="all", unimportant=["x+"], ignore_case=True)
        r = fileformats.rules_for(engine.TextRules(), fmt)
        self.assertEqual((r.ws, r.ignore_case, r.unimportant), ("all", True, ["x+"]))


if __name__ == "__main__":
    unittest.main()
