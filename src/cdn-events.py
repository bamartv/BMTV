from __future__ import annotations
import base64
import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import os

EVENTS_API = "https://api.cdnlivetv.is/api/v1/events/sports/?user=cdnlivetv&plan=free"
IPTV_CHANNELS_API = "https://iptv-org.github.io/api/channels.json"
IPTV_LOGOS_API = "https://iptv-org.github.io/api/logos.json"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data"

if os.environ.get("GITHUB_ACTIONS") == "true":
    OUTPUT_FILE = Path(__file__).resolve().parent.parent / "SPORTS.m3u"
else:
    OUTPUT_FILE = CACHE_DIR / ".SPORTS.m3u"
OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
MAX_WORKERS = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
    "Referer": "https://cdnlivetv.tv/",
}

STREAM_REFERER = "https://cdnlivetv.is/"
STREAM_UA      = HEADERS["User-Agent"]


def http_get(url: str, timeout: int = 15) -> Optional[str]:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def fetch_all_channels() -> tuple[list[dict], dict]:
    body = http_get(EVENTS_API)
    if not body:
        return [], {}
    try:
        raw = json.loads(body)
        data = raw.get("cdn-live-tv", {})
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON parse: {e}", file=sys.stderr)
        return [], {}

    channels = {}
    for _category, events in data.items():
        if not isinstance(events, list):
            continue
        for event in events:
            for ch in event.get("channels", []):
                name = ch.get("channel_name", "").strip()
                if not name:
                    continue
                key = name.lower()
                if key not in channels:
                    channels[key] = {
                        "name":  name,
                        "url":   ch.get("url", ""),
                    }
    return sorted(list(channels.values()), key=lambda x: x["name"]), data


def _b64decode(s: str) -> str:
    s = s.replace("-", "+").replace("_", "/")
    while len(s) % 4:
        s += "="
    try:
        return base64.b64decode(s).decode("utf-8", errors="replace")
    except Exception:
        return ""


def extract_m3u8(html: str) -> Optional[str]:
    decoded: set[str] = set()
    for b64_val in re.findall(r"var\s+\w+\s*=\s*'([A-Za-z0-9+/=_-]+)'", html):
        v = _b64decode(b64_val)
        if v:
            decoded.add(v)

    channel_id = next((v for v in decoded if re.fullmatch(r"[0-9a-f]{24}", v)), None)
    token_qs   = next((v for v in decoded if v.startswith("?token=")), None)

    if channel_id and token_qs:
        return f"https://cdnlivetv.is/secure/api/v1/{channel_id}/playlist.m3u8{token_qs}"

    m = re.search(r"https://[^\s\"']+playlist\.m3u8[^\s\"']*", html)
    return m.group() if m else None


def resolve_channel(ch: dict) -> tuple[dict, Optional[str]]:
    html = http_get(ch["url"])
    m3u8_url = extract_m3u8(html) if html else None
    return ch, m3u8_url


def _normalize(name: str) -> str:
    s = re.sub(r'\b(HD|FHD|UHD|4K|HDR|US|UK|RO|ES|FR|IT|DE|IN|CA|AU|HDTV|FHD)\b', '', name, flags=re.IGNORECASE)
    s = re.sub(r'\[.*?\]', '', s)
    s = re.sub(r'\(.*?\)', '', s)
    s = re.sub(r'[^a-zA-Z0-9 ]', '', s)
    s = re.sub(r'\s+', ' ', s)
    return s.lower().strip()


def build_logo_index() -> dict[str, str]:
    """Build a name → logo_url lookup from iptv-org database."""
    cache_file = CACHE_DIR / ".iptv_cache.json"
    now = 0
    try:
        now = int(__import__("time").time())
    except Exception:
        pass

    channels_data = None
    logos_data = None

    # Try loading cached data (max 24h old)
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text("utf-8"))
            if now - cached.get("ts", 0) < 86400:
                channels_data = cached.get("channels")
                logos_data = cached.get("logos")
                print("[*] Using cached iptv-org data")
        except Exception:
            pass

    if channels_data is None:
        print("[*] Downloading iptv-org channels...")
        body = http_get(IPTV_CHANNELS_API, timeout=30)
        channels_data = json.loads(body) if body else []
    if logos_data is None:
        print("[*] Downloading iptv-org logos...")
        body = http_get(IPTV_LOGOS_API, timeout=30)
        logos_data = json.loads(body) if body else []

    # Build channel_id → logo_url
    logo_by_channel: dict[str, str] = {}
    for entry in logos_data or []:
        url = entry.get("url", "")
        if url:
            logo_by_channel[entry["channel"]] = url

    # Build normalized name → logo_url
    index: dict[str, str] = {}
    for ch in channels_data or []:
        cid = ch.get("id", "")
        if cid in logo_by_channel:
            names = [ch.get("name", "")]
            names.extend(ch.get("alt_names") or [])
            for n in names:
                key = _normalize(n)
                if key and key not in index:
                    index[key] = logo_by_channel[cid]

    # Cache for next run
    try:
        cache_file.write_text(json.dumps({
            "ts": now,
            "channels": channels_data,
            "logos": logos_data,
        }), "utf-8")
    except Exception:
        pass

    print(f"[*] Loaded {len(index)} channel names with logos")
    return index


def find_logo(name: str, logo_index: dict[str, str]) -> str:
    base = _normalize(name)
    if not base:
        return ""

    # 1. exact normalized match
    if base in logo_index:
        return logo_index[base]

    parts = base.split()

    # 2. try without trailing words (progressive)
    for i in range(len(parts) - 1, 0, -1):
        key = " ".join(parts[:i])
        if key in logo_index:
            return logo_index[key]

    # 3. try first word only
    if len(parts) > 1 and parts[0] in logo_index:
        return logo_index[parts[0]]

    # 4. try first two words
    if len(parts) > 2:
        key = " ".join(parts[:2])
        if key in logo_index:
            return logo_index[key]

    return ""


def main() -> None:
    print("[*] Fetching active channels...")
    channels, events_data = fetch_all_channels()
    if not channels:
        print("[ERROR] No channels found.", file=sys.stderr)
        sys.exit(1)

    events_file = CACHE_DIR / ".SPORTS_EVENT.json"
    try:
        simplified = {}
        for category, events in events_data.items():
            if not isinstance(events, list):
                continue
            simplified[category] = []
            for ev in events:
                ch_names = [ch.get("channel_name", "").strip() for ch in ev.get("channels", []) if ch.get("channel_name")]
                if ch_names:
                    simplified[category].append({
                        "event": ev.get("event", "").strip() or f"{ev.get('homeTeam','')} vs {ev.get('awayTeam','')}",
                        "tournament": ev.get("tournament", ""),
                        "time": ev.get("time", ""),
                        "start": ev.get("start", ""),
                        "end": ev.get("end", ""),
                        "status": ev.get("status", ""),
                        "channels": ch_names,
                    })
        events_file.write_text(json.dumps(simplified, indent=2), "utf-8")
        print(f"[*] Saved events data → {events_file.name}")
    except Exception as e:
        print(f"[WARN] Failed to save events: {e}", file=sys.stderr)

    print("[*] Loading logo database...")
    logo_index = build_logo_index()

    total = len(channels)
    print(f"[*] Found {total} unique channel(s). Resolving stream URLs in parallel (workers={MAX_WORKERS})...")

    resolved_channels = []
    success_count = 0
    logo_found = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(resolve_channel, ch): ch for ch in channels}
        for idx, future in enumerate(as_completed(futures), 1):
            try:
                ch, m3u8_url = future.result()
                status = "OK" if m3u8_url else "FAILED"
                print(f"[{idx}/{total}] Resolving: {ch['name']} ... {status}")
                if m3u8_url:
                    logo_url = find_logo(ch["name"], logo_index)
                    if logo_url:
                        ch["logo"] = logo_url
                        logo_found += 1
                    resolved_channels.append((ch, m3u8_url))
                    success_count += 1
            except Exception as e:
                ch = futures[future]
                print(f"[{idx}/{total}] Resolving: {ch['name']} ... ERROR: {e}")

    resolved_channels.sort(key=lambda x: x[0]["name"])
    print(f"[*] Logos found: {logo_found}/{success_count}")

    playlist_lines = ["#EXTM3U\n"]
    for ch, m3u8_url in resolved_channels:
        logo_attr = f' tvg-logo="{ch["logo"]}"' if ch.get("logo") else ""
        playlist_lines.append(
            f'#EXTINF:-1 tvg-name="{ch["name"]}"{logo_attr} group-title="Sports",{ch["name"]}'
        )
        playlist_lines.append(f"#EXTVLCOPT:http-user-agent={STREAM_UA}")
        playlist_lines.append(f"#EXTVLCOPT:http-referrer={STREAM_REFERER}")
        playlist_lines.append(f"{m3u8_url}\n")

    OUTPUT_FILE.write_text("\n".join(playlist_lines) + "\n", encoding="utf-8")
    print(f"[*] Done. Generated playlist with {success_count}/{total} channels → {OUTPUT_FILE.name}")


if __name__ == "__main__":
    main()
