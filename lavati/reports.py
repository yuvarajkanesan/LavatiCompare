"""Difference reports (HTML, text, XML) for folder and text comparisons. No Qt imports."""
import html
import re
import time
from xml.sax.saxutils import escape as _xesc, quoteattr as _xattr

from . import engine

STATUS_LABEL = {"changed": "Different", "newer": "Left newer", "older": "Left older", "left": "Left only",
                "right": "Right only", "same": "Identical"}
STATUS_COLOR = {"changed": "#c62828", "newer": "#b45309", "older": "#6a35c9", "left": "#1a5fd0",
                "right": "#6a35c9", "same": "#8b91a3"}
_BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def _x(s):
    return _xesc(_BAD_XML.sub("�", s))


def fmt_size(n):
    if n is None:
        return ""
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def fmt_time(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else ""


HTML_HEAD = ("<!doctype html><html><head><meta charset='utf-8'><title>{title}</title><style>"
             "body{{font-family:Segoe UI,Helvetica,Arial,sans-serif;margin:32px;color:#1d2030}}"
             "h1{{font-size:20px}}table{{border-collapse:collapse;width:100%}}"
             "td,th{{padding:5px 10px;border-bottom:1px solid #e3e5ec;text-align:left;font-size:13px;vertical-align:top}}"
             "th{{background:#f5f6fa}}.mono{{font-family:Consolas,'JetBrains Mono',monospace;white-space:pre-wrap}}"
             ".del{{background:#fde6e6}}.add{{background:#e3f4e8}}.unimp{{background:#f3f4f8}}.sep{{background:#eef0f5;color:#8b91a3}}"
             ".num{{color:#8b91a3;text-align:right;width:40px}}.sum span{{margin-right:18px}}</style></head><body>")


# ================================================================== folders
def select_nodes(nodes, show="all"):
    """File nodes for the report: all | differences | same | orphans | newer | older."""
    out = []
    for n in engine.walk(nodes):
        if n.is_dir and not n.archive:
            continue
        s = n.status
        if show == "all" or (show == "differences" and s != "same") or (show == "same" and s == "same") \
                or (show == "orphans" and s in ("left", "right")) or show == s:
            out.append(n)
    return out


def folder_report_lines(left, right, nodes, show="all", counter=None):
    counter = counter or engine.count_statuses(nodes)
    rows = select_nodes(nodes, show)
    return rows, counter


def write_folder_report(path, fmt, left, right, nodes, show="all", counter=None, title="Folder comparison report"):
    rows, counter = folder_report_lines(left, right, nodes, show, counter)
    gen = time.strftime("%Y-%m-%d %H:%M")
    summary = {k: counter.get(k, 0) for k in engine.STATUSES}
    if fmt == "html":
        body = []
        for n in rows:
            col = STATUS_COLOR[n.status]
            body.append(
                f"<tr><td style='color:{col};font-weight:600'>{STATUS_LABEL[n.status]}</td>"
                f"<td class='mono'>{html.escape(n.rel)}</td>"
                f"<td>{fmt_size(n.left['size']) if n.left else ''}</td><td>{fmt_time(n.left['mtime']) if n.left else ''}</td>"
                f"<td>{fmt_size(n.right['size']) if n.right else ''}</td><td>{fmt_time(n.right['mtime']) if n.right else ''}</td></tr>")
        doc = (HTML_HEAD.format(title=html.escape(title)) + f"<h1>{html.escape(title)}</h1>"
               f"<p><b>Left:</b> {html.escape(left)}<br><b>Right:</b> {html.escape(right)}<br><b>Generated:</b> {gen}"
               f" &middot; <b>Showing:</b> {html.escape(show)}</p><p class='sum'>"
               + "".join(f"<span style='color:{STATUS_COLOR[k]}'>{STATUS_LABEL[k]}: <b>{summary[k]}</b></span>" for k in engine.STATUSES)
               + "</p><table><tr><th>Status</th><th>Path</th><th>Left size</th><th>Left modified</th>"
                 "<th>Right size</th><th>Right modified</th></tr>" + "".join(body) + "</table></body></html>")
    elif fmt == "xml":
        parts = ['<?xml version="1.0" encoding="UTF-8"?>',
                 f'<lavati-report type="folder" generated={_xattr(gen)}>',
                 f'  <left path={_xattr(left)}/>', f'  <right path={_xattr(right)}/>',
                 "  <summary " + " ".join(f'{k}="{summary[k]}"' for k in engine.STATUSES) + "/>", "  <files>"]
        for n in rows:
            attrs = f'status="{n.status}" path={_xattr(n.rel)}'
            if n.left:
                attrs += f' left-size="{n.left["size"]}" left-modified={_xattr(fmt_time(n.left["mtime"]))}'
            if n.right:
                attrs += f' right-size="{n.right["size"]}" right-modified={_xattr(fmt_time(n.right["mtime"]))}'
            parts.append(f"    <file {attrs}/>")
        parts += ["  </files>", "</lavati-report>"]
        doc = "\n".join(parts) + "\n"
    else:
        w = max([len(n.rel) for n in rows] + [10])
        lines = [title, "=" * len(title), f"Left:   {left}", f"Right:  {right}", f"Generated: {gen}   Showing: {show}", "",
                 "  ".join(f"{STATUS_LABEL[k]}: {summary[k]}" for k in engine.STATUSES), "",
                 f"{'Status':<12} {'Path':<{w}}  {'Left size':>10}  {'Right size':>10}  Left modified        Right modified"]
        for n in rows:
            lines.append(f"{STATUS_LABEL[n.status]:<12} {n.rel:<{w}}  {fmt_size(n.left['size']) if n.left else '':>10}  "
                         f"{fmt_size(n.right['size']) if n.right else '':>10}  "
                         f"{fmt_time(n.left['mtime']) if n.left else '':<19}  {fmt_time(n.right['mtime']) if n.right else ''}")
        doc = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)
    return len(rows)


# =================================================================== text
def _rows(L, R, rules, anchors, context):
    """[(kind, li, ri)] with separators ('sep') when context is limited."""
    ops = engine.diff_opcodes(L, R, rules=rules, anchors=anchors)
    pairs, hunks = engine.text_pairs(L, R, ops, rules)
    n = len(pairs)
    if context is None:
        return [(p[2], p[0], p[1]) for p in pairs], len(hunks), pairs
    show = [False] * n
    for i, p in enumerate(pairs):
        if p[2] not in ("eq", "unimp"):
            for k in range(max(0, i - context), min(n, i + context + 1)):
                show[k] = True
    out, prev = [], True
    for i, p in enumerate(pairs):
        if show[i]:
            if not prev and out:
                out.append(("sep", None, None))
            out.append((p[2], p[0], p[1]))
        prev = show[i]
    return out, len(hunks), pairs


def write_text_report(path, fmt, left_path, right_path, L, R, rules, anchors=None, context=None,
                      title="Text comparison report"):
    rules = rules or engine.TextRules()
    rows, nh, pairs = _rows(L, R, rules, anchors, context)
    counts = {"del": 0, "ins": 0, "mod": 0}
    for p in pairs:
        if p[2] in counts:
            counts[p[2]] += 1
    gen = time.strftime("%Y-%m-%d %H:%M")
    if fmt == "html":
        body = []
        for kind, li, ri in rows:
            if kind == "sep":
                body.append("<tr class='sep'><td colspan='4'>…</td></tr>")
                continue
            cls = {"mod": ("del", "add"), "del": ("del", ""), "ins": ("", "add"), "unimp": ("unimp", "unimp")}.get(kind, ("", ""))
            lt = html.escape(L[li]) if li is not None else ""
            rt = html.escape(R[ri]) if ri is not None else ""
            body.append(f"<tr><td class='num'>{li + 1 if li is not None else ''}</td><td class='mono {cls[0]}'>{lt}</td>"
                        f"<td class='num'>{ri + 1 if ri is not None else ''}</td><td class='mono {cls[1]}'>{rt}</td></tr>")
        doc = (HTML_HEAD.format(title=html.escape(title)) + f"<h1>{html.escape(title)}</h1>"
               f"<p><b>Left:</b> {html.escape(left_path)}<br><b>Right:</b> {html.escape(right_path)}<br><b>Generated:</b> {gen}</p>"
               f"<p class='sum'><span><b>{nh}</b> hunks</span><span style='color:#1b7a3a'>+{counts['ins']} added</span>"
               f"<span style='color:#c62828'>&minus;{counts['del']} removed</span><span style='color:#b45309'>~{counts['mod']} modified</span></p>"
               "<table><tr><th>#</th><th>Left</th><th>#</th><th>Right</th></tr>" + "".join(body) + "</table></body></html>")
    elif fmt == "xml":
        parts = ['<?xml version="1.0" encoding="UTF-8"?>', f'<lavati-report type="text" generated={_xattr(gen)}>',
                 f'  <left path={_xattr(left_path)}/>', f'  <right path={_xattr(right_path)}/>',
                 f'  <summary hunks="{nh}" added="{counts["ins"]}" removed="{counts["del"]}" modified="{counts["mod"]}"/>', "  <lines>"]
        status = {"eq": "equal", "unimp": "unimportant", "mod": "modified", "del": "removed", "ins": "added"}
        for kind, li, ri in rows:
            if kind == "sep":
                parts.append("    <skipped/>")
                continue
            a = f' left-line="{li + 1}"' if li is not None else ""
            b = f' right-line="{ri + 1}"' if ri is not None else ""
            lt = f"<left>{_x(L[li])}</left>" if li is not None else ""
            rt = f"<right>{_x(R[ri])}</right>" if ri is not None else ""
            parts.append(f'    <line status="{status[kind]}"{a}{b}>{lt}{rt}</line>')
        parts += ["  </lines>", "</lavati-report>"]
        doc = "\n".join(parts) + "\n"
    else:
        lines = [title, "=" * len(title), f"Left:  {left_path}", f"Right: {right_path}", f"Generated: {gen}",
                 f"{nh} hunks, +{counts['ins']} added, -{counts['del']} removed, ~{counts['mod']} modified", ""]
        lines += engine.unified_diff(L, R, left_path, right_path, 3 if context is None else context, rules, anchors) \
            if context is not None or True else []
        doc = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)
    return nh
