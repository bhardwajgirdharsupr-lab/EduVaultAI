import os
from urllib.parse import parse_qs, urlencode, urlparse

from .base import BaseConnector, ConnectorResult, cleanup_title, fetch_json, fetch_page_title, normalize_url, strip_suffixes


class YouTubeConnector(BaseConnector):
    source = "youtube"
    platform = "YouTube"
    host_tokens = ("youtube.com", "youtu.be")

    def import_url(self, url):
        video_id = extract_video_id(url)
        metadata = self._fetch_oembed(url)
        if not metadata and video_id:
            metadata = self._fetch_data_api(video_id)

        if metadata:
            title = cleanup_title(metadata.get("title") or "YouTube video")
            author = metadata.get("author_name") or metadata.get("channelTitle") or "YouTube"
            return ConnectorResult(
                title=title[:180],
                source=self.source,
                platform=self.platform,
                url=canonical_youtube_url(url),
                description=f"Video by {author}. Imported from YouTube.",
                skills="Video learning",
            )

        title, warning = fetch_page_title(url)
        title = strip_suffixes(title, ("- YouTube", "| YouTube", " - YouTube"))
        if not title or title == url:
            title = "YouTube video"
        return ConnectorResult(
            title=cleanup_title(title)[:180],
            source=self.source,
            platform=self.platform,
            url=canonical_youtube_url(url),
            description=f"YouTube learning resource imported from {url}",
            skills="Video learning",
            warning=warning,
        )

    def _fetch_oembed(self, url):
        try:
            endpoint = "https://www.youtube.com/oembed?" + urlencode({"url": normalize_url(url), "format": "json"})
            return fetch_json(endpoint)
        except Exception:
            return None

    def _fetch_data_api(self, video_id):
        api_key = os.environ.get("YOUTUBE_API_KEY")
        if not api_key:
            return None
        endpoint = "https://www.googleapis.com/youtube/v3/videos?" + urlencode(
            {"part": "snippet", "id": video_id, "key": api_key}
        )
        try:
            data = fetch_json(endpoint)
        except Exception:
            return None
        items = data.get("items") or []
        if not items:
            return None
        return items[0].get("snippet") or None


def extract_video_id(url):
    parsed = urlparse(normalize_url(url))
    if parsed.netloc.endswith("youtu.be"):
        return parsed.path.strip("/").split("/")[0]
    query = parse_qs(parsed.query)
    if query.get("v"):
        return query["v"][0]
    if parsed.path.startswith("/shorts/") or parsed.path.startswith("/embed/"):
        parts = parsed.path.strip("/").split("/")
        return parts[1] if len(parts) > 1 else ""
    return ""


def canonical_youtube_url(url):
    video_id = extract_video_id(url)
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else normalize_url(url)
