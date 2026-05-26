import os
import re
from base64 import b64encode
from urllib.parse import urlparse

from .base import BaseConnector, ConnectorResult, cleanup_title, fetch_json, fetch_page_title, strip_suffixes


class UdemyConnector(BaseConnector):
    source = "udemy"
    platform = "Udemy"
    host_tokens = ("udemy.com",)

    def import_url(self, url):
        course_slug = self._course_slug(url)
        api_result = self._fetch_api_metadata(course_slug) if course_slug else None
        if api_result:
            return api_result

        title, warning = fetch_page_title(url)
        title = self._title_from_path(url) or strip_suffixes(title, ("| Udemy", "- Udemy", " | Udemy"))
        if not title or title == url:
            title = "Udemy course"
        return ConnectorResult(
            title=cleanup_title(title)[:180],
            source=self.source,
            platform=self.platform,
            url=url,
            description=f"Udemy learning resource imported from {url}",
            skills=self._skills_from_slug(url),
            warning=warning,
        )

    def _course_slug(self, url):
        match = re.search(r"/course/([^/?#]+)/?", urlparse(url).path)
        return match.group(1) if match else ""

    def _fetch_api_metadata(self, course_slug):
        client_id = os.environ.get("UDEMY_CLIENT_ID")
        client_secret = os.environ.get("UDEMY_CLIENT_SECRET")
        if not client_id or not client_secret:
            return None
        api_url = f"https://www.udemy.com/api-2.0/courses/{course_slug}/?fields[course]=title,headline,url,primary_category"
        token = b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
        try:
            data = fetch_json(api_url, headers={"Authorization": f"Basic {token}"})
        except Exception:
            return None
        imported_url = data.get("url") or f"https://www.udemy.com/course/{course_slug}/"
        return ConnectorResult(
            title=cleanup_title(data.get("title") or slug_to_title(course_slug))[:180],
            source=self.source,
            platform=self.platform,
            url=f"https://www.udemy.com{imported_url}" if imported_url.startswith("/") else imported_url,
            description=data.get("headline") or f"Udemy learning resource imported from {course_slug}",
            skills=(data.get("primary_category") or {}).get("title", ""),
        )

    def _title_from_path(self, url):
        slug = self._course_slug(url)
        return slug_to_title(slug) if slug else ""

    def _skills_from_slug(self, url):
        text = urlparse(url).path.lower().replace("-", " ")
        skill_map = {
            "python": "Python",
            "react": "React",
            "javascript": "JavaScript",
            "data": "Data analysis",
            "design": "Design",
            "marketing": "Marketing",
            "aws": "Cloud",
        }
        return ", ".join(label for key, label in skill_map.items() if key in text)


def slug_to_title(slug):
    return " ".join(word.capitalize() for word in slug.replace("-", " ").split())
