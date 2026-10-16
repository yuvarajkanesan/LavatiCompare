"""Languages, code structure (outline / go to symbol) and the .class viewer."""
import os
import shutil
import struct
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
from lavati import config, fileformats, highlight, javaclass, symbols  # noqa: E402


def make_class(extra_method=False):
    """A tiny hand-assembled Hello.class (the same bytes javac would emit for a simple class)."""
    cp = []

    def utf(s):
        b = s.encode()
        cp.append(b"\x01" + struct.pack(">H", len(b)) + b)
        return len(cp)

    def ref(tag, *idx):
        cp.append(bytes([tag]) + b"".join(struct.pack(">H", i) for i in idx))
        return len(cp)
    hello = ref(7, utf("Hello"))
    obj = ref(7, utf("java/lang/Object"))
    n_greet, d_greet, n_code = utf("greet"), utf("(I)V"), utf("Code")
    n_count, d_int = utf("count"), utf("I")
    system = ref(7, utf("java/lang/System"))
    nat_out = ref(12, utf("out"), utf("Ljava/io/PrintStream;"))
    f_out = ref(9, system, nat_out)
    s_hi = ref(8, utf("hi"))
    ps = ref(7, utf("java/io/PrintStream"))
    nat_pl = ref(12, utf("println"), utf("(Ljava/lang/String;)V"))
    m_pl = ref(10, ps, nat_pl)
    n_src, v_src = utf("SourceFile"), utf("Hello.java")
    code = (bytes([0xB2]) + struct.pack(">H", f_out) + bytes([0x12, s_hi, 0xB6]) + struct.pack(">H", m_pl)
            + bytes([0x1B, 0x99]) + struct.pack(">h", 4) + bytes([0xB1, 0xB1]))
    body = struct.pack(">HHI", 2, 2, len(code)) + code + struct.pack(">HH", 0, 0)

    def method(name, desc):
        return struct.pack(">HHHH", 0x0001, name, desc, 1) + struct.pack(">HI", n_code, len(body)) + body
    methods = [method(n_greet, d_greet)]
    if extra_method:
        methods.append(method(utf("extra"), d_greet))
    out = struct.pack(">IHH", 0xCAFEBABE, 0, 52) + struct.pack(">H", len(cp) + 1) + b"".join(cp)
    out += struct.pack(">HHHH", 0x0021, hello, obj, 0)
    out += struct.pack(">H", 1) + struct.pack(">HHHH", 0x0002, n_count, d_int, 0)
    out += struct.pack(">H", len(methods)) + b"".join(methods)
    out += struct.pack(">H", 1) + struct.pack(">HIH", n_src, 2, v_src)
    return out


class LanguageTests(unittest.TestCase):
    def test_many_languages_by_name(self):
        for name, want in (("a.py", "Python"), ("a.rs", "Rust"), ("a.zig", "Zig"), ("a.hs", "Haskell"), ("a.ex", "Elixir"),
                           ("a.erl", "Erlang"), ("a.clj", "Clojure"), ("a.jl", "Julia"), ("a.sol", "Solidity"), ("a.tf", "Terraform / HCL"),
                           ("a.proto", "Protocol Buffers / Thrift"), ("a.vhd", "VHDL"), ("a.f90", "Fortran"), ("a.cob", "COBOL"),
                           ("a.vb", "Visual Basic"), ("a.diff", "Diff / Patch"), ("a.mts", "TypeScript"), ("a.jsp", "HTML"),
                           ("Jenkinsfile", "Groovy"), ("CMakeLists.txt", "CMake"), ("go.mod", "Go"), (".gitignore", "Properties"),
                           ("BUILD", "Python"), ("a.nix", "Nix"), ("a.graphql", "GraphQL"), ("a.nim", "Nim"), ("a.tex", "LaTeX")):
            self.assertEqual(highlight.language_name(name), want, name)
        self.assertGreaterEqual(len(highlight.LANGUAGES), 80)
        self.assertEqual(highlight.LANGUAGES[-1].name, "Plain text")

    def test_content_and_shebang_detection(self):
        self.assertEqual(highlight.language_name("run", None, "#!/usr/bin/env python3.11\nprint(1)"), "Python")
        self.assertEqual(highlight.language_name("deploy", None, "#!/bin/bash -e\necho"), "Shell")
        self.assertEqual(highlight.language_name("x", None, "#!/usr/bin/node\n"), "JavaScript")
        self.assertEqual(highlight.language_name("a.m", None, "@interface Foo\n@end"), "Objective-C")
        self.assertEqual(highlight.language_name("a.m", None, "function y = f(x)"), "MATLAB / Octave")
        self.assertEqual(highlight.language_name("a.h", None, "class A { public: int x; };"), "C++")
        self.assertEqual(highlight.language_name("a.h", None, "int x;"), "C")
        self.assertEqual(highlight.language_name("noext", None, "<?xml version='1.0'?><a/>"), "XML")
        self.assertEqual(highlight.language_name("noext", None, "plain words"), "Plain text")

    def test_every_language_tokenizes_safely(self):
        sample = ['"quoted \' mixed" // c # d ; e % f -- g /* h', "{ ( [ ] ) } <tag attr=\"1\"> 0x1F 3.14e5 `bt` '''tq''' \"\"\"tq\"\"\"",
                  "@ann $var %%X @@ :sym", "", "   indented\ttab", "/* open", "still in block */ end", "(* ml *) {- hs -} #| l |# #[[ c ]]"]
        for lang in highlight.LANGUAGES:
            lines = (sample * 5)
            marks = highlight.highlight(lines, lang)
            self.assertEqual(len(marks), len(lines), lang.name)
            for ln, ms in zip(lines, marks):
                for a, b, _k in ms:
                    self.assertTrue(0 <= a <= b <= len(ln), (lang.name, a, b, ln))


class SymbolTests(unittest.TestCase):
    def test_python_java_cpp_markdown(self):
        py = "class A:\n    def f(self):\n        return 1\n\n    async def g(self):\n        pass\n\ndef top():\n    return 2\n".split("\n")
        got = [(s.kind, s.qname, s.line, s.end) for s in symbols.extract(py, "Python")]
        self.assertEqual(got, [("class", "A", 0, 5), ("method", "A/f", 1, 2), ("method", "A/g", 4, 5), ("function", "top", 7, 8)])
        java = ("public class Foo {\n    public Foo(int n) {\n        x = n;\n    }\n    @Override\n    public String toString() {\n"
                "        if (x > 0) { return \"{\"; }\n        return \"y\";\n    }\n}\n").split("\n")
        names = [s.qname for s in symbols.extract(java, "Java")]
        self.assertEqual(names, ["Foo", "Foo/Foo", "Foo/toString"])         # control statements and braces in strings are not symbols
        syms = symbols.extract(java, "Java")
        self.assertEqual((syms[2].line, syms[2].end), (5, 8))
        cpp = "namespace n {\nclass W {\npublic:\n  int get() const { return v; }\n};\nvoid W::set(int x) {\n  v = x;\n}\n}\nint main(int c)\n{\n  return 0;\n}\n".split("\n")
        self.assertEqual([s.name for s in symbols.extract(cpp, "C++")], ["n", "W", "get", "W::set", "main"])
        md = ["# Title", "text", "## Sub", "more", "# Other"]
        self.assertEqual([(s.name, s.depth) for s in symbols.extract(md, "Markdown")], [("Title", 0), ("Sub", 1), ("Other", 0)])
        self.assertEqual(symbols.extract(["x"], "Plain text"), [])

    def test_compare_marks_added_removed_changed(self):
        a = ["class A:", "    def f(self):", "        return 1", "    def g(self):", "        return 2", "def top():", "    pass"]
        b = ["class A:", "    def f(self):", "        return 99", "    def h(self):", "        return 3", "def top():", "    pass"]
        rows = symbols.compare(symbols.extract(a, "Python"), a, symbols.extract(b, "Python"), b)
        self.assertEqual([(r.status, r.qname) for r in rows],
                         [("changed", "A"), ("changed", "A/f"), ("removed", "A/g"), ("added", "A/h"), ("same", "top")])
        self.assertEqual(rows[2].rline, -1)
        self.assertEqual(rows[3].lline, -1)

    def test_view_outline_and_go_to_symbol(self):
        from lavati.outline import GoToSymbolDialog, fuzzy
        from lavati.text_view import TextCompareView
        d = tempfile.mkdtemp()
        try:
            cfg = config.Config(os.path.join(d, "c.json"))
            a, b = os.path.join(d, "a.py"), os.path.join(d, "b.py")
            with open(a, "w") as f:
                f.write("class Svc:\n    def compute(self, x):\n        return x\n\n    def legacy(self):\n        pass\n")
            with open(b, "w") as f:
                f.write("class Svc:\n    def compute(self, x):\n        return x * 2\n\n    def describe(self):\n        pass\n")
            v = TextCompareView(cfg, a, b)
            v.resize(1300, 600)
            v.show()
            v.b_outline.setChecked(True)
            end = time.time() + 5
            while time.time() < end and not v.outline.rows:
                app.processEvents()
                time.sleep(0.02)
            self.assertEqual([(r.status, r.name) for r in v.outline.rows],
                             [("changed", "Svc"), ("changed", "compute"), ("removed", "legacy"), ("added", "describe")])
            v.outline.only.setChecked(True)
            v.outline.search.setText("desc")
            hidden = [v.outline.tree.topLevelItem(0).child(i).isHidden() for i in range(3)]
            self.assertEqual(hidden, [True, True, False])
            v.goto_symbol(*[(r.lline, r.rline) for r in v.outline.rows if r.name == "describe"][0])
            self.assertEqual(v.panes["R"].cur_li, 4)                        # exists only on the right: jumps there
            dlg = GoToSymbolDialog(v.outline.rows)
            dlg.edit.setText("cmp")                                          # fuzzy: c-m-p inside "compute"
            self.assertEqual(dlg.list.count(), 1)
            self.assertIsNotNone(fuzzy("lgcy", "legacy"))
            self.assertIsNone(fuzzy("xyz", "legacy"))
            v.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ClassFileTests(unittest.TestCase):
    def test_disassembly_reads_signatures_and_bytecode(self):
        text = javaclass.disassemble_bytes(make_class())
        for want in ("class file version 52.0 (Java 8)", "public class Hello", "private int count;", "public void greet(int);",
                     "descriptor: (I)V", "getstatic #", "Field java.lang.System.out:Ljava/io/PrintStream;", 'String "hi"',
                     "invokevirtual #", "Method java.io.PrintStream.println:(Ljava/lang/String;)V", "ifeq 13", "source file: Hello.java"):
            self.assertIn(want, text)
        self.assertEqual(text, javaclass.disassemble_bytes(make_class()))   # deterministic: safe to compare
        with self.assertRaises(javaclass.ClassError):
            javaclass.disassemble_bytes(b"not a class file at all")
        with self.assertRaises(javaclass.ClassError):
            javaclass.disassemble_bytes(make_class()[:60])

    def test_text_compare_of_two_class_files_shows_the_new_method(self):
        from lavati.home import detect
        from lavati.text_view import TextCompareView
        d = tempfile.mkdtemp()
        try:
            cfg = config.Config(os.path.join(d, "c.json"))
            a, b = os.path.join(d, "Hello.class"), os.path.join(d, "Hello2.class")
            with open(a, "wb") as f:
                f.write(make_class())
            with open(b, "wb") as f:
                f.write(make_class(extra_method=True))
            self.assertEqual(detect(a, b)[0], "text")
            pr = fileformats.prepare(a, cfg)
            self.assertTrue(pr.readonly)
            self.assertIn("javap", pr.note)
            v = TextCompareView(cfg, a, b)
            v.show()
            self.assertTrue(v.sides["L"].readonly)
            self.assertGreaterEqual(len(v.hunks), 1)
            self.assertTrue(any("extra" in ln for ln in v.panes["R"].lines))
            self.assertFalse(any("extra" in ln for ln in v.panes["L"].lines))
            v.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
