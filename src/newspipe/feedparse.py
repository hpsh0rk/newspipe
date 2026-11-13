"""Pure, bounded RSS/Atom source evidence. Parsing never follows linked URLs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import math
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

ATOM_NS = "http://www.w3.org/2005/Atom"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"
MAX_BODY = 2 * 1024 * 1024
MAX_ENTRIES = 1000
MAX_TEXT = 20000
MAX_MARKUP = 40000


class FeedError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class FeedEntry:
    native_id: str
    title: str
    permalink: str
    source_text: str
    source_markup: str
    text_truncated: bool
    published_ts: float | None
    updated_ts: float | None
    author: str
    body_field: str
    references: tuple[dict, ...]


def timestamp(value: str, *, iso=False) -> float | None:
    try:
        date = datetime.fromisoformat(value.strip().replace("Z", "+00:00")) if iso else parsedate_to_datetime(value)
        if date.tzinfo is None:
            return None
        stamp = date.timestamp()
        return stamp if math.isfinite(stamp) and stamp >= 0 else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def source_link(value: str, base: str = "") -> str:
    value = urljoin(base, value.strip()) if value.strip() else ""
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname or any(ord(c) < 32 for c in value):
            return ""
        parts.port
        return value
    except ValueError:
        return ""


def text_evidence(markup: str, *, base_url="", plain=False):
    """Keep bounded visible text and typed links; inline source content may be an excerpt."""
    references = []
    if plain:
        text = " ".join(markup.split())
    else:
        class Extract(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.parts = []
                self.hidden = []

            def handle_starttag(self, tag, attrs):
                tag = tag.rsplit(":", 1)[-1]
                if tag in {"script", "style", "noscript", "template", "iframe", "svg", "form"}:
                    self.hidden.append(tag)
                if self.hidden:
                    return
                if tag in {"p", "div", "br", "li", "pre", "blockquote", "tr", "section", "article"}:
                    self.parts.append(" ")
                if tag == "a" and len(references) < 20:
                    href = source_link(dict(attrs).get("href") or "", base_url)
                    ref = {"role": "linked", "url": href}
                    if href and ref not in references:
                        references.append(ref)

            def handle_startendtag(self, tag, attrs):
                self.handle_starttag(tag, attrs)
                self.handle_endtag(tag)

            def handle_endtag(self, tag):
                tag = tag.rsplit(":", 1)[-1]
                if self.hidden:
                    if tag == self.hidden[-1]:
                        self.hidden.pop()
                else:
                    self.parts.append(" ")

            def handle_data(self, data):
                if not self.hidden:
                    self.parts.append(data)

        extractor = Extract()
        extractor.feed(markup)
        extractor.close()
        text = " ".join("".join(extractor.parts).split())
    truncated = len(text) > MAX_TEXT or len(markup) > MAX_MARKUP
    return text[:MAX_TEXT], markup[:MAX_MARKUP], truncated, tuple(references)


def _content(node, name, ns=""):
    child = node.find(ns + name)
    return (child.text or "").strip() if child is not None else ""


def _atom_content(node):
    if node is None or node.get("src"):
        return "", False
    if node.get("type", "text") == "xhtml":
        return "".join(ET.tostring(child, encoding="unicode") for child in node), False
    return node.text or "", node.get("type", "text") == "text"


def _xhtml_references(node, base):
    references, stack = [], [(node, base)]
    while stack and len(references) < 20:
        current, parent_base = stack.pop()
        current_base = urljoin(parent_base, current.get(XML_BASE, ""))
        if current.tag.rsplit("}", 1)[-1] == "a":
            link = source_link(current.get("href", ""), current_base)
            ref = {"role": "linked", "url": link}
            if link and ref not in references:
                references.append(ref)
        stack.extend((child, current_base) for child in reversed(list(current)))
    return tuple(references)


def parse(body: bytes, *, base_url: str = "") -> tuple[FeedEntry, ...]:
    if not isinstance(body, bytes) or len(body) > MAX_BODY:
        raise FeedError("too_large")
    # Reject declarations before ElementTree can expand internal entities (also UTF-16/32).
    declarations = body.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
        raise FeedError("invalid_feed")
    try:
        root = ET.fromstring(body)
    except (ET.ParseError, ValueError):
        raise FeedError("invalid_feed") from None
    atom = root.tag == "{" + ATOM_NS + "}feed"
    if root.tag.rsplit("}", 1)[-1].lower() == "rdf":
        raise FeedError("unsupported_feed")
    if not atom and (root.tag != "rss" or root.find("channel") is None):
        raise FeedError("invalid_feed")
    ns = "{" + ATOM_NS + "}" if atom else ""
    nodes = root.findall(ns + "entry") if atom else root.findall("channel/item")
    if len(nodes) > MAX_ENTRIES:
        raise FeedError("too_large")
    base = urljoin(base_url, root.get(XML_BASE, ""))
    if not atom:
        base = urljoin(base, root.find("channel").get(XML_BASE, ""))
    items = []
    for node in nodes:
        entry_base = urljoin(base, node.get(XML_BASE, ""))
        if atom:
            links = [link for link in node.findall(ns + "link") if link.get("rel", "alternate") == "alternate"]
            link = next((link for link in links if link.get("type", "text/html") in {"text/html", "application/xhtml+xml"}), None)
            permalink = source_link(link.get("href", ""), urljoin(entry_base, link.get(XML_BASE, ""))) if link is not None else ""
            native = _content(node, "id", ns) or permalink
            body_node = node.find(ns + "content")
            markup, plain = _atom_content(body_node)
            field = "content"
            if not markup:
                body_node = node.find(ns + "summary")
                markup, plain = _atom_content(body_node)
                field = "summary"
            body_base = urljoin(entry_base, body_node.get(XML_BASE, "")) if body_node is not None else entry_base
            title_markup, title_plain = _atom_content(node.find(ns + "title"))
            author = _content(node.find(ns + "author"), "name", ns) if node.find(ns + "author") is not None else ""
            published = timestamp(_content(node, "published", ns), iso=True)
            updated = timestamp(_content(node, "updated", ns), iso=True)
        else:
            permalink = source_link(_content(node, "link"), entry_base)
            native = _content(node, "guid") or permalink
            markup = _content(node, "{" + CONTENT_NS + "}encoded")
            field = "content:encoded" if markup else "description"
            markup = markup or _content(node, "description")
            plain, title_plain = False, True
            body_node, body_base = None, entry_base
            title_markup = _content(node, "title")
            author = _content(node, "author")
            published = timestamp(_content(node, "pubDate"))
            updated = None
        if not native:
            raise FeedError("invalid_feed")
        text, raw, truncated, refs = text_evidence(markup, base_url=body_base, plain=plain)
        if body_node is not None and body_node.get("type") == "xhtml":
            refs = _xhtml_references(body_node, entry_base)
        title = text_evidence(title_markup, plain=title_plain)[0]
        items.append(FeedEntry(native, title[:500], permalink, text, raw,
                               truncated or len(title) > 500, published, updated,
                               author[:500], field, refs))
    return tuple(items)
