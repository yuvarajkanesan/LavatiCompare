"""Plain-text extraction from Word, OpenDocument, PowerPoint, RTF and PDF files. No Qt imports."""
import os
import re
import zipfile
import xml.etree.ElementTree as ET

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
T_NS = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"

MAX_XML = 200 * 1024 * 1024


class ExtractError(Exception):
    pass


def _read_member(z, name):
    info = z.getinfo(name)
    if info.file_size > MAX_XML:
        raise ExtractError(f"{name} is too large to read")
    with z.open(name) as f:
        data = f.read(MAX_XML + 1)             # the header size can lie; bound what is actually inflated
    if len(data) > MAX_XML:
        raise ExtractError(f"{name} is too large to read")
    return data


def _parse(data):
    # defusedxml is not required: ElementTree does not resolve external entities
    return ET.fromstring(data)


def docx_text(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if "word/document.xml" not in names:
            raise ExtractError("Not a Word (.docx) document")
        parts = ["word/document.xml"]
        parts += sorted(n for n in names if re.match(r"word/(header|footer|footnotes|endnotes)\d*\.xml$", n))
        out = []
        for part in parts:
            root = _parse(_read_member(z, part))
            if part != "word/document.xml":
                out.append(f"--- {os.path.basename(part)} ---")
            for p in root.iter(W_NS + "p"):
                buf = []
                for el in p.iter():
                    t = el.tag
                    if t == W_NS + "t":
                        buf.append(el.text or "")
                    elif t == W_NS + "tab":
                        buf.append("\t")
                    elif t in (W_NS + "br", W_NS + "cr"):
                        buf.append("\n")
                out.append("".join(buf))
    return "\n".join(out)


def odf_text(path):
    with zipfile.ZipFile(path) as z:
        if "content.xml" not in z.namelist():
            raise ExtractError("Not an OpenDocument file")
        root = _parse(_read_member(z, "content.xml"))
    out = []

    def walk_text(el):
        buf = [el.text or ""]
        for ch in el:
            t = ch.tag
            if t == T_NS + "s":
                buf.append(" " * int(ch.get(T_NS + "c", "1")))
            elif t == T_NS + "tab":
                buf.append("\t")
            elif t == T_NS + "line-break":
                buf.append("\n")
            else:
                buf.append(walk_text(ch))
            buf.append(ch.tail or "")
        return "".join(buf)
    for el in root.iter():
        if el.tag in (T_NS + "p", T_NS + "h"):
            out.append(walk_text(el))
    return "\n".join(out)


def pptx_text(path):
    with zipfile.ZipFile(path) as z:
        slides = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                        key=lambda n: int(re.findall(r"\d+", n)[-1]))
        if not slides:
            raise ExtractError("No slides found")
        out = []
        for i, name in enumerate(slides, 1):
            out.append(f"--- Slide {i} ---")
            root = _parse(_read_member(z, name))
            for p in root.iter(A_NS + "p"):
                out.append("".join(t.text or "" for t in p.iter(A_NS + "t")))
            num = re.findall(r"\d+", name)[-1]
            notes = "ppt/notesSlides/notesSlide" + num + ".xml"
            if notes in z.namelist():
                root = _parse(_read_member(z, notes))
                txt = [("".join(t.text or "" for t in p.iter(A_NS + "t"))) for p in root.iter(A_NS + "p")]
                txt = [t for t in txt if t.strip()]
                if txt:
                    out.append("[notes]")
                    out += txt
    return "\n".join(out)


_RTF_SKIP = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "header", "footer", "themedata",
             "colorschememapping", "latentstyles", "datastore", "generator", "listtable", "listoverridetable",
             "rsidtbl", "fldinst", "bkmkstart", "bkmkend"}


def rtf_text(path):
    with open(path, "rb") as f:
        data = f.read().decode("latin-1")
    if not data.lstrip().startswith("{\\rtf"):
        raise ExtractError("Not an RTF file")
    out = []
    stack = []
    skip = 0              # depth at which we started skipping (0 = not skipping)
    uc = 1
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        if c == "{":
            stack.append((skip, uc))
            i += 1
        elif c == "}":
            if stack:
                skip, uc = stack.pop()
                if skip and len(stack) < skip:
                    skip = 0
            i += 1
        elif c == "\\":
            i += 1
            if i >= n:
                break
            c = data[i]
            if c in "\\{}":
                if not skip:
                    out.append(c)
                i += 1
            elif c == "'":
                hexv = data[i + 1:i + 3]
                if not skip:
                    try:
                        out.append(bytes([int(hexv, 16)]).decode("cp1252", errors="replace"))
                    except ValueError:
                        pass
                i += 3
            elif c == "*":
                skip = skip or len(stack)
                i += 1
            elif c.isalpha():
                m = re.match(r"([a-zA-Z]+)(-?\d+)? ?", data[i:])
                word, arg = m.group(1), m.group(2)
                i += m.end()
                if word in _RTF_SKIP:
                    skip = skip or len(stack)
                elif skip:
                    pass
                elif word in ("par", "line", "sect", "page"):
                    out.append("\n")
                elif word == "tab":
                    out.append("\t")
                elif word == "u" and arg is not None:
                    v = int(arg)
                    out.append(chr(v + 65536 if v < 0 else v))
                    for _ in range(uc):          # skip the ANSI fallback characters
                        if data[i:i + 2] == "\\'":
                            i += 4
                        elif i < n and data[i] not in "{}\\":
                            i += 1
                elif word == "uc" and arg is not None:
                    uc = int(arg)
            else:
                i += 1
        elif c in "\r\n":
            i += 1
        else:
            if not skip:
                out.append(c)
            i += 1
    return re.sub(r"\n{3,}", "\n\n", "".join(out))


def pdf_text(path):
    try:
        import pypdfium2 as pdfium
    except ImportError:
        pdfium = None
    if pdfium is not None:
        doc = pdfium.PdfDocument(path)
        try:
            out = []
            for i in range(len(doc)):
                page = doc[i]
                tp = page.get_textpage()
                out.append(f"--- Page {i + 1} ---")
                out.append(tp.get_text_range().replace("\r\n", "\n").replace("\r", "\n"))
                tp.close()
                page.close()
            return "\n".join(out)
        finally:
            doc.close()
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise ExtractError("PDF support needs the 'pypdfium2' (or 'pypdf') package: pip install pypdfium2") from e
    r = PdfReader(path)
    return "\n".join(f"--- Page {i + 1} ---\n{p.extract_text() or ''}" for i, p in enumerate(r.pages))


BUILTIN = {
    ".docx": docx_text, ".docm": docx_text, ".dotx": docx_text,
    ".odt": odf_text, ".ods": odf_text, ".odp": odf_text, ".ott": odf_text,
    ".pptx": pptx_text, ".pptm": pptx_text,
    ".rtf": rtf_text,
    ".pdf": pdf_text,
}


def can_extract(path):
    return os.path.splitext(path)[1].lower() in BUILTIN


def extract(path):
    ext = os.path.splitext(path)[1].lower()
    fn = BUILTIN.get(ext)
    if fn is None:
        raise ExtractError(f"No text extractor for {ext or 'this file type'}")
    try:
        return fn(path)
    except zipfile.BadZipFile as e:
        raise ExtractError(f"{os.path.basename(path)} is not a valid Office/OpenDocument file") from e
    except ET.ParseError as e:
        raise ExtractError(f"Cannot parse {os.path.basename(path)}: {e}") from e
