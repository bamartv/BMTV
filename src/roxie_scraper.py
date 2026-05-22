#!/usr/bin/env python3
"""
RoxieStreams scraper.

Collects event/page metadata plus stream links, then writes:
  - roxie_streams.json
  - roxie_streams.m3u8

The playlists include Referer/User-Agent hints for players that support them.
Browser CORS still needs a server-side proxy; it cannot be fixed by a static
M3U8 file because browsers control the Referer header.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://roxiestreams.su"
OUTPUT_FILES = {
    "json": Path("roxie_streams.json"),
    "m3u8": Path("roxie_streams.m3u8"),
}

KNOWN_STREAM_PAGES = {
    "soccer": ["/soccer", "/soccer-streams-1"],
    "mlb": ["/mlb"],
    "nba": ["/nba", "/nba-streams-1"],
    "nfl": ["/nfl"],
    "nhl": ["/nhl", "/nhl-streams-1"],
    "fighting": ["/fighting"],
    "motorsports": ["/motorsports"],
    "aew": ["/aew"],
}

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_URL + "/",
}

STREAM_EXTENSIONS = ("m3u8", "mpd")
GENERIC_LABELS = {
    "roxiestreams",
    "soccer",
    "mlb",
    "nba",
    "nfl",
    "nhl",
    "fighting",
    "motorsports",
    "stream request (discord)",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("roxie")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def absolute_url(href: str, base: str = BASE_URL) -> str:
    return urljoin(base, href.strip())


def same_site(url: str) -> bool:
    return urlparse(url).netloc == urlparse(BASE_URL).netloc


def stream_type(url: str) -> str:
    lowered = url.lower()
    if ".mpd" in lowered:
        return "mpd"
    if ".m3u8" in lowered:
        return "m3u8"
    return "unknown"


def fetch(session: requests.Session, url: str, retries: int = 3, delay: float = 2.0) -> str | None:
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, timeout=20)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or response.encoding
            log.info("[%s] %s", response.status_code, url)
            return response.text
        except requests.RequestException as exc:
            log.warning("Attempt %s/%s failed for %s: %s", attempt, retries, url, exc)
            if attempt < retries:
                time.sleep(delay)
    return None


def extract_domains(text: str | None) -> list[str]:
    if not text:
        return []

    domains: list[str] = []
    for line in text.splitlines():
        line = clean_text(line)
        if not line or line.startswith("<") or "---" in line:
            continue
        if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}", line):
            domains.append(line.lower())
    return sorted(set(domains))


def build_stream_headers(source_page: str) -> dict[str, str]:
    return {
        "Referer": source_page or BASE_URL + "/",
        "Origin": BASE_URL,
        "User-Agent": BASE_HEADERS["User-Agent"],
        "Accept": "*/*",
    }


def build_stream_url(subdomain: str, path: str, domain: str) -> str:
    path = path.lstrip("/")
    return f"https://{subdomain}.{domain}/{path}"


def add_stream(streams: list[dict[str, Any]], seen: set[str], stream: dict[str, Any]) -> None:
    url = stream.get("url", "")
    if not url or url in seen:
        return
    seen.add(url)
    streams.append(stream)


def normalize_page_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(path=parsed.path.rstrip("/") or "/", query="", fragment="").geturl()


def is_event_label(label: str, sport: str = "") -> bool:
    normalized = clean_text(label).lower()
    if not normalized or normalized in GENERIC_LABELS:
        return False
    if sport and normalized == sport.lower():
        return False
    if normalized.startswith("stream ") or "discord" in normalized:
        return False
    return len(normalized) > 2


def split_matchup(event_title: str) -> dict[str, str] | None:
    parts = re.split(r"\s+(?:vs\.?|v\.?|versus|against|at)\s+", event_title, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2:
        return None

    home = clean_text(parts[0])
    away = clean_text(parts[1])
    if not home or not away:
        return None

    return {"home": home, "away": away}


def build_display_name(event_title: str, stream_label: str) -> str:
    event_title = clean_text(event_title)
    stream_label = clean_text(stream_label)
    if not event_title:
        return stream_label
    if not stream_label or stream_label.lower() in {"direct link", event_title.lower()}:
        return event_title
    return f"{event_title} - {stream_label}"


def discover_stream_pages(homepage_html: str) -> dict[str, list[str]]:
    pages = {sport: list(paths) for sport, paths in KNOWN_STREAM_PAGES.items()}
    soup = BeautifulSoup(homepage_html, "html.parser")

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue

        full_url = absolute_url(href)
        if not same_site(full_url):
            continue

        path = urlparse(full_url).path.rstrip("/") or "/"
        lowered = path.lower()
        if lowered == "/":
            continue

        for sport in pages:
            if sport in lowered:
                pages[sport].append(path)
                break
        else:
            if "stream" in lowered:
                slug = lowered.strip("/").split("-")[0] or "other"
                pages.setdefault(slug, []).append(path)
            else:
                slug = lowered.strip("/").split("/")[0] or "other"
                pages.setdefault(slug, []).append(path)

    return {sport: sorted(set(paths)) for sport, paths in pages.items()}


def extract_events(homepage_html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(homepage_html, "html.parser")
    events: list[dict[str, str]] = []

    table = soup.find("table", id="eventsTable")
    if table:
        for row in table.select("tbody tr"):
            cells = row.find_all("td")
            if not cells:
                continue

            link = cells[0].find("a", href=True)
            name = clean_text(link.get_text(" ", strip=True) if link else cells[0].get_text(" ", strip=True))
            page = absolute_url(link["href"]) if link else ""
            start_time = clean_text(cells[1].get_text(" ", strip=True) if len(cells) > 1 else "")

            if name:
                events.append({"name": name, "page": page, "start_time": start_time})

    if events:
        return events

    for anchor in soup.find_all("a", href=True):
        name = clean_text(anchor.get_text(" ", strip=True))
        href = anchor["href"].strip()
        if name and "stream" in href.lower():
            events.append({"name": name, "page": absolute_url(href), "start_time": ""})

    return events


def extract_page_metadata(url: str, html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    title = clean_text(soup.title.string if soup.title else "")
    headings = [clean_text(h.get_text(" ", strip=True)) for h in soup.find_all(["h1", "h2", "h3"])]
    buttons = [clean_text(btn.get_text(" ", strip=True)) for btn in soup.find_all(["button", "a"])]

    links = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        label = clean_text(anchor.get_text(" ", strip=True))
        if href:
            links.append({"label": label, "url": absolute_url(href, url)})

    return {
        "url": url,
        "title": title,
        "headings": [item for item in headings if item],
        "controls": [item for item in buttons if item],
        "links": links,
    }


def extract_direct_stream_urls(html: str) -> list[str]:
    pattern = re.compile(r"https?://[^\s\"'<>]+\.(?:m3u8|mpd)(?:\?[^\s\"'<>]*)?", re.IGNORECASE)
    urls = []
    for match in pattern.finditer(html):
        urls.append(match.group(0).rstrip("'\"\\);,"))
    return urls


def extract_random_stream_calls(html: str) -> list[tuple[str, str]]:
    pattern = re.compile(
        r"getRandomStream\s*\(\s*['\"]([^'\"]+)['\"](?:\s*,\s*['\"]([^'\"]+)['\"])?\s*\)",
        re.IGNORECASE,
    )
    return [(m.group(1), m.group(2) or "daffodil") for m in pattern.finditer(html)]


def extract_button_labels(html: str) -> dict[tuple[str, str], str]:
    labels: dict[tuple[str, str], str] = {}
    call_pattern = re.compile(
        r"getRandomStream\s*\(\s*['\"]([^'\"]+)['\"](?:\s*,\s*['\"]([^'\"]+)['\"])?\s*\)",
        re.IGNORECASE,
    )
    soup = BeautifulSoup(html, "html.parser")

    for element in soup.find_all(["button", "a"]):
        text = " ".join(
            value
            for value in [
                element.get("onclick", ""),
                element.get("href", ""),
                element.get("data-url", ""),
                element.get("data-src", ""),
            ]
            if value
        )
        if "getRandomStream" not in text:
            continue

        label = clean_text(element.get_text(" ", strip=True))
        for match in call_pattern.finditer(text):
            path = match.group(1)
            subdomain = match.group(2) or "daffodil"
            if label:
                labels[(path, subdomain)] = label

    return labels


def discover_related_paths(metadata: dict[str, Any], sport: str) -> list[str]:
    paths: list[str] = []
    for link in metadata.get("links", []):
        url = link.get("url", "")
        if not url or not same_site(url):
            continue

        path = urlparse(url).path.rstrip("/") or "/"
        lowered = path.lower()
        if lowered == "/":
            continue
        if sport.lower() in lowered or "stream" in lowered:
            paths.append(path)

    return sorted(set(paths))


def extract_event_titles_from_links(metadata: dict[str, Any], sport: str) -> dict[str, str]:
    titles: dict[str, str] = {}
    for link in metadata.get("links", []):
        label = clean_text(link.get("label", ""))
        url = link.get("url", "")
        if not url or not same_site(url) or not is_event_label(label, sport):
            continue

        path = urlparse(url).path.lower()
        if "stream" not in path and sport.lower() not in path:
            continue

        titles[normalize_page_url(url)] = label

    return titles


def enrich_stream(stream: dict[str, Any], sport: str, event_title: str) -> dict[str, Any]:
    event_title = clean_text(event_title)
    stream_label = clean_text(stream.get("label", ""))
    matchup = split_matchup(event_title)

    enriched = dict(stream)
    enriched["sport"] = sport
    enriched["event_title"] = event_title or stream_label or sport.upper()
    enriched["matchup"] = matchup
    enriched["display_name"] = build_display_name(enriched["event_title"], stream_label)
    return enriched


def build_events_by_sport(sports: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}

    for sport, sport_data in sports.items():
        events: dict[str, dict[str, Any]] = {}
        for stream in sport_data.get("streams", []):
            event_title = stream.get("event_title") or sport.upper()
            event = events.setdefault(
                event_title,
                {
                    "title": event_title,
                    "sport": sport,
                    "matchup": stream.get("matchup"),
                    "page": stream.get("source_page", ""),
                    "streams": [],
                },
            )
            event["streams"].append(stream)

        grouped[sport] = list(events.values())

    return grouped


def extract_drm_keys(html: str) -> dict[str, str]:
    """Read ClearKey data if the page exposes it in plain JavaScript."""
    keys: dict[str, str] = {}
    object_pattern = re.compile(r"(?:const|let|var)\s+dashKeys\s*=\s*(\{.*?\})", re.IGNORECASE | re.DOTALL)
    kv_pattern = re.compile(r"['\"]([a-fA-F0-9]{16,})['\"]\s*:\s*['\"]([a-fA-F0-9]{16,})['\"]")
    for obj in object_pattern.finditer(html):
        for key_match in kv_pattern.finditer(obj.group(1)):
            keys[key_match.group(1)] = key_match.group(2)
    return keys


def extract_streams_from_page(url: str, html: str, domains: list[str]) -> list[dict[str, Any]]:
    streams: list[dict[str, Any]] = []
    seen: set[str] = set()
    labels = extract_button_labels(html)
    drm_keys = extract_drm_keys(html)

    for direct_url in extract_direct_stream_urls(html):
        media_type = stream_type(direct_url)
        stream = {
            "label": "Direct Link",
            "url": direct_url,
            "type": media_type,
            "source_page": url,
            "headers": build_stream_headers(url),
            "drm_keys": drm_keys if media_type == "mpd" and drm_keys else None,
        }
        add_stream(streams, seen, stream)

    for path, subdomain in extract_random_stream_calls(html):
        label = labels.get((path, subdomain), path)
        for domain in domains:
            built_url = build_stream_url(subdomain, path, domain)
            stream = {
                "label": label,
                "url": built_url,
                "type": stream_type(built_url),
                "source_page": url,
                "cdn_domain": domain,
                "subdomain": subdomain,
                "path": path,
                "headers": build_stream_headers(url),
                "drm_keys": None,
            }
            add_stream(streams, seen, stream)

    log.info("Extracted %s stream(s) from %s", len(streams), url)
    return streams


def scrape() -> dict[str, Any]:
    session = requests.Session()
    session.headers.update(BASE_HEADERS)

    result: dict[str, Any] = {
        "scraped_at": now_iso(),
        "base_url": BASE_URL,
        "domains": [],
        "events": [],
        "events_by_sport": {},
        "pages": {},
        "sports": {},
        "stream_count": 0,
        "notes": [
            "Playlist header tags are player hints. Browser CORS requires a server-side proxy.",
        ],
    }

    log.info("Fetching homepage")
    homepage_html = fetch(session, BASE_URL)
    if not homepage_html:
        log.error("Could not fetch homepage")
        return result

    result["events"] = extract_events(homepage_html)
    page_event_titles = {
        normalize_page_url(event["page"]): event["name"]
        for event in result["events"]
        if event.get("page") and event.get("name")
    }
    pages_by_sport = discover_stream_pages(homepage_html)

    log.info("Fetching CDN domain list")
    domains = extract_domains(fetch(session, f"{BASE_URL}/domainsz21.txt"))
    if "shadow-ran.online" not in domains:
        domains.append("shadow-ran.online")
    result["domains"] = sorted(set(domains))

    for sport, paths in pages_by_sport.items():
        log.info("Sport: %s", sport.upper())
        result["sports"][sport] = {"pages": [], "streams": []}
        seen_stream_urls: set[str] = set()
        seen_page_paths: set[str] = set()
        index = 0

        while index < len(paths):
            path = paths[index]
            index += 1
            if path in seen_page_paths:
                continue
            seen_page_paths.add(path)

            page_url = absolute_url(path)
            html = fetch(session, page_url)
            if not html:
                continue

            result["sports"][sport]["pages"].append(page_url)
            metadata = extract_page_metadata(page_url, html)
            result["pages"][page_url] = metadata
            page_event_titles.update(extract_event_titles_from_links(metadata, sport))

            for related_path in discover_related_paths(metadata, sport):
                if related_path not in seen_page_paths and related_path not in paths:
                    paths.append(related_path)

            for stream in extract_streams_from_page(page_url, html, result["domains"]):
                if stream["url"] not in seen_stream_urls:
                    seen_stream_urls.add(stream["url"])
                    event_title = page_event_titles.get(normalize_page_url(page_url), "")
                    result["sports"][sport]["streams"].append(enrich_stream(stream, sport, event_title))

        log.info("%s total stream(s) for %s", len(result["sports"][sport]["streams"]), sport)

    result["events_by_sport"] = build_events_by_sport(result["sports"])
    result["stream_count"] = sum(len(sport_data.get("streams", [])) for sport_data in result["sports"].values())

    return result


def m3u_attr(value: str) -> str:
    return value.replace('"', "'")


def build_m3u8_playlist(data: dict[str, Any]) -> str:
    lines = [
        "#EXTM3U",
        f"# RoxieStreams Playlist - generated {data.get('scraped_at', '')}",
        f"# Source: {data.get('base_url', BASE_URL)}",
        "",
    ]

    for sport, sport_data in data.get("sports", {}).items():
        for stream in sport_data.get("streams", []):
            if stream.get("type") != "m3u8":
                continue

            label = stream.get("display_name") or stream.get("event_title") or stream.get("label") or sport.upper()
            url = stream.get("url", "")
            source_page = stream.get("source_page") or stream.get("source") or BASE_URL + "/"
            headers = stream.get("headers") or build_stream_headers(source_page)
            event_title = stream.get("event_title") or sport.upper()
            group = f"{sport.upper()} - {event_title}" if event_title else sport.upper()
            tvg_name = m3u_attr(label)

            lines.append(f'#EXTINF:-1 tvg-name="{tvg_name}" group-title="{group}",{label}')
            lines.append(f"#EXTGRP:{group}")
            lines.append(f"#EXTDESC:{event_title}")
            lines.append(f"#EXTVLCOPT:http-referrer={headers.get('Referer', source_page)}")
            lines.append(f"#EXTVLCOPT:http-user-agent={headers.get('User-Agent', BASE_HEADERS['User-Agent'])}")
            kodi_headers = urlencode(
                {
                    "Referer": headers.get("Referer", source_page),
                    "User-Agent": headers.get("User-Agent", BASE_HEADERS["User-Agent"]),
                },
                quote_via=quote,
            )
            lines.append(f"#KODIPROP:inputstream.adaptive.stream_headers={kodi_headers}")
            lines.append(
                "#EXTHTTP:"
                + json.dumps(
                    {
                        "Referer": headers.get("Referer", source_page),
                        "Origin": headers.get("Origin", BASE_URL),
                        "User-Agent": headers.get("User-Agent", BASE_HEADERS["User-Agent"]),
                    },
                    separators=(",", ":"),
                )
            )
            lines.append(url)
            lines.append("")

    return "\n".join(lines)


def save_outputs(data: dict[str, Any]) -> None:
    json_path = OUTPUT_FILES["json"]
    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("JSON saved -> %s", json_path)

    m3u8_path = OUTPUT_FILES["m3u8"]
    m3u8_path.write_text(build_m3u8_playlist(data), encoding="utf-8")
    log.info("M3U8 saved -> %s", m3u8_path)

def main() -> None:
    print("=" * 60)
    print("  RoxieStreams Scraper")
    print("  Extracting streams -> JSON + M3U8")
    print("=" * 60)

    data = scrape()
    save_outputs(data)

    total = sum(len(sport_data.get("streams", [])) for sport_data in data.get("sports", {}).values())
    print("\nDone")
    print(f"   Events found: {len(data.get('events', []))}")
    print(f"   Total streams: {total}")
    print(f"   Output folder: {Path.cwd().resolve()}")
    print("   Files: roxie_streams.json | roxie_streams.m3u8")


if __name__ == "__main__":
    main()
