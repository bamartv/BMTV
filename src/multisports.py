import os
import urllib.request
from urllib.parse import quote

# ================= CONFIG =================

SOURCE_URL = os.environ.get("MULTISPORT_URL")
OUTPUT_FILE = "multisports.m3u"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/144.0.0.0 Safari/537.36"
)

NEW_EPG = "https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"

# =========================================


def fetch_playlist(url: str) -> list[str]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": DEFAULT_USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="ignore").splitlines()


def main():
    if not SOURCE_URL:
        raise RuntimeError("MULTISPORT_URL secret is missing")

    lines = fetch_playlist(SOURCE_URL)

    output = [f'#EXTM3U url-tvg="{NEW_EPG}"']

    current_extinf = None
    referrer = None
    origin = None
    user_agent = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith("#EXTM3U"):
            continue

        # EXTINF
        if line.startswith("#EXTINF"):
            current_extinf = line
            referrer = None
            origin = None
            user_agent = None
            output.append(line)
            continue

        # VLC headers
        if line.startswith("#EXTVLCOPT:http-referrer="):
            referrer = line.split("=", 1)[1].strip()
            continue

        if line.startswith("#EXTVLCOPT:http-origin="):
            origin = line.split("=", 1)[1].strip()
            continue

        if line.startswith("#EXTVLCOPT:http-user-agent="):
            ua_raw = line.split("=", 1)[1].strip()
            user_agent = quote(ua_raw, safe="")
            continue

        # Stream URL
        if line.startswith("http") and current_extinf:
            base_url = line.split("|", 1)[0]

            headers = []
            if referrer:
                headers.append(f"referer={referrer}")
            if origin:
                headers.append(f"origin={origin}")
            if user_agent:
                headers.append(f"user-agent={user_agent}")

            if headers:
                output.append(base_url + "|" + "|".join(headers))
            else:
                output.append(base_url)

            current_extinf = None
            continue

        if line.startswith("#"):
            output.append(line)

    if len(output) <= 1:
        raise RuntimeError("Output playlist is empty")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(output))

    print(f"Saved {OUTPUT_FILE} ({len(output)} lines)")


if __name__ == "__main__":
    main()
