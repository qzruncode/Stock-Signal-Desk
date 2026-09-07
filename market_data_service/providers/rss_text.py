"""Text-only normalization for RSS entries and their document attachments."""

from __future__ import annotations

from hashlib import sha256
from html import escape
from html.parser import HTMLParser
import json
from pathlib import PurePosixPath
import re
from typing import Any, Iterable
from urllib.parse import unquote, urlparse
import xml.etree.ElementTree as ET


_TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".json",
    ".xml",
    ".rss",
    ".atom",
    ".html",
    ".htm",
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".rtf",
}

_TEXT_APPLICATION_MIMES = {
    "application/atom+xml",
    "application/csv",
    "application/feed+json",
    "application/json",
    "application/ld+json",
    "application/msword",
    "application/pdf",
    "application/rss+xml",
    "application/rtf",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/xhtml+xml",
    "application/xml",
    "text/csv",
    "text/tab-separated-values",
}

_NON_TEXT_PREFIXES = ("audio/", "image/", "video/")
_NON_TEXT_EXTENSIONS = {
    ".aac",
    ".avi",
    ".bmp",
    ".flac",
    ".gif",
    ".jpeg",
    ".jpg",
    ".m4a",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".ogg",
    ".png",
    ".svg",
    ".webm",
    ".webp",
    ".wav",
}


def _extension(url: str, title: str = "") -> str:
    for value in (url, title):
        if not value:
            continue
        try:
            suffix = PurePosixPath(unquote(urlparse(value).path)).suffix.lower()
        except ValueError:
            suffix = PurePosixPath(value).suffix.lower()
        if suffix:
            return suffix
    return ""


def is_text_document_reference(
    *,
    url: str,
    mime_type: str = "",
    title: str = "",
) -> bool:
    """Classify a reference without downloading it.

    Unknown references are kept only when their extension is explicitly
    text-bearing. Declared image/audio/video MIME types always lose.
    """
    mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    extension = _extension(str(url or ""), str(title or ""))
    if extension in _NON_TEXT_EXTENSIONS:
        return False
    if mime.startswith(_NON_TEXT_PREFIXES) and extension not in _TEXT_EXTENSIONS:
        return False
    if mime.startswith("text/") or mime in _TEXT_APPLICATION_MIMES:
        return True
    return extension in _TEXT_EXTENSIONS


class _TextOnlyHtmlParser(HTMLParser):
    _SAFE_TAGS = {
        "article",
        "b",
        "blockquote",
        "br",
        "code",
        "div",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "ol",
        "p",
        "pre",
        "section",
        "strong",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
    _VOID_TAGS = {"br"}
    _DROP_WITH_CONTENT = {
        "audio",
        "canvas",
        "iframe",
        "object",
        "script",
        "style",
        "svg",
        "video",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._drop_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        normalized = tag.lower()
        if normalized in self._DROP_WITH_CONTENT:
            self._drop_depth += 1
            return
        if self._drop_depth:
            return
        if normalized == "img":
            alt = next(
                (str(value) for key, value in attrs if key.lower() == "alt" and value),
                "",
            )
            if alt:
                self.parts.append(escape(alt))
            return
        if normalized in self._SAFE_TAGS:
            self.parts.append(f"<{normalized}>")

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() in self._DROP_WITH_CONTENT:
            self._drop_depth = max(0, self._drop_depth - 1)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in self._DROP_WITH_CONTENT:
            self._drop_depth = max(0, self._drop_depth - 1)
            return
        if (
            not self._drop_depth
            and normalized in self._SAFE_TAGS
            and normalized not in self._VOID_TAGS
        ):
            self.parts.append(f"</{normalized}>")

    def handle_data(self, data: str) -> None:
        if not self._drop_depth:
            self.parts.append(escape(data))


class _TextDocumentLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.attachments: list[dict[str, Any]] = []
        self._anchor: dict[str, Any] | None = None
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        normalized = tag.casefold()
        attributes = {str(key).casefold(): str(value or "") for key, value in attrs}
        url = ""
        if normalized == "a":
            url = attributes.get("href", "")
            self._anchor = {"url": url}
            self._anchor_text = []
        elif normalized in {"embed", "iframe"}:
            url = attributes.get("src", "")
        elif normalized == "object":
            url = attributes.get("data", "")
        if (
            url
            and normalized != "a"
            and is_text_document_reference(
                url=url,
                mime_type=attributes.get("type", ""),
                title=attributes.get("title", ""),
            )
        ):
            self.attachments.append(
                {
                    "url": url,
                    "mime_type": attributes.get("type", ""),
                    "title": attributes.get("title", ""),
                }
            )

    def handle_data(self, data: str) -> None:
        if self._anchor is not None:
            self._anchor_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "a" or self._anchor is None:
            return
        title = re.sub(
            r"\s+",
            " ",
            "".join(self._anchor_text),
        ).strip()
        url = str(self._anchor.get("url") or "")
        if is_text_document_reference(
            url=url,
            title=title,
        ):
            self.attachments.append(
                {
                    "url": url,
                    "mime_type": "",
                    "title": title,
                }
            )
        self._anchor = None
        self._anchor_text = []


def extract_text_document_links(value: Any) -> list[dict[str, Any]]:
    raw = str(value or "")
    if not raw:
        return []
    parser = _TextDocumentLinkParser()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        return []
    deduped: dict[str, dict[str, Any]] = {}
    for attachment in parser.attachments:
        url = str(attachment.get("url") or "").strip()
        if url:
            deduped.setdefault(url, attachment)
    return list(deduped.values())


def sanitize_text_html(value: Any) -> str:
    """Remove every media resource URL while preserving readable text."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    parser = _TextOnlyHtmlParser()
    try:
        parser.feed(raw)
        parser.close()
        text = "".join(parser.parts).strip()
    except Exception:
        text = re.sub(
            r"<(?:img|audio|video|source|picture|svg|canvas|object|embed|iframe)\b[^>]*>.*?</(?:audio|video|picture|svg|canvas|object|iframe)>",
            " ",
            raw,
            flags=re.I | re.S,
        )
        text = escape(re.sub(r"<[^>]+>", " ", text))
    return text.strip()


def filter_text_attachments(
    attachments: Iterable[Any],
) -> tuple[list[dict[str, Any]], int]:
    kept: list[dict[str, Any]] = []
    discarded = 0
    for value in attachments:
        if not isinstance(value, dict):
            continue
        url = str(value.get("url") or "").strip()
        mime = str(
            value.get("mime_type") or value.get("mime") or value.get("type") or ""
        ).strip()
        title = str(value.get("title") or value.get("name") or "").strip()
        if not url or not is_text_document_reference(
            url=url,
            mime_type=mime,
            title=title,
        ):
            discarded += 1
            continue
        kept.append(
            {
                "url": url,
                "mime_type": mime,
                "title": title,
                "size": value.get("size") or value.get("size_in_bytes"),
                "duration": None,
            }
        )
    return kept, discarded


def item_content_hash(item: dict[str, Any]) -> str:
    payload = {
        "id": item.get("id") or item.get("guid") or "",
        "title": item.get("title") or "",
        "link": item.get("link") or "",
        "summary": item.get("summary") or "",
        "content_html": item.get("content_html") or "",
        "attachments": item.get("attachments") or [],
    }
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def normalize_text_item(item: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical text-only RSS item."""
    normalized = dict(item)
    embedded_documents = extract_text_document_links(
        normalized.get("content_html") or normalized.get("summary") or ""
    )
    attachments, discarded = filter_text_attachments(
        [
            *(normalized.get("attachments") or []),
            *embedded_documents,
        ]
    )
    attachments = list(
        {
            str(value.get("url") or ""): value
            for value in attachments
            if value.get("url")
        }.values()
    )
    normalized["attachments"] = attachments
    content_html = sanitize_text_html(
        normalized.get("content_html") or normalized.get("summary") or ""
    )
    normalized["content_html"] = content_html
    normalized["summary"] = re.sub(
        r"\s+",
        " ",
        re.sub(r"<[^>]+>", " ", normalized["content_html"]),
    ).strip()
    normalized.pop("image", None)
    normalized.pop("banner", None)
    normalized.pop("media", None)
    normalized.pop("itunes_image", None)
    normalized["_discarded_non_text"] = discarded
    normalized["content_hash"] = item_content_hash(normalized)
    return normalized


def filter_raw_feed_bytes(content: bytes, format: str) -> bytes:
    """Strip non-text media fields from RSS/Atom/JSON/RSS3 exports."""
    normalized_format = str(format or "").lower()
    if normalized_format in {"json", "rss3"}:
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return content

        def clean(value: Any) -> None:
            if isinstance(value, dict):
                for key in list(value):
                    lowered = str(key).lower()
                    if lowered in {
                        "image",
                        "banner",
                        "media",
                        "icon",
                        "favicon",
                        "itunes_image",
                    }:
                        value.pop(key, None)
                        continue
                    if lowered in {"attachments", "enclosures"}:
                        kept, _discarded = filter_text_attachments(value.get(key) or [])
                        value[key] = kept
                        continue
                    clean(value[key])
            elif isinstance(value, list):
                for item in value:
                    clean(item)

        clean(payload)
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return content
    for parent in root.iter():
        for child in list(parent):
            raw_tag = str(child.tag)
            local = raw_tag.rsplit("}", 1)[-1].lower()
            namespace = (
                raw_tag[1:].split("}", 1)[0].lower() if raw_tag.startswith("{") else ""
            )
            is_media_namespace = any(
                token in namespace
                for token in ("search.yahoo.com/mrss", "itunes", "media")
            )
            if is_media_namespace and local in {
                "content",
                "group",
                "image",
                "thumbnail",
            }:
                parent.remove(child)
                continue
            if local in {"image", "icon", "logo"}:
                parent.remove(child)
                continue
            if local == "enclosure":
                url = str(child.attrib.get("url") or child.attrib.get("href") or "")
                mime = str(child.attrib.get("type") or "")
                title = str(child.attrib.get("title") or "")
                if not is_text_document_reference(
                    url=url,
                    mime_type=mime,
                    title=title,
                ):
                    parent.remove(child)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


__all__ = [
    "extract_text_document_links",
    "filter_text_attachments",
    "filter_raw_feed_bytes",
    "is_text_document_reference",
    "item_content_hash",
    "normalize_text_item",
    "sanitize_text_html",
]
