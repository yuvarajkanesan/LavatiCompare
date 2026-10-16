"""Syntax highlighting: small per-language grammars with multi-line state. No Qt imports.

highlight(lines, lang) -> [[(start, end, kind), ...] per line]
kinds: c comment, s string, n number, k keyword, t type, p property/attribute/variable, d decorator/preprocessor
"""
import os
import re
from dataclasses import dataclass, field


def _w(s):
    return frozenset(s.split())


@dataclass
class Lang:
    name: str
    exts: tuple = ()
    names: tuple = ()                 # whole file names (Makefile, Dockerfile ...)
    line: tuple = ()                  # line-comment starters
    block: tuple = ()                 # ((start, end), ...)
    strings: tuple = ('"', "'")
    triple: tuple = ()                # triple-quote delimiters
    multiline_strings: tuple = ()     # quote chars whose strings may span lines (backtick)
    kw: frozenset = frozenset()
    types: frozenset = frozenset()
    ci: bool = False                  # case-insensitive keywords
    extra: tuple = ()                 # ((regex, kind), ...) tried before identifiers
    numbers: bool = True
    special: str = ""                 # xml | json | markdown | ini | css
    _rx: object = field(default=None, repr=False, compare=False)


C_KW = _w("""if else for while do switch case default break continue return goto sizeof typedef struct union enum
static extern const volatile register inline auto void""")
C_TYPES = _w("int long short char float double unsigned signed bool size_t uint8_t uint16_t uint32_t uint64_t int8_t int16_t int32_t int64_t FILE NULL nullptr true false")
CPP_KW = C_KW | _w("""class public private protected virtual override final namespace using template typename new delete this try catch throw
operator friend explicit constexpr noexcept decltype static_cast dynamic_cast reinterpret_cast const_cast mutable""")
JAVA_KW = _w("""abstract assert break case catch class continue default do else enum extends final finally for if implements import
instanceof interface native new package private protected public return static strictfp super switch synchronized this throw
throws transient try volatile while var record sealed permits yield""")
JAVA_TYPES = _w("boolean byte char short int long float double void String Object Integer Long List Map Set true false null")
KOTLIN_KW = _w("""as break class continue do else for fun if in interface is object package return super this throw try typealias val var
when while by catch constructor delegate dynamic field file finally get import init param property receiver set setparam where
abstract actual annotation companion const crossinline data enum expect external final infix inline inner internal lateinit
noinline open operator out override private protected public reified sealed suspend tailrec vararg""")
KOTLIN_TYPES = _w("Int Long Short Byte Float Double Boolean Char String Unit Any Nothing List Map Set Array true false null")
PY_KW = _w("""False None True and as assert async await break class continue def del elif else except finally for from global if import in
is lambda nonlocal not or pass raise return try while with yield match case self cls""")
PY_TYPES = _w("int str float bool list dict set tuple bytes object type Exception print len range")
JS_KW = _w("""async await break case catch class const continue debugger default delete do else export extends finally for from function if
import in instanceof let new of return static super switch this throw try typeof var void while with yield get set
interface type enum implements namespace declare abstract readonly private protected public as is keyof""")
JS_TYPES = _w("true false null undefined NaN Infinity string number boolean any unknown never void object Promise Array Map Set Object console window document")
GO_KW = _w("break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var")
GO_TYPES = _w("bool byte complex128 error float32 float64 int int8 int16 int32 int64 rune string uint uint8 uint16 uint32 uint64 uintptr true false nil iota")
RUST_KW = _w("as async await break const continue crate dyn else enum extern fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait type unsafe use where while")
RUST_TYPES = _w("bool char f32 f64 i8 i16 i32 i64 i128 isize str u8 u16 u32 u64 u128 usize String Vec Option Result Box true false None Some Ok Err")
CS_KW = _w("""abstract as base break case catch checked class const continue default delegate do else enum event explicit extern finally fixed
for foreach goto if implicit in interface internal is lock namespace new operator out override params private protected public readonly
ref return sealed sizeof stackalloc static struct switch this throw try typeof unchecked unsafe using virtual volatile while var async await yield get set partial record init""")
CS_TYPES = _w("bool byte char decimal double float int long object sbyte short string uint ulong ushort void dynamic true false null String List Dictionary Task")
SWIFT_KW = _w("""associatedtype class deinit enum extension fileprivate func import init inout internal let open operator private protocol public
static struct subscript typealias var break case continue default defer do else fallthrough for guard if in repeat return switch where while
as catch false is nil rethrows super self throw throws true try async await""")
SH_KW = _w("if then else elif fi for while until do done case esac function in select time return exit break continue export local readonly declare set unset source alias")
PS_KW = _w("begin break catch class continue data do dynamicparam else elseif end exit filter finally for foreach from function if in param process return switch throw trap try until using var while workflow")
BAT_KW = _w("if else for in do goto call exit set setlocal endlocal echo pause shift not exist defined errorlevel equ neq lss leq gtr geq start cd md mkdir rd rmdir copy move del type")
SQL_KW = _w("""select from where insert into values update set delete create alter drop table index view database join inner outer left right full on
group by order having limit offset union all distinct as and or not null is in like between exists case when then else end primary key foreign
references default constraint unique check begin commit rollback transaction grant revoke with""")
RUBY_KW = _w("alias and begin break case class def defined do else elsif end ensure false for if in module next nil not or redo rescue retry return self super then true undef unless until when while yield require include extend attr_accessor attr_reader attr_writer puts")
PHP_KW = _w("abstract and array as break callable case catch class clone const continue declare default do echo else elseif empty enddeclare endfor endforeach endif endswitch endwhile extends final finally fn for foreach function global goto if implements include include_once instanceof insteadof interface isset list match namespace new or print private protected public require require_once return static switch throw trait try unset use var while xor yield")
LUA_KW = _w("and break do else elseif end false for function goto if in local nil not or repeat return then true until while")
GROOVY_KW = JAVA_KW | _w("def in as trait println plugins id apply implementation api compileOnly testImplementation dependencies repositories")
SCALA_KW = _w("abstract case catch class def do else extends false final finally for forSome if implicit import lazy match new null object override package private protected return sealed super this throw trait try true type val var while with yield")
DART_KW = _w("abstract as assert async await break case catch class const continue covariant default deferred do dynamic else enum export extends extension external factory false final finally for Function get hide if implements import in interface is late library mixin new null on operator part required rethrow return set show static super switch sync this throw true try typedef var void while with yield")
PERL_KW = _w("my our local sub if elsif else unless while until for foreach return last next redo use no package require print die eval do and or not")
R_KW = _w("if else repeat while function for next break TRUE FALSE NULL NA Inf NaN in library require return")
YAML_KW = _w("true false null yes no on off True False Null")

LANGUAGES = [
    Lang("Python", (".py", ".pyw", ".pyi", ".pyx"), line=("#",), triple=('"""', "'''"), kw=PY_KW, types=PY_TYPES,
         extra=((r"@[A-Za-z_][\w.]*", "d"),)),
    Lang("Kotlin", (".kt", ".kts"), line=("//",), block=(("/*", "*/"),), triple=('"""',), kw=KOTLIN_KW, types=KOTLIN_TYPES,
         extra=((r"@[A-Za-z_]\w*", "d"),)),
    Lang("Java", (".java",), line=("//",), block=(("/*", "*/"),), kw=JAVA_KW, types=JAVA_TYPES, extra=((r"@[A-Za-z_]\w*", "d"),)),
    Lang("Groovy", (".gradle", ".groovy"), line=("//",), block=(("/*", "*/"),), triple=('"""', "'''"), kw=GROOVY_KW, types=JAVA_TYPES),
    Lang("Scala", (".scala", ".sc"), line=("//",), block=(("/*", "*/"),), triple=('"""',), kw=SCALA_KW, types=JAVA_TYPES),
    Lang("JavaScript", (".js", ".mjs", ".cjs", ".jsx"), line=("//",), block=(("/*", "*/"),), strings=('"', "'", "`"),
         multiline_strings=("`",), kw=JS_KW, types=JS_TYPES),
    Lang("TypeScript", (".ts", ".tsx"), line=("//",), block=(("/*", "*/"),), strings=('"', "'", "`"),
         multiline_strings=("`",), kw=JS_KW, types=JS_TYPES, extra=((r"@[A-Za-z_]\w*", "d"),)),
    Lang("C", (".c", ".h"), line=("//",), block=(("/*", "*/"),), kw=C_KW, types=C_TYPES, extra=((r"^\s*#\s*\w+", "d"),)),
    Lang("C++", (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".ino"), line=("//",), block=(("/*", "*/"),), kw=CPP_KW,
         types=C_TYPES, extra=((r"^\s*#\s*\w+", "d"),)),
    Lang("C#", (".cs",), line=("//",), block=(("/*", "*/"),), kw=CS_KW, types=CS_TYPES, extra=((r"^\s*#\s*\w+", "d"),)),
    Lang("Go", (".go",), line=("//",), block=(("/*", "*/"),), strings=('"', "'", "`"), multiline_strings=("`",), kw=GO_KW, types=GO_TYPES),
    Lang("Rust", (".rs",), line=("//",), block=(("/*", "*/"),), kw=RUST_KW, types=RUST_TYPES, extra=((r"#!?\[[^\]]*\]", "d"),)),
    Lang("Swift", (".swift",), line=("//",), block=(("/*", "*/"),), triple=('"""',), kw=SWIFT_KW, types=C_TYPES | _w("String Int Double Bool Array Dictionary")),
    Lang("Dart", (".dart",), line=("//",), block=(("/*", "*/"),), kw=DART_KW, types=_w("int double String bool List Map Set num var dynamic")),
    Lang("PHP", (".php",), line=("//", "#"), block=(("/*", "*/"),), kw=PHP_KW, types=_w("true false null"), ci=True,
         extra=((r"\$[A-Za-z_]\w*", "p"),)),
    Lang("Ruby", (".rb", ".rake", ".gemspec"), names=("Rakefile", "Gemfile"), line=("#",), kw=RUBY_KW, types=_w("true false nil"),
         extra=((r":[A-Za-z_]\w*", "p"), (r"@{1,2}[A-Za-z_]\w*", "p"))),
    Lang("Lua", (".lua",), line=("--",), block=(("--[[", "]]"),), kw=LUA_KW, types=_w("true false nil")),
    Lang("Perl", (".pl", ".pm"), line=("#",), kw=PERL_KW, extra=((r"[$@%][A-Za-z_]\w*", "p"),)),
    Lang("R", (".r",), line=("#",), kw=R_KW),
    Lang("Shell", (".sh", ".bash", ".zsh", ".ksh"), names=(".bashrc", ".profile", ".zshrc"), line=("#",), strings=('"', "'"),
         kw=SH_KW, extra=((r"\$\{[^}]*\}|\$[A-Za-z_]\w*|\$[0-9@#?*!$-]", "p"),)),
    Lang("PowerShell", (".ps1", ".psm1", ".psd1"), line=("#",), block=(("<#", "#>"),), kw=PS_KW, ci=True,
         extra=((r"\$[A-Za-z_][\w:]*|\$\{[^}]*\}", "p"), (r"-[A-Za-z]+\b", "d"))),
    Lang("Batch", (".bat", ".cmd"), line=("::",), kw=BAT_KW, ci=True, strings=('"',),
         extra=((r"(?i:^\s*rem\b.*$)", "c"), (r"%[A-Za-z_0-9~]+%|%%?[0-9A-Za-z]", "p"), (r"^\s*:[A-Za-z_]\w*", "d"))),
    Lang("SQL", (".sql",), line=("--",), block=(("/*", "*/"),), kw=SQL_KW, ci=True, strings=("'", '"')),
    Lang("CSS", (".css", ".scss", ".less"), block=(("/*", "*/"),), special="css", line=("//",)),
    Lang("HTML", (".html", ".htm", ".xhtml", ".vue", ".svelte"), special="xml"),
    Lang("XML", (".xml", ".xsd", ".xsl", ".xslt", ".svg", ".plist", ".csproj", ".vbproj", ".props", ".targets", ".config",
                 ".resx", ".axml", ".wsdl", ".xaml", ".fxml", ".pom", ".manifest", ".rss", ".atom"), special="xml"),
    Lang("JSON", (".json", ".jsonc", ".json5", ".geojson", ".webmanifest"), special="json", line=("//",), block=(("/*", "*/"),)),
    Lang("YAML", (".yml", ".yaml"), line=("#",), special="ini", kw=YAML_KW),
    Lang("TOML", (".toml",), line=("#",), special="ini", kw=YAML_KW),
    Lang("INI", (".ini", ".cfg", ".conf", ".inf", ".desktop", ".editorconfig", ".gitconfig", ".reg"), line=(";", "#"), special="ini", kw=YAML_KW),
    Lang("Properties", (".properties", ".env"), line=("#", "!"), special="ini", kw=YAML_KW),
    Lang("Markdown", (".md", ".markdown", ".mdx"), special="markdown"),
    Lang("Makefile", (".mk",), names=("Makefile", "makefile", "GNUmakefile"), line=("#",), kw=_w("ifeq ifneq ifdef ifndef else endif include define endef export"),
         extra=((r"\$\([^)]*\)|\$\{[^}]*\}", "p"), (r"^[A-Za-z0-9_.%/-]+\s*:(?!=)", "d"))),
    Lang("Dockerfile", (".dockerfile",), names=("Dockerfile",), line=("#",),
         kw=_w("FROM RUN CMD LABEL EXPOSE ENV ADD COPY ENTRYPOINT VOLUME USER WORKDIR ARG ONBUILD STOPSIGNAL HEALTHCHECK SHELL AS")),
    Lang("Plain text", (".txt", ".log", ".text"), numbers=False, strings=()),
]

from .langs_more import ALIASES as _ALIASES, ALIAS_NAMES as _ALIAS_NAMES, SHEBANGS as _SHEBANGS, build as _build_more  # noqa: E402

LANGUAGES[-1:-1] = _build_more(Lang, _w)          # every extra language goes before 'Plain text'

_BY_EXT = {}
_BY_NAME = {}
for _l in LANGUAGES:
    for _e in _l.exts:
        _BY_EXT.setdefault(_e, _l)
    for _n in _l.names:
        _BY_NAME[_n.lower()] = _l
_BY_LANG = {_l.name: _l for _l in LANGUAGES}
for _name, _exts in _ALIASES.items():
    for _e in _exts:
        _BY_EXT.setdefault(_e, _BY_LANG[_name])
for _name, _names in _ALIAS_NAMES.items():
    for _n in _names:
        _BY_NAME.setdefault(_n.lower(), _BY_LANG[_name])
PLAIN = LANGUAGES[-1]
LANG_NAMES = [l.name for l in LANGUAGES]
_SHEBANG = re.compile(r"^#!\s*(?:\S*/)?(?:env\s+(?:-\S+\s+)*)?([\w.+-]+)")


def language_for(path, override=None, sample=""):
    """Pick the language from the file name, then (for unknown names) from a #! line or from the content.

    `sample` is the start of the file's text; it is only looked at when the name alone does not decide."""
    if override:
        for l in LANGUAGES:
            if l.name == override:
                return l
    name = os.path.basename(path or "").lower()
    found = _BY_NAME.get(name)
    if found is None:
        ext = os.path.splitext(name)[1]
        if name.startswith(".") and not ext:
            ext = name
        found = _BY_EXT.get(ext)
        if found is not None and sample:
            found = _sniff(ext, found, sample)
    if found is not None:
        return found
    if sample.startswith("#!"):
        m = _SHEBANG.match(sample.split("\n", 1)[0])
        if m:
            interp = re.sub(r"[\d.]+$", "", m.group(1)) if m.group(1) not in _SHEBANGS else m.group(1)
            lang = _SHEBANGS.get(m.group(1)) or _SHEBANGS.get(interp)
            if lang in _BY_LANG:
                return _BY_LANG[lang]
    if sample.lstrip().startswith("<?xml"):
        return _BY_LANG["XML"]
    if sample.lstrip().startswith("<?php"):
        return _BY_LANG["PHP"]
    return PLAIN


def _sniff(ext, found, sample):
    """A few extensions are shared by several languages: look at the content."""
    if ext == ".m":
        if re.search(r"^\s*(?:@interface|@implementation|#import|@end)\b", sample, re.M):
            return _BY_LANG["Objective-C"]
    elif ext == ".h":
        if re.search(r"\b(?:class|namespace|template\s*<|public:|private:|std::)", sample):
            return _BY_LANG["C++"]
        if re.search(r"^\s*(?:@interface|#import|@protocol)\b", sample, re.M):
            return _BY_LANG["Objective-C"]
    elif ext == ".v" and re.search(r"^\s*(?:Require|Lemma|Theorem|Definition|Inductive)\b", sample, re.M):
        return _BY_LANG["Plain text"]
    return found


def language_name(path, override=None, sample=""):
    return language_for(path, override, sample).name


# ----------------------------------------------------------------- tokenizer
_NUM = r"0[xX][0-9a-fA-F_]+|0[bB][01_]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?[fFlLuUdD]*"


def _build(lang):
    if lang._rx is not None:
        return lang._rx
    alts = []
    for i, (rx, _k) in enumerate(lang.extra):
        alts.append(f"(?P<x{i}>{rx})")
    if lang.block:
        alts.append("(?P<bs>" + "|".join(re.escape(b[0]) for b in lang.block) + ")")
    if lang.line:
        alts.append("(?P<lc>" + "|".join(re.escape(x) for x in lang.line) + ")")
    if lang.triple:
        alts.append("(?P<tq>" + "|".join(re.escape(x) for x in lang.triple) + ")")
    if lang.strings:
        alts.append("(?P<st>" + "|".join(re.escape(x) for x in lang.strings) + ")")
    if lang.numbers:
        alts.append(rf"(?P<num>\b(?:{_NUM}))")
    alts.append(r"(?P<id>[A-Za-z_][\w$]*)")
    flags = re.MULTILINE
    lang._rx = re.compile("|".join(alts), flags)
    return lang._rx


_STR_END = {}


def _string_end(q):
    if q not in _STR_END:
        _STR_END[q] = re.compile(r"(?:\\.|[^\\" + re.escape(q) + r"])*" + re.escape(q))
    return _STR_END[q]


def _tokenize_generic(lines, lang):
    rx = _build(lang)
    out = []
    state = None
    kw, types = lang.kw, lang.types
    ci = lang.ci
    block_end = {b[0]: b[1] for b in lang.block}
    for line in lines:
        marks = []
        pos, n = 0, len(line)
        if state is not None:
            kind, end = state
            if kind == "b":
                j = line.find(end)
                if j < 0:
                    marks.append((0, n, "c"))
                    out.append(marks)
                    continue
                marks.append((0, j + len(end), "c"))
                pos, state = j + len(end), None
            else:                       # multi-line string
                j = _find_unescaped(line, end, 0)
                if j < 0:
                    marks.append((0, n, "s"))
                    out.append(marks)
                    continue
                marks.append((0, j + len(end), "s"))
                pos, state = j + len(end), None
        while pos < n:
            m = rx.search(line, pos)
            if not m:
                break
            g = m.lastgroup
            s, e = m.start(), m.end()
            if g == "id":
                w = m.group()
                key = w.lower() if ci else w
                if key in kw:
                    marks.append((s, e, "k"))
                elif key in types:
                    marks.append((s, e, "t"))
                pos = e
            elif g == "num":
                marks.append((s, e, "n"))
                pos = e
            elif g == "lc":
                marks.append((s, n, "c"))
                pos = n
            elif g == "bs":
                end = block_end[m.group()]
                j = line.find(end, e)
                if j < 0:
                    marks.append((s, n, "c"))
                    state = ("b", end)
                    pos = n
                else:
                    marks.append((s, j + len(end), "c"))
                    pos = j + len(end)
            elif g == "tq":
                d = m.group()
                j = _find_unescaped(line, d, e)
                if j < 0:
                    marks.append((s, n, "s"))
                    state = ("s", d)
                    pos = n
                else:
                    marks.append((s, j + len(d), "s"))
                    pos = j + len(d)
            elif g == "st":
                q = m.group()
                mm = _string_end(q).match(line, e)
                if mm:
                    marks.append((s, mm.end(), "s"))
                    pos = mm.end()
                elif q in lang.multiline_strings:
                    marks.append((s, n, "s"))
                    state = ("s", q)
                    pos = n
                else:
                    marks.append((s, n, "s"))
                    pos = n
            else:                       # extra pattern
                kind = lang.extra[int(g[1:])][1]
                if e == s:
                    pos = s + 1
                    continue
                marks.append((s, e, kind))
                pos = e
        out.append(marks)
    return out


def _find_unescaped(line, delim, start):
    i = start
    while True:
        j = line.find(delim, i)
        if j < 0:
            return -1
        bs = 0
        k = j - 1
        while k >= start and line[k] == "\\":
            bs += 1
            k -= 1
        if bs % 2 == 0:
            return j
        i = j + 1


_XML_RX = re.compile(r"(?P<cm><!--)|(?P<tag></?[A-Za-z_][\w:.-]*|<[!?][A-Za-z\[]*)|(?P<attr>[A-Za-z_:][\w:.-]*)(?=\s*=)|(?P<st>\"[^\"]*\"|'[^']*')|(?P<ent>&[#\w]+;)|(?P<cl>/?>|\?>)")


def _tokenize_xml(lines, lang):
    out = []
    in_comment = False
    for line in lines:
        marks = []
        pos, n = 0, len(line)
        if in_comment:
            j = line.find("-->")
            if j < 0:
                out.append([(0, n, "c")])
                continue
            marks.append((0, j + 3, "c"))
            pos, in_comment = j + 3, False
        while pos < n:
            m = _XML_RX.search(line, pos)
            if not m:
                break
            g = m.lastgroup
            if g == "cm":
                j = line.find("-->", m.end())
                if j < 0:
                    marks.append((m.start(), n, "c"))
                    in_comment = True
                    pos = n
                else:
                    marks.append((m.start(), j + 3, "c"))
                    pos = j + 3
                continue
            kind = {"tag": "k", "attr": "p", "st": "s", "ent": "n", "cl": "k"}[g]
            marks.append((m.start(), m.end(), kind))
            pos = m.end()
        out.append(marks)
    return out


_JSON_RX = re.compile(r"(?P<key>\"(?:\\.|[^\\\"])*\"(?=\s*:))|(?P<st>\"(?:\\.|[^\\\"])*\")|(?P<num>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|(?P<kw>\b(?:true|false|null)\b)|(?P<lc>//.*)")


def _tokenize_json(lines, lang):
    out = []
    for line in lines:
        marks = []
        for m in _JSON_RX.finditer(line):
            kind = {"key": "p", "st": "s", "num": "n", "kw": "k", "lc": "c"}[m.lastgroup]
            marks.append((m.start(), m.end(), kind))
        out.append(marks)
    return out


def _tokenize_ini(lines, lang):
    out = []
    com = tuple(lang.line)
    for line in lines:
        marks = []
        stripped = line.lstrip()
        off = len(line) - len(stripped)
        if stripped.startswith(com) and com:
            out.append([(off, len(line), "c")])
            continue
        if stripped.startswith("[") and "]" in stripped:
            out.append([(off, off + stripped.index("]") + 1, "d")])
            continue
        m = re.match(r"(\s*(?:- )?)([^\s=:#;][^=:]*?)(\s*[=:])", line)
        vstart = 0
        if m:
            marks.append((m.start(2), m.end(2), "p"))
            vstart = m.end(3)
        for t in re.finditer(r"\"(?:\\.|[^\\\"])*\"|'[^']*'|\b\d+(?:\.\d+)?\b|\b[A-Za-z_]+\b|\s[#;].*$", line[vstart:]):
            s, e = t.start() + vstart, t.end() + vstart
            txt = t.group()
            if txt[0] in "\"'":
                marks.append((s, e, "s"))
            elif txt[0].isdigit():
                marks.append((s, e, "n"))
            elif txt.lstrip()[0] in "#;":
                marks.append((s + (len(txt) - len(txt.lstrip())), e, "c"))
            elif txt in lang.kw:
                marks.append((s, e, "k"))
        out.append(marks)
    return out


def _tokenize_markdown(lines, lang):
    out = []
    fence = False
    for line in lines:
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
            out.append([(0, len(line), "d")])
            continue
        if fence:
            out.append([(0, len(line), "s")])
            continue
        marks = []
        if re.match(r"\s{0,3}#{1,6}\s", line):
            marks.append((0, len(line), "k"))
        elif re.match(r"\s*([-*+]|\d+\.)\s", line):
            m = re.match(r"\s*([-*+]|\d+\.)", line)
            marks.append((m.start(1), m.end(1), "d"))
        elif line.startswith(">"):
            marks.append((0, len(line), "c"))
        for m in re.finditer(r"`[^`]+`", line):
            marks.append((m.start(), m.end(), "s"))
        for m in re.finditer(r"\*\*[^*]+\*\*|__[^_]+__", line):
            marks.append((m.start(), m.end(), "t"))
        for m in re.finditer(r"\[[^\]]*\]\([^)]*\)", line):
            marks.append((m.start(), m.end(), "p"))
        out.append(sorted(marks))
    return out


_CSS_RX = re.compile(r"(?P<at>@[\w-]+)|(?P<prop>[A-Za-z-]+)(?=\s*:)|(?P<st>\"[^\"]*\"|'[^']*')|(?P<num>#[0-9a-fA-F]{3,8}\b|-?\d+(?:\.\d+)?(?:px|em|rem|%|vh|vw|pt|s|ms|deg)?)|(?P<sel>[.#][A-Za-z_][\w-]*)")


def _tokenize_css(lines, lang):
    out = []
    in_block = False
    for line in lines:
        marks = []
        pos, n = 0, len(line)
        if in_block:
            j = line.find("*/")
            if j < 0:
                out.append([(0, n, "c")])
                continue
            marks.append((0, j + 2, "c"))
            pos, in_block = j + 2, False
        while pos < n:
            ci = line.find("/*", pos)
            seg_end = ci if ci >= 0 else n
            for m in _CSS_RX.finditer(line, pos, seg_end):
                kind = {"at": "d", "prop": "p", "st": "s", "num": "n", "sel": "t"}[m.lastgroup]
                marks.append((m.start(), m.end(), kind))
            if ci < 0:
                break
            cj = line.find("*/", ci + 2)
            if cj < 0:
                marks.append((ci, n, "c"))
                in_block = True
                break
            marks.append((ci, cj + 2, "c"))
            pos = cj + 2
        out.append(sorted(marks))
    return out


def highlight(lines, lang):
    """Return per-line marks [(start, end, kind)] for the whole document."""
    if lang is None or lang is PLAIN and not lang.strings:
        return [[] for _ in lines]
    sp = lang.special
    if sp == "xml":
        return _tokenize_xml(lines, lang)
    if sp == "json":
        return _tokenize_json(lines, lang)
    if sp == "ini":
        return _tokenize_ini(lines, lang)
    if sp == "markdown":
        return _tokenize_markdown(lines, lang)
    if sp == "css":
        return _tokenize_css(lines, lang)
    return _tokenize_generic(lines, lang)
