import json
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_USER_AGENT = "EduVaultBot/1.0 (+https://eduvault.local)"


@dataclass
class ConnectorResult:
    title: str
    source: str
    platform: str
    url: str
    description: str
    skills: str = ""
    warning: Optional[str] = None


class TitleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.title_parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data.strip())

    @property
    def value(self):
        return " ".join(part for part in self.title_parts if part).strip()


class BaseConnector:
    source = "web"
    platform = "Web"
    host_tokens = ()

    def can_handle(self, url):
        host = urlparse(url).netloc.lower()
        return any(token in host for token in self.host_tokens)

    def import_url(self, url):
        clean_url = normalize_url(url)
        title, warning = fetch_page_title(clean_url)
        return ConnectorResult(
            title=title,
            source=self.source,
            platform=self.platform_for_url(clean_url),
            url=clean_url,
            description=f"Imported from {clean_url}",
            warning=warning,
        )

    def platform_for_url(self, url):
        return self.platform


class WebConnector(BaseConnector):
    source = "web"
    platform = "Web"

    def can_handle(self, url):
        return True

    def platform_for_url(self, url):
        return urlparse(url).netloc or self.platform


def normalize_url(url):
    url = (url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
    return url


def fetch_text(url, timeout=6, limit=250_000, headers=None):
    request_headers = {"User-Agent": DEFAULT_USER_AGENT}
    request_headers.update(headers or {})
    req = Request(url, headers=request_headers)
    with urlopen(req, timeout=timeout) as response:
        return response.read(limit).decode("utf-8", errors="ignore")


def fetch_json(url, timeout=6, headers=None):
    return json.loads(fetch_text(url, timeout=timeout, headers=headers))


def fetch_page_title(url):
    clean_url = normalize_url(url)
    parsed = urlparse(clean_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return clean_url or "Imported resource", "Invalid URL format. Saved the resource with the URL as its title."
    try:
        parser = TitleParser()
        parser.feed(fetch_text(clean_url))
        return cleanup_title(parser.value or clean_url), None
    except Exception:
        return clean_url, "Could not fetch the page title, so EduVault saved the URL directly."


def cleanup_title(title):
    title = unescape(title or "").strip()
    title = re.sub(r"\s+", " ", title)
    return title


def strip_suffixes(title, suffixes):
    cleaned = cleanup_title(title)
    for suffix in suffixes:
        if cleaned.lower().endswith(suffix.lower()):
            cleaned = cleaned[: -len(suffix)].strip(" -|")
    return cleaned


def get_connector(url):
    clean_url = normalize_url(url)
    from .coursera import CourseraConnector
    from .udemy import UdemyConnector
    from .youtube import YouTubeConnector

    for connector in (YouTubeConnector(), CourseraConnector(), UdemyConnector()):
        if connector.can_handle(clean_url):
            return connector
    return WebConnector()


def import_url_metadata(url):
    clean_url = normalize_url(url)
    return get_connector(clean_url).import_url(clean_url)
