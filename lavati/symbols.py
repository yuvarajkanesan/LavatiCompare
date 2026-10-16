"""Code structure for the outline: classes, functions, methods, headings. Regex based (no parser needed), no Qt.

extract(lines, language_name, marks=None) -> [Symbol]      marks: highlighter output used to ignore braces in strings/comments
compare(left_syms, left_lines, right_syms, right_lines, same) -> [Row]  one row per symbol with added/removed/changed/same
"""
import difflib
import re
from dataclasses import dataclass


@dataclass
class Symbol:
    kind: str
    name: str
    line: int          # 0-based first line
    end: int           # 0-based last line (inclusive)
    depth: int = 0
    qname: str = ""


@dataclass
class Row:
    qname: str
    name: str
    kind: str
    depth: int
    status: str        # same | changed | added | removed
    lline: int         # line in the left file, or -1
    rline: int


CONTROL = frozenset("if for while switch catch return new else do try using lock foreach synchronized sizeof typeof throw await delete "
                    "case when assert elif fixed checked unchecked defined unless until with yield and or not in is as".split())
MODS = (r"(?:(?:public|private|protected|internal|static|final|abstract|sealed|partial|open|data|inner|expect|actual|export|default|"
        r"declare|unsafe|readonly|ref|override|virtual|async|extern|new|native|suspend|inline|operator|infix|tailrec|const|"
        r"synchronized|transient|volatile|lateinit|fileprivate|mutating|required|convenience|final|lazy|implicit)\s+)")
TYPES_RX = re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*" + MODS + r"*(class|interface|enum|struct|record|object|trait|namespace|protocol|"
                      r"extension|module|union|impl|contract|library|actor)\s+(\w+)")
JAVA_METHOD = re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*" + MODS + r"*(?:[\w.<>\[\],?&*]+\s+)+(\w+)\s*\(")
CTOR = re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*" + MODS + r"+([A-Z]\w*)\s*\(")
KOTLIN_FUN = re.compile(r"\bfun\s+(?:<[^>]*>\s*)?(?:[\w.<>?]+\.)?(\w+)\s*\(")
C_FUNC = re.compile(r"^[ \t]*(?:[A-Za-z_][\w\s:<>*&~,]*?[\s*&])?([A-Za-z_~][\w:~]*)\s*\([^;{}]*\)?\s*(?:const|noexcept|override|final|\s)*(?:\{|$)")

# (kind, regex, name_group) tried in order on every line; a regex with kind None reads the kind from group 1 and the name from group 2
FAMILY = {}


def _fam(names, *rules):
    for n in names.split("|"):
        FAMILY[n] = rules


_fam("Python", ("class", r"^\s*class\s+(\w+)", 1), ("function", r"^\s*(?:async\s+)?def\s+(\w+)", 1))
_fam("Ruby|Crystal", ("class", r"^\s*class\s+([\w:]+)", 1), ("module", r"^\s*module\s+([\w:]+)", 1), ("function", r"^\s*def\s+(?:self\.)?([\w?!=]+)", 1))
_fam("JavaScript|TypeScript",
     (None, r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?(class|interface|enum|namespace|module)\s+(\w+)", 2),
     ("function", r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)", 1),
     ("function", r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*(?::\s*[^=]+)?=>|\w+\s*=>)", 1),
     ("method", r"^\s*(?:(?:public|private|protected|static|async|get|set|readonly|override|abstract)\s+)*\*?\s*(\w+)\s*(?:<[^>]*>)?\([^)]*\)\s*(?::\s*[^{;]+)?\{\s*$", 1))
_fam("Go", ("type", r"^type\s+(\w+)\s+(?:struct|interface)", 1), ("function", r"^func\s+(?:\(\s*\w*\s*\*?\s*(\w+)[^)]*\)\s*)?(\w+)", 2))
_fam("Rust", (None, r"^\s*(?:pub(?:\([^)]*\))?\s+)?(struct|enum|trait|mod|union)\s+(\w+)", 2),
     ("impl", r"^\s*impl(?:<[^>]*>)?\s+(?:[\w:<>]+\s+for\s+)?(\w+)", 1),
     ("function", r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+|unsafe\s+|const\s+|extern\s+\"[^\"]*\"\s+)*fn\s+(\w+)", 1))
_fam("Swift", (None, r"^\s*(?:(?:public|private|internal|open|final|fileprivate)\s+)*(class|struct|enum|protocol|extension|actor)\s+(\w+)", 2),
     ("function", r"\bfunc\s+(\w+)", 1), ("function", r"^\s*(init)\b", 1))
_fam("PHP", (None, r"^\s*(?:abstract\s+|final\s+)*(class|interface|trait|enum)\s+(\w+)", 2), ("function", r"\bfunction\s+&?(\w+)", 1))
_fam("Kotlin", (None, TYPES_RX.pattern, 2), ("function", KOTLIN_FUN.pattern, 1))
_fam("Scala", (None, r"^\s*(?:(?:case|sealed|abstract|final|private|protected|implicit)\s+)*(class|object|trait)\s+(\w+)", 2), ("function", r"\bdef\s+(\w+)", 1))
_fam("Shell", ("function", r"^\s*(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\s*\)", 1), ("function", r"^\s*function\s+([\w-]+)", 1))
_fam("PowerShell", ("function", r"^\s*(?:function|filter)\s+([\w:-]+)", 1))
_fam("SQL", (None, r"(?i)^\s*create\s+(?:or\s+replace\s+)?(?:unique\s+)?(table|view|function|procedure|index|trigger|type|package)\s+(?:if\s+not\s+exists\s+)?([\w.\[\]\"`]+)", 2))
_fam("Lua", ("function", r"\bfunction\s+([\w.:]+)", 1))
_fam("Perl", ("function", r"^\s*sub\s+(\w+)", 1), ("module", r"^\s*package\s+([\w:]+)", 1))
_fam("R", ("function", r"^\s*([\w.]+)\s*(?:<-|=)\s*function", 1))
_fam("Haskell", ("module", r"^module\s+([\w.]+)", 1), ("function", r"^([a-z_]\w*')?\s*([a-z_]\w*)\s*::", 2), ("type", r"^(?:data|newtype|class|type)\s+(\w+)", 1))
_fam("Elixir", ("module", r"^\s*defmodule\s+([\w.]+)", 1), ("function", r"^\s*defp?\s+(\w+[?!]?)", 1), ("function", r"^\s*defmacrop?\s+(\w+)", 1))
_fam("Erlang", ("function", r"^([a-z]\w*)\(", 1), ("module", r"^-module\((\w+)\)", 1))
_fam("Clojure", ("function", r"^\s*\(defn?-?\s+([^\s)]+)", 1), ("module", r"^\s*\(ns\s+([^\s)]+)", 1))
_fam("Julia", ("module", r"^\s*(?:module|struct|abstract type|mutable struct)\s+(\w+)", 1), ("function", r"^\s*function\s+([\w.!]+)", 1))
_fam("OCaml / F#", ("function", r"^\s*(?:let|and|member|override|let rec)\s+(?:rec\s+|inline\s+|private\s+|this\.)*(\w+)", 1),
     ("module", r"^\s*(?:module|namespace|type)\s+(\w+)", 1))
_fam("Zig", ("function", r"\bfn\s+(\w+)", 1))
_fam("Nim", ("function", r"^\s*(?:proc|func|method|iterator|template|macro)\s+(\w+)", 1), ("type", r"^\s*type\s+(\w+)", 1))
_fam("Solidity", (None, r"^\s*(contract|interface|library|function|modifier|event|struct|enum)\s+(\w+)", 2))
_fam("Pascal / Delphi", ("function", r"(?i)^\s*(?:class\s+)?(?:procedure|function|constructor|destructor)\s+([\w.]+)", 1))
_fam("Fortran", (None, r"(?i)^\s*(subroutine|function|program|module)\s+(\w+)", 2))
_fam("Visual Basic", (None, r"(?i)^\s*(?:(?:Public|Private|Protected|Friend|Shared|Overrides|Overridable|Async)\s+)*(Sub|Function|Class|Module|Property)\s+(\w+)", 2))
_fam("MATLAB / Octave", ("function", r"^\s*function\s+(?:[\w\[\],\s]+=\s*)?(\w+)", 1))
_fam("CMake", (None, r"(?i)^\s*(function|macro)\s*\(\s*(\w+)", 2))
_fam("Objective-C", (None, r"^\s*@(interface|implementation|protocol)\s+(\w+)", 2), ("method", r"^[-+]\s*\([^)]*\)\s*(\w+)", 1)
     )
_fam("Terraform / HCL", (None, r"^\s*(resource|data|module|variable|output|provider|locals)\s+\"?([\w-]+)\"?", 2))
_fam("Protocol Buffers / Thrift|GraphQL", (None, r"^\s*(message|enum|service|rpc|type|interface|input|query|mutation|union|struct|exception)\s+(\w+)", 2))
_fam("Makefile", ("target", r"^([A-Za-z0-9_.%/-]+)\s*:(?!=)", 1))
_fam("Dockerfile", ("stage", r"(?i)^FROM\s+\S+(?:\s+AS\s+(\w+))?", 1))
_fam("Markdown", ("heading", r"^(#{1,6})\s+(.+?)\s*#*\s*$", 2))
_fam("LaTeX", ("heading", r"^\s*\\((?:sub)*section|chapter|part)\*?\{([^}]*)\}", 2))
_fam("INI|TOML|Properties", ("section", r"^\s*\[+([^\]]+)\]+", 1))
_fam("YAML", ("key", r"^([A-Za-z_][\w.-]*):", 1))
_fam("Diff / Patch", ("file", r"^diff --git a/(\S+)", 1))
_JAVA_LIKE = "Java|C#|Groovy|Dart|Vala / Haxe / ActionScript|D"
_C_LIKE = ("C|C++|CUDA|GLSL / HLSL / Shader")

BRACE = set()
BRACE |= {"Java", "C#", "Groovy", "Dart", "Kotlin", "Scala", "JavaScript", "TypeScript", "Go", "Rust", "Swift", "PHP", "Perl", "C", "C++",
          "CUDA", "Solidity", "Objective-C", "D", "Zig", "GLSL / HLSL / Shader", "Terraform / HCL", "Protocol Buffers / Thrift", "GraphQL",
          "PowerShell", "Shell", "R", "Vala / Haxe / ActionScript", "Nginx"}
INDENT = {"Python", "Ruby", "Crystal", "Elixir", "Lua", "Julia", "Nim", "Haskell", "Clojure", "Erlang", "OCaml / F#", "Fortran",
          "Visual Basic", "Pascal / Delphi", "MATLAB / Octave", "CMake", "SQL"}
FLAT = {"Markdown", "LaTeX", "INI", "TOML", "Properties", "YAML", "Makefile", "Dockerfile", "Diff / Patch"}


def supported(lang):
    return lang in FAMILY or lang in _JAVA_LIKE.split("|") or lang in _C_LIKE.split("|")


def _compiled(lang):
    rules = []
    for kind, rx, grp in FAMILY.get(lang, ()):
        flags = re.I if rx.startswith("(?i)") else 0
        rules.append((kind, re.compile(rx, flags), grp))
    return rules


def _raw(lines, lang):
    """[(line, kind, name, level)] candidates, in file order."""
    out = []
    rules = _compiled(lang)
    java_like = lang in _JAVA_LIKE.split("|")
    c_like = lang in _C_LIKE.split("|")
    seen_erlang = set()
    n = len(lines)
    for i, text in enumerate(lines):
        if not text or len(text) > 400:
            continue
        s = text.strip()
        if not s:
            continue
        if lang not in FLAT and s.startswith(("//", "/*", "*", "--", "%", ";", "#")):
            continue
        hit = None
        for kind, rx, grp in rules:
            m = rx.search(text)
            if m:
                if kind is None:
                    hit = (m.group(1).lower(), m.group(grp))
                elif lang == "Go" and kind == "function" and m.group(1):
                    hit = ("method", f"{m.group(1)}.{m.group(2)}")
                elif lang == "Markdown":
                    hit = ("heading", m.group(2))
                    out.append((i, "heading", m.group(2), len(m.group(1))))
                    hit = None
                    break
                elif lang == "LaTeX":
                    depth = 1 + m.group(1).count("sub") if "section" in m.group(1) else 0
                    out.append((i, "heading", m.group(2), depth))
                    hit = None
                    break
                elif lang == "Dockerfile":
                    out.append((i, "stage", m.group(1) or text.split()[1], 0))
                    hit = None
                    break
                else:
                    g = m.group(grp)
                    if g:
                        hit = (kind, g)
                if hit:
                    break
        if hit is None and java_like:
            m = TYPES_RX.search(text)
            if m:
                hit = (m.group(1).lower(), m.group(2))
            else:
                m = JAVA_METHOD.search(text) or CTOR.search(text)
                if m and m.group(1) not in CONTROL and not s.endswith(";") and "=" not in text[:m.start(1)]:
                    first = s.split(None, 1)[0].rstrip("(")
                    nxt = ""
                    for k in range(i + 1, min(n, i + 3)):
                        if lines[k].strip():
                            nxt = lines[k].strip()
                            break
                    if first not in CONTROL and (s.endswith(("{", ")", ",")) or nxt.startswith("{") or "throws" in s):
                        hit = ("function", m.group(1))
        if hit is None and c_like:
            m = TYPES_RX.search(text)
            if m and not s.endswith(";"):
                hit = (m.group(1).lower(), m.group(2))
            else:
                m = C_FUNC.match(text)
                if m and m.group(1) not in CONTROL and not s.endswith(";") and not s.startswith(("return", "else", "#")) and "=" not in text[:m.start(1)]:
                    hit = ("function", m.group(1))
        if hit is None:
            continue
        if lang == "Erlang":
            if hit[1] in seen_erlang:
                continue
            seen_erlang.add(hit[1])
        out.append((i, hit[0], hit[1], 0))
    return out


def _code_only(text, marks):
    if not marks:
        return text
    chars = list(text)
    for s, e, kind in marks:
        if kind in ("c", "s"):
            for k in range(s, min(e, len(chars))):
                chars[k] = " "
    return "".join(chars)


def extract(lines, lang, marks=None, limit=4000):
    """Symbols of a document. Unsupported languages give []."""
    if not supported(lang):
        return []
    raw = _raw(lines, lang)[:limit]
    n = len(lines)
    syms = []
    if lang in FLAT:
        levels = [(r[3] if lang in ("Markdown", "LaTeX") else 0) for r in raw]
        for k, (i, kind, name, lvl) in enumerate(raw):
            end = n - 1
            for j in range(k + 1, len(raw)):
                if levels[j] <= levels[k]:
                    end = raw[j][0] - 1
                    break
            syms.append(Symbol(kind, name, i, max(i, end), lvl - 1 if lang == "Markdown" else lvl))
        return _qualify(syms, flat=True)
    if lang in BRACE:
        if marks is None or len(marks) != n:                 # braces inside strings and comments must not count
            from . import highlight
            marks = highlight.highlight(lines, highlight.language_for("", lang))
        depth_after, cum = [0] * n, 0
        opens_here = [False] * n
        for i, text in enumerate(lines):
            code = _code_only(text, marks[i] if marks and i < len(marks) else None) if ("{" in text or "}" in text) else ""
            o, c = code.count("{"), code.count("}")
            opens_here[i] = o > 0
            cum += o - c
            depth_after[i] = cum
        for i, kind, name, _l in raw:
            before = depth_after[i - 1] if i else 0
            end = i
            # find the opening brace (this line or one of the next few), then its matching close
            j = i
            while j < min(n, i + 6) and not opens_here[j]:
                if ";" in lines[j] and j > i:
                    break
                j += 1
            if j < n and opens_here[j]:
                base = depth_after[j - 1] if j else 0
                k = j
                while k < n and depth_after[k] > base:
                    k += 1
                end = min(k, n - 1)
            syms.append(Symbol(kind, name, i, end))
        return _qualify(syms)
    # indentation based
    def indent(s):
        return len(s) - len(s.lstrip())
    for i, kind, name, _l in raw:
        ind = indent(lines[i])
        end = i
        for j in range(i + 1, n):
            t = lines[j]
            if not t.strip():
                continue
            st = t.strip()
            if st.startswith(("#", "--", "%", ";")) and indent(t) > ind:
                end = j
                continue
            if indent(t) > ind:
                end = j
                continue
            if indent(t) == ind and re.match(r"(?i)(end\b|\}|esac\b|fi\b|done\b|endfunction|endif|end\.)", st):
                end = j
            break
        syms.append(Symbol(kind, name, i, end))
    return _qualify(syms)


def _qualify(syms, flat=False):
    """Fill depth and qualified names from containment (a symbol inside another one's line range is its child)."""
    stack = []
    for s in syms:
        while stack and not (stack[-1].line <= s.line <= stack[-1].end and stack[-1] is not s):
            stack.pop()
        if flat:
            while stack and s.depth <= stack[-1].depth:
                stack.pop()
        parent = stack[-1] if stack else None
        if not flat:
            s.depth = len(stack)
        s.qname = (parent.qname + "/" if parent else "") + s.name
        if parent and s.kind == "function" and parent.kind in ("class", "struct", "interface", "enum", "object", "trait", "impl", "record",
                                                                "protocol", "extension", "contract", "library", "actor", "union"):
            s.kind = "method"
        stack.append(s)
    return syms


def _nth(syms):
    """Key each symbol by qualified name plus occurrence number (overloads share a name)."""
    seen, keyed = {}, []
    for s in syms:
        c = seen.get(s.qname, 0)
        seen[s.qname] = c + 1
        keyed.append((f"{s.qname}#{c}", s))
    return keyed


def compare(lsyms, llines, rsyms, rlines, same=None):
    """Align the symbols of the two files and say which were added, removed or changed.

    `same(left_slice, right_slice)` decides if two bodies are equal (default: exact lines)."""
    same = same or (lambda a, b: a == b)
    lk, rk = _nth(lsyms), _nth(rsyms)
    sm = difflib.SequenceMatcher(None, [k for k, _ in lk], [k for k, _ in rk], autojunk=False)
    rows = []

    def left_only(s):
        rows.append(Row(s.qname, s.name, s.kind, s.depth, "removed", s.line, -1))

    def right_only(s):
        rows.append(Row(s.qname, s.name, s.kind, s.depth, "added", -1, s.line))
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for (_, a), (_, b) in zip(lk[i1:i2], rk[j1:j2]):
                st = "same" if same(llines[a.line:a.end + 1], rlines[b.line:b.end + 1]) else "changed"
                rows.append(Row(b.qname, b.name, b.kind, b.depth, st, a.line, b.line))
        else:
            for _, a in lk[i1:i2]:
                left_only(a)
            for _, b in rk[j1:j2]:
                right_only(b)
    return rows
