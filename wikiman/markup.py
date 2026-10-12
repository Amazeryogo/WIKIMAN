"""Markdown rendering, wikilinks, sanitising, diffs and small formatting helpers."""
import difflib
import re
import unicodedata
import xml.etree.ElementTree as etree
from datetime import datetime, timezone
from urllib.parse import quote

import markdown
import nh3
from markdown.extensions import Extension
from markdown.inlinepatterns import InlineProcessor
from markupsafe import Markup

WIKILINK_RE = r"\[\[([^\[\]|\n]+?)(?:\|([^\[\]\n]+?))?\]\]"


def slugify(title):
    s = unicodedata.normalize("NFKC", title or "").strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:200]


class _WikiLinkProcessor(InlineProcessor):
    def __init__(self, pattern, md, resolver, collected):
        super().__init__(pattern, md)
        self.resolver = resolver
        self.collected = collected

    def handleMatch(self, m, data):
        target = m.group(1).strip()
        label = (m.group(2) or target).strip()
        slug = slugify(target)
        if not slug:
            return None, None, None
        self.collected.add(slug)
        el = etree.Element("a")
        if self.resolver(slug):
            el.set("href", f"/wiki/{quote(slug)}")
            el.set("class", "wikilink")
        else:
            el.set("href", f"/wiki/{quote(slug)}/edit?title={quote(target)}")
            el.set("class", "wikilink missing")
            el.set("title", f"{target} (page does not exist)")
        el.text = label
        return el, m.start(0), m.end(0)


class _WikiLinkExtension(Extension):
    def __init__(self, resolver, collected):
        super().__init__()
        self.resolver = resolver
        self.collected = collected

    def extendMarkdown(self, md):
        md.inlinePatterns.register(
            _WikiLinkProcessor(WIKILINK_RE, md, self.resolver, self.collected),
            "wikilink",
            175,
        )


class _StrikeExtension(Extension):
    def extendMarkdown(self, md):
        from markdown.inlinepatterns import SimpleTagInlineProcessor

        md.inlinePatterns.register(
            SimpleTagInlineProcessor(r"(~~)(.+?)~~", "del"), "strike", 40
        )


ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "code", "dd", "del", "details", "div",
    "dl", "dt", "em", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins",
    "kbd", "li", "mark", "ol", "p", "pre", "s", "small", "span", "strong", "sub",
    "summary", "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}
ALLOWED_ATTRS = {
    "*": {"id", "class", "title"},
    "a": {"href"},
    "img": {"src", "alt", "width", "height"},
    "td": {"align", "style"},
    "th": {"align", "style"},
    "ol": {"start"},
}


REDIRECT_RE = re.compile(r"^\s*#REDIRECT\s*\[\[([^\[\]|\n]+?)(?:\|[^\[\]\n]*)?\]\]", re.I)
_CODE_RE = re.compile(r"(```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]*`)")


def redirect_target(body):
    """Slug a '#REDIRECT [[Title]]' page points at, or None."""
    m = REDIRECT_RE.match(body or "")
    return slugify(m.group(1)) or None if m else None


def rewrite_links(body, old_slug, new_title):
    """Point every [[link]] to old_slug at new_title, keeping custom labels.

    Code blocks and inline code are left alone.
    """
    def sub(m):
        if slugify(m.group(1)) != old_slug:
            return m.group(0)
        label = m.group(2)
        return f"[[{new_title}|{label}]]" if label else f"[[{new_title}]]"

    parts = _CODE_RE.split(body or "")
    for i in range(0, len(parts), 2):  # even indexes are outside code
        parts[i] = re.sub(WIKILINK_RE, sub, parts[i])
    return "".join(parts)


def link_targets(body):
    """Slugs of every wikilink in a body (cheap regex pass, no rendering)."""
    out = set()
    for m in re.finditer(WIKILINK_RE, body or ""):
        s = slugify(m.group(1))
        if s:
            out.add(s)
    return out


def render(body, resolver=lambda slug: True):
    """Render wiki markdown to safe HTML. Returns (Markup, set_of_link_slugs)."""
    collected = set()
    md = markdown.Markdown(
        extensions=[
            "fenced_code",
            "tables",
            "sane_lists",
            "toc",
            "def_list",
            "footnotes",
            "attr_list",
            _StrikeExtension(),
            _WikiLinkExtension(resolver, collected),
        ],
        extension_configs={"toc": {"permalink": False, "title": "Contents"}},
    )
    html = md.convert(body or "")
    clean = nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        url_schemes={"http", "https", "mailto"},
        filter_style_properties={"text-align"},
    )
    return Markup(clean), collected


def diff_lines(old, new, context=3):
    """Line diff as a list of (kind, old_no, new_no, text); kind in ctx/del/add/skip."""
    a = (old or "").splitlines()
    b = (new or "").splitlines()
    rows = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for group in sm.get_grouped_opcodes(context):
        if rows:
            rows.append(("skip", None, None, ""))
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for k in range(i2 - i1):
                    rows.append(("ctx", i1 + k + 1, j1 + k + 1, a[i1 + k]))
                continue
            if tag in ("replace", "delete"):
                for k in range(i1, i2):
                    rows.append(("del", k + 1, None, a[k]))
            if tag in ("replace", "insert"):
                for k in range(j1, j2):
                    rows.append(("add", None, k + 1, b[k]))
    return rows


def diff_stats(old, new):
    rows = diff_lines(old, new, context=0)
    return (
        sum(1 for r in rows if r[0] == "add"),
        sum(1 for r in rows if r[0] == "del"),
    )


def _parse_ts(ts):
    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=timezone.utc)


def format_time(ts):
    """'3 minutes ago' style, with the exact UTC time as a tooltip."""
    if not ts:
        return ""
    try:
        dt = _parse_ts(ts)
    except ValueError:
        return ts
    secs = int((datetime.now(timezone.utc) - dt).total_seconds())
    if secs < 60:
        rel = "just now"
    elif secs < 3600:
        n = secs // 60
        rel = f"{n} minute{'s' if n != 1 else ''} ago"
    elif secs < 86400:
        n = secs // 3600
        rel = f"{n} hour{'s' if n != 1 else ''} ago"
    elif secs < 86400 * 30:
        n = secs // 86400
        rel = f"{n} day{'s' if n != 1 else ''} ago"
    else:
        rel = dt.strftime("%d %b %Y")
    return Markup('<time datetime="{0}Z" title="{0} UTC">{1}</time>').format(ts, rel)
