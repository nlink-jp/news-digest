"""Article identity and text normalization.

Everything here decides what counts as "the same article", so changes ripple
through the whole corpus: `article_id` is the key of the seen index and of
every story reference. Changing `canonicalize_url` severs past records from
present ones and requires a `schema_version` bump plus a documented rebuild.

Standard library only.
"""

from __future__ import annotations

import hashlib
import html
import re
import urllib.parse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

# Query parameters that identify the referrer rather than the resource.
# Dropping them keeps one article from being stored twice under two URLs.
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "dclid", "yclid", "msclkid", "igshid", "mc_cid", "mc_eid",
    "ref", "ref_src", "ref_url", "referrer", "source", "spm", "at_medium",
    "at_campaign", "__twitter_impression", "s", "sh", "cmpid", "ncid", "sr_share",
}

SUMMARY_MAX_CHARS = 800

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def canonicalize_url(url: str) -> str:
    """The comparison form of an article URL.

    Lower-cases scheme and host, drops a leading `www.`, strips tracking
    parameters and the fragment, and removes a trailing slash. Parameters that
    survive keep their order, so the result is stable for a given input.
    """
    url = (url or "").strip()
    if not url:
        return ""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    query = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    ]
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urllib.parse.urlunsplit(
        (parts.scheme.lower(), netloc, path, urllib.parse.urlencode(query), "")
    )


def article_id(canonical_url: str) -> str:
    """`sha1:<16 hex>` of the canonical URL.

    The algorithm prefix is part of the value on purpose: the identity rule is
    the one thing a corpus cannot silently change, so the data says which rule
    produced it.
    """
    return "sha1:" + hashlib.sha1(canonical_url.encode("utf-8")).hexdigest()[:16]


def normalize_title(title: str) -> str:
    """A title reduced for duplicate detection across outlets.

    Removes an outlet-name suffix (`… | Some Blog`), a leading bracketed
    label, whitespace, and punctuation, so the same story syndicated to two
    sites collapses to one key.
    """
    t = html.unescape(title or "")
    t = re.sub(r"\s*[|｜]\s*[^|｜]{1,40}$", "", t)
    t = re.sub(r"^【[^】]{1,12}】\s*", "", t)
    t = re.sub(r"[\s　]+", "", t)
    t = re.sub(r"[「」『』【】\[\]()（）\"'“”‘’,、.。･・:：;；!！?？\-―ー~〜/／]", "", t)
    return t.lower()


def strip_html(raw: str, limit: int = SUMMARY_MAX_CHARS) -> str:
    """Feed summaries usually carry HTML. Reduce to plain text and cap it."""
    if not raw:
        return ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def derive_title(summary: str, limit: int = 90) -> str:
    """Build a headline from the body, for feeds whose entries have no title.

    Microblog-derived feeds (Mastodon and the like) publish untitled entries.
    Left alone they are all dropped by the minimum-title-length rule, so a
    headline is derived from the opening sentence instead.
    """
    text = (summary or "").strip()
    if not text:
        return ""
    text = re.sub(r"^(?:\s*#\s*\w+\s*)+", "", text)
    text = re.sub(r"^[\s*·•\-–—]+", "", text)
    if not text:
        text = summary.strip()
    # Break at the first sentence end. A period counts as one only when
    # followed by whitespace, so host names are not cut in half.
    match = re.search(r"^(.{20,%d}?)(?:[。！？!?]|\.\s|\s\*\s|$)" % limit, text)
    head = (match.group(1) if match else text[:limit]).strip()
    return head if len(head) >= 8 else text[:limit].strip()


def parse_date(raw: str) -> datetime | None:
    """Parse the date formats feeds actually emit, or return None.

    RSS 2.0 uses RFC 822, Atom uses RFC 3339, and RDF feeds use Dublin Core
    dates that are usually but not always ISO 8601. A naive result is read as
    UTC rather than as local time, so the same feed parses identically
    wherever the collector runs.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    iso = raw.replace("Z", "+00:00")
    for candidate in (iso, iso[:19], iso[:10]):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
