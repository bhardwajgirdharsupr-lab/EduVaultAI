import re
from urllib.parse import urlparse

from .base import BaseConnector, ConnectorResult, cleanup_title, fetch_page_title, strip_suffixes


class CourseraConnector(BaseConnector):
    source = "coursera"
    platform = "Coursera"
    host_tokens = ("coursera.org",)

    def import_url(self, url):
        title, warning = fetch_page_title(url)
        course_name = self._title_from_path(url) or strip_suffixes(
            title,
            ("| Coursera", "- Coursera", " | Coursera"),
        )
        if not course_name or course_name == url:
            course_name = "Coursera course"

        description = f"Coursera learning resource imported from {url}"
        skills = self._skills_from_slug(url)
        return ConnectorResult(
            title=cleanup_title(course_name)[:180],
            source=self.source,
            platform=self.platform,
            url=url,
            description=description,
            skills=skills,
            warning=warning,
        )

    def _title_from_path(self, url):
        path = urlparse(url).path.strip("/")
        match = re.search(r"(?:learn|professional-certificates|specializations|projects)/([^/?#]+)", path)
        if not match:
            return ""
        return slug_to_title(match.group(1))

    def _skills_from_slug(self, url):
        words = set(urlparse(url).path.lower().replace("-", " ").split("/"))
        skill_map = {
            "python": "Python",
            "data": "Data analysis",
            "analytics": "Analytics",
            "machine": "Machine learning",
            "sql": "SQL",
            "excel": "Spreadsheets",
            "google": "Google tools",
            "project": "Project work",
        }
        return ", ".join(label for key, label in skill_map.items() if key in " ".join(words))


def slug_to_title(slug):
    return " ".join(word.capitalize() for word in slug.replace("-", " ").split())
