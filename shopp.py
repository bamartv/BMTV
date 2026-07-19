#!/usr/bin/env python3

from utils import Cache, Time, get_logger, leagues, network
from datetime import datetime
from urllib.parse import quote, urljoin
import re
import asyncio
import json
from playwright.async_api import async_playwright

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "SHOPP"

CACHE_FILE = Cache(TAG, exp=28_800)

BASE_URL = "https://xyzstreams.st/"
MAIN_URL = "https://xyzstreams.st/"
API_BASE = "https://xyzstreams.st/api/get-stream"

# Output files
VLC_OUTPUT = "shopp_vlc.m3u8"
TIVIMATE_OUTPUT = "shopp_tivimate.m3u8"

# Headers for streams
REFERER = "https://xyzstreams.st/"
ORIGIN = "https://xyzstreams.st"

# Use mobile user agent for better compatibility
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; K) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Mobile Safari/537.36"
)

TIVIMATE_USER_AGENT = USER_AGENT

# Browser headers for scraping
SCRAPE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Host": "xyzstreams.st",
    "Referer": "https://xyzstreams.st/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": USER_AGENT,
}


async def get_main_page_playwright() -> str | None:
    """Fetch main page using Playwright for JavaScript rendering"""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled']
            )
            
            context = await browser.new_context(
                user_agent=USER_AGENT,
                viewport={'width': 390, 'height': 844}
            )
            
            page = await context.new_page()
            
            # Use domcontentloaded instead of networkidle for faster loading
            await page.goto(
                MAIN_URL,
                wait_until="domcontentloaded",
                timeout=30000
            )
            
            # Wait for event cards to load
            try:
                await page.wait_for_selector('.event-card', timeout=10000)
            except:
                pass
            
            html = await page.content()
            
            await browser.close()
            
            return html
            
    except Exception as e:
        log.error(f"Playwright main page error: {e}")
        return None


async def get_events_from_main_page() -> list[dict]:
    """Scrape the main page for all event cards using robust parsing"""
    events = []
    seen_events = set()  # Track unique events by title + href
    
    try:
        # Try Playwright first for JavaScript-rendered content
        html_content = await get_main_page_playwright()
        
        # Fallback to network module if Playwright fails
        if not html_content:
            log.warning("Playwright failed, falling back to network module")
            if not (r := await network.request(MAIN_URL, headers=SCRAPE_HEADERS, log=log)):
                return events
            html_content = r.text
        
        # Debug: log content info
        log.info(f"Downloaded {len(html_content)} bytes")
        
        # Find ALL event card blocks - simple pattern that works regardless of attribute order
        event_blocks = re.findall(
            r'<a\b[^>]*class=["\'][^"\']*event-card[^"\']*["\'][^>]*>.*?</a>',
            html_content,
            re.I | re.S
        )
        
        log.info(f"Found {len(event_blocks)} event blocks")
        
        for block in event_blocks:
            # Extract href - works regardless of attribute order
            href_match = re.search(
                r'href=["\']([^"\']+)["\']',
                block,
                re.I
            )
            
            # Extract title from h3 tag
            title_match = re.search(
                r'<h3[^>]*>(.*?)</h3>',
                block,
                re.I | re.S
            )
            
            if not href_match or not title_match:
                continue
            
            href = href_match.group(1).strip()
            
            # Clean title from HTML tags
            title = re.sub(
                r'<[^>]+>',
                '',
                title_match.group(1)
            ).strip()
            
            if not title:
                continue
            
            # Create unique key from title + href to handle duplicate hrefs
            event_key = f"{title}|{href}"
            
            # Skip duplicate events
            if event_key in seen_events:
                continue
            seen_events.add(event_key)
            
            full_url = urljoin(BASE_URL, href)
            
            events.append({
                "name": title,
                "url": full_url,
                "sport": "Live Event"
            })
            
        log.info(f"Found {len(events)} unique events on main page")
        return events
        
    except Exception as e:
        log.error(f"Main page scrape error: {e}")
        return []


async def extract_streams_from_event_page(event_url: str, event_name: str) -> list[str]:
    """Extract m3u8 stream URLs using Playwright for JavaScript execution"""
    found_streams = set()
    captured_api_urls = set()
    
    try:
        async with async_playwright() as p:
            # Launch browser with mobile viewport
            browser = await p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled']
            )
            
            # Create context with mobile user agent
            context = await browser.new_context(
                user_agent=USER_AGENT,
                viewport={'width': 390, 'height': 844},
                device_scale_factor=2
            )
            
            page = await context.new_page()
            
            # Capture network requests
            def capture_request(req):
                url = req.url
                if '.m3u8' in url:
                    found_streams.add(url)
                if '/api/get-stream' in url:
                    captured_api_urls.add(url)
                    
            page.on('request', capture_request)
            
            # Also capture responses with async handler
            async def capture_response(resp):
                try:
                    url = resp.url
                    
                    # Direct m3u8 requests
                    if '.m3u8' in url:
                        found_streams.add(url)
                    
                    # Token API responses
                    if '/api/get-stream' in url:
                        try:
                            data = await resp.json()
                            stream_url = data.get('url')
                            
                            if stream_url and '.m3u8' in stream_url:
                                found_streams.add(stream_url)
                                log.info(f"Captured tokenized stream: {stream_url}")
                        except Exception:
                            pass
                except Exception:
                    pass
                    
            page.on(
                "response",
                lambda resp: asyncio.create_task(capture_response(resp))
            )
            
            # Navigate to event page with shorter timeout and domcontentloaded
            try:
                await page.goto(
                    event_url,
                    wait_until='domcontentloaded',
                    timeout=30000
                )
                
                # Wait for potential stream content to load
                await page.wait_for_timeout(5000)
                
                # Try to trigger stream loading by selecting first feed option
                try:
                    select_elements = await page.locator("select").all()
                    for select in select_elements:
                        try:
                            options = await select.locator("option").all()
                            if len(options) > 1:
                                await select.select_option(index=1)
                                await page.wait_for_timeout(3000)
                                break
                        except Exception:
                            continue
                except Exception:
                    pass
                
                # Try to click buttons that might load streams
                try:
                    buttons = await page.locator("button").all()
                    for btn in buttons:
                        try:
                            btn_text = await btn.text_content()
                            if btn_text and any(keyword in btn_text.lower() for keyword in ['play', 'stream', 'watch', 'load']):
                                await btn.click(timeout=1000)
                                await page.wait_for_timeout(1500)
                        except Exception:
                            continue
                except Exception:
                    pass
                
                # Wait for API calls to complete
                await page.wait_for_timeout(3000)
                
                # Get page content for additional extraction
                html_content = await page.content()
                
                # Extract channel names from HTML for API calls
                api_channels = set()
                
                # Find channel names in various contexts
                api_channels.update(
                    re.findall(
                        r'channel=([A-Za-z0-9_-]+)',
                        html_content
                    )
                )
                
                api_channels.update(
                    re.findall(
                        r'/api/get-stream\?channel=([A-Za-z0-9_-]+)',
                        html_content
                    )
                )
                
                # Also extract from select options and button texts
                channel_names = re.findall(
                    r'([A-Z]{2,}(?:\s*[A-Z0-9]+)?)',
                    html_content
                )
                
                # Common channel names to try
                common_channels = ['FOX', 'fox4k', 'ITV', 'tsn4k', 'dsports1arg',  'BBC', 'TSN', 'Telemundo', 'ESPN', 'TNT', 'NBC', 'CBS', 'ABC']
                for channel in common_channels:
                    if channel in html_content:
                        api_channels.add(channel)
                
                # Query the API directly for each channel
                for channel in api_channels:
                    try:
                        api_url = f"{API_BASE}?channel={channel}"
                        
                        response = await page.request.get(
                            api_url,
                            headers={
                                "Referer": REFERER,
                                "Origin": ORIGIN,
                                "User-Agent": USER_AGENT,
                            }
                        )
                        
                        if response.ok:
                            data = await response.json()
                            stream_url = data.get('url')
                            
                            if stream_url and '.m3u8' in stream_url:
                                found_streams.add(stream_url)
                                log.info(f"API stream found: {channel} -> {stream_url}")
                    except Exception as e:
                        log.debug(f"API lookup failed for {channel}: {e}")
                
                # Process any captured API URLs from network requests
                for api_url in captured_api_urls:
                    try:
                        response = await page.request.get(
                            api_url,
                            headers={
                                "Referer": REFERER,
                                "Origin": ORIGIN,
                                "User-Agent": USER_AGENT,
                            }
                        )
                        
                        if response.ok:
                            data = await response.json()
                            stream_url = data.get('url')
                            
                            if stream_url and '.m3u8' in stream_url:
                                found_streams.add(stream_url)
                                log.info(f"Captured API URL stream: {stream_url}")
                    except Exception:
                        pass
                
                # Extract m3u8 URLs from HTML - keep full URLs with query strings
                hls_patterns = re.findall(
                    r'https?://[^\s"\']+\.m3u8(?:\?[^\s"\']*)?',
                    html_content,
                    re.I
                )
                for url in hls_patterns:
                    found_streams.add(url)
                
                # Try to find stream URLs in JavaScript variables
                js_patterns = re.findall(
                    r'["\'](https?://streamxyz\.shop/[^"\']+\.m3u8(?:\?[^"\']*)?)["\']',
                    html_content,
                    re.I
                )
                for url in js_patterns:
                    found_streams.add(url)
                
                # Extract from player configurations
                player_patterns = re.findall(
                    r'(?:src|source|url|stream)\s*[:=]\s*["\'](https?://[^"\']+\.m3u8(?:\?[^"\']*)?)["\']',
                    html_content,
                    re.I
                )
                for url in player_patterns:
                    found_streams.add(url)
                
            except Exception as e:
                log.error(f"Error loading {event_name}: {e}")
                
            await browser.close()
            
    except Exception as e:
        log.error(f"Playwright error for {event_name}: {e}")
        
    # Convert to list and sort, preferring tokenized URLs
    streams = sorted(found_streams)
    
    # Filter out non-tokenized URLs if tokenized versions exist
    final_streams = []
    tokenized_urls = [s for s in streams if '?' in s and 'expires' in s]
    
    if tokenized_urls:
        final_streams = tokenized_urls
    else:
        final_streams = streams
    
    if final_streams:
        log.info(f"Found {len(final_streams)} streams for {event_name}")
    else:
        log.warning(f"No streams found for {event_name}")
        
    return final_streams


async def get_events() -> dict[str, dict[str, str | float]]:
    """Main function to get events and their streams"""
    events = {}
    
    # Step 1: Get events from main page
    main_events = await get_events_from_main_page()
    
    if not main_events:
        log.warning("No events found on main page")
        return events
    
    log.info(f"Found {len(main_events)} events on main page")
    
    # Step 2: For each event, extract streams
    for event_info in main_events:
        try:
            event_name = event_info['name']
            event_url = event_info['url']
            
            log.info(f"Processing event: {event_name}")
            
            # Extract streams from event page
            streams = await extract_streams_from_event_page(event_url, event_name)
            
            if not streams:
                log.warning(f"No streams found for {event_name}")
                continue
            
            # Get sport info for TVG ID and logo
            sport = event_info.get('sport', 'Live Event')
            tvg_id, logo = leagues.get_tvg_info(sport, event_name)
            
            # Add each stream as a separate entry
            for i, stream_url in enumerate(streams, start=1):
                key = f"[{sport}] {event_name} Stream {i} ({TAG})"
                
                events[key] = {
                    "url": stream_url,
                    "logo": logo,
                    "base": BASE_URL,
                    "timestamp": Time.now().timestamp(),
                    "id": tvg_id or f"stream_{i}",
                }
                
            log.info(f"Added {len(streams)} streams for {event_name}")
            
        except Exception as e:
            log.error(f"Error processing event {event_info.get('name', 'Unknown')}: {e}")
            continue
    
    return events


async def scrape() -> None:
    """Scrape events from website"""
    if cached := CACHE_FILE.load():
        urls.update(cached)
        log.info(f"Loaded {len(urls)} event(s) from cache")
        return
    
    log.info('Scraping from "xyzstreams"')
    
    events = await get_events()
    
    if events:
        urls.update(events)
        log.info(f"Collected and cached {len(urls)} new event(s)")
        CACHE_FILE.write(urls)
    else:
        log.warning("No events found")


def generate_playlists() -> None:
    """Generate VLC and TiviMate playlist files"""
    
    if not urls:
        log.warning("No events to generate playlists")
        return
        
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    
    header = (
        '#EXTM3U x-tvg-url="https://epgshare01.online/'
        'epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"\n'
        f"# Last Updated: {ts}\n\n"
    )
    
    # VLC PLAYLIST
    with open(VLC_OUTPUT, "w", encoding="utf-8") as f:
        f.write(header)
        ch_no = 1
        
        for event_name, event_data in urls.items():
            url = event_data.get("url")
            logo = event_data.get(
                "logo",
                "https://i.gyazo.com/4a5e9fa2525808ee4b65002b56d3450e.png",
            )
            tvg_id = event_data.get("id", "Live.Event.us")
            
            if not url:
                continue
                
            f.write(
                f'#EXTINF:-1 tvg-chno="{ch_no}" '
                f'tvg-id="{tvg_id}" '
                f'tvg-name="{event_name}" '
                f'tvg-logo="{logo}" '
                f'group-title="Live Events",{event_name}\n'
            )
            f.write(f"#EXTVLCOPT:http-referrer={REFERER}\n")
            f.write(f"#EXTVLCOPT:http-origin={ORIGIN}\n")
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(f"{url}\n\n")
            ch_no += 1
            
    log.info(f"Generated VLC playlist: {VLC_OUTPUT} with {ch_no - 1} streams")
    
    # TIVIMATE PLAYLIST
    ua_enc = quote(TIVIMATE_USER_AGENT, safe="")
    
    with open(TIVIMATE_OUTPUT, "w", encoding="utf-8") as f:
        f.write(header)
        ch_no = 1
        
        for event_name, event_data in urls.items():
            url = event_data.get("url")
            logo = event_data.get(
                "logo",
                "https://i.gyazo.com/4a5e9fa2525808ee4b65002b56d3450e.png",
            )
            tvg_id = event_data.get("id", "Live.Event.us")
            
            if not url:
                continue
                
            f.write(
                f'#EXTINF:-1 tvg-chno="{ch_no}" '
                f'tvg-id="{tvg_id}" '
                f'tvg-name="{event_name}" '
                f'tvg-logo="{logo}" '
                f'group-title="Live Events",{event_name}\n'
            )
            f.write(
                f'{url}'
                f'|referer={REFERER}'
                f'|origin={ORIGIN}'
                f'|user-agent={ua_enc}\n\n'
            )
            ch_no += 1
            
    log.info(f"Generated TiviMate playlist: {TIVIMATE_OUTPUT} with {ch_no - 1} streams")


async def main() -> None:
    """Run updater and generate playlists"""
    log.info("Starting SHOPP playlist generator")
    await scrape()
    generate_playlists()
    log.info("Playlist generation completed")
    
    print("\nPlaylists generated successfully!")
    print(f"VLC: {VLC_OUTPUT}")
    print(f"TiviMate: {TIVIMATE_OUTPUT}")
    print(f"Total streams: {len(urls)}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
