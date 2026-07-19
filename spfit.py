#!/usr/bin/env python3

import asyncio
from urllib.parse import urljoin
from datetime import datetime
import re
from concurrent.futures import ThreadPoolExecutor

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from selectolax.parser import HTMLParser

from utils import Cache, Event, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "SPFIT"

CACHE_FILE = Cache(TAG, exp=28_800)

BASE_URL = "https://streamseast.me"

# Output files
VLC_OUTPUT = "spfit_vlc.m3u8"
TIVIMATE_OUTPUT = "spfit_tivimate.m3u8"

# Headers
USER_AGENT = "Mozilla%2F5.0%20(Windows%20NT%2010.0%3B%20Win64%3B%20x64)%20AppleWebKit%2F537.36%20(KHTML%2C%20like%20Gecko)%20Chrome%2F120.0.0.0%20Safari%2F537.36"
REFERER = "https://sportspass.shop/"
ORIGIN = "https://sportspass.shop"

# Sport categories
SPORT_CATEGORIES = {
    "Soccer": "/soccer",
    # "NBA": "/nba",
    # "NFL": "/nfl",
    # "NHL": "/nhl",
    "MLB": "/mlb",
    "MMA": "/mma",
    "Boxing": "/boxing",
    "F1": "/f1",
}

# Concurrency settings
MAX_CONCURRENT_PAGES = 5
PAGE_TIMEOUT = 10000
RETRY_ATTEMPTS = 2


def clean_event_name(event_name: str) -> str:
    """Clean event name by removing commas and extra spaces"""
    if not event_name:
        return event_name
    
    cleaned = event_name.replace(",", "")
    cleaned = re.sub(r'\s+', ' ', cleaned)
    cleaned = re.sub(r'\s*-\s*(?:Live|Stream|Watch|SPFIT)\s*$', '', cleaned, flags=re.IGNORECASE)
    
    return cleaned.strip()


async def get_event_links_from_category(page, category_url: str) -> list[tuple[str, str]]:
    """Extract event links from category page"""
    events = []
    
    try:
        await page.goto(category_url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(1000)
        
        # Optimized selector for event links
        links = await page.query_selector_all('a[href*="/soccer/"], a[href*="/nba/"], a[href*="/nhl/"], a[href*="/mlb/"], a[href*="/mma/"], a[href*="/boxing/"], a[href*="/f1/"]')
        
        for link in links:
            href = await link.get_attribute('href')
            if not href:
                continue
            
            # Get full URL
            full_url = urljoin(BASE_URL, href) if href.startswith('/') else href
            
            # Get event name
            event_name = await link.inner_text()
            event_name = clean_event_name(event_name)
            
            if full_url not in [e[1] for e in events]:
                events.append((event_name, full_url))
        
    except Exception as e:
        log.error(f"Error getting events from {category_url}: {e}")
    
    return events


async def extract_m3u8_from_event(page, event_url: str, event_name: str, url_num: int) -> str | None:
    """Navigate to event page and extract m3u8 stream URL"""
    
    try:
        # Navigate to event page with shorter timeout
        await page.goto(event_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        await page.wait_for_timeout(2000)
        
        # Set up network request monitoring
        m3u8_url = None
        
        def handle_request(request):
            nonlocal m3u8_url
            if '.m3u8' in request.url and not m3u8_url:
                m3u8_url = request.url
        
        page.on('request', handle_request)
        
        # Quick check for iframes
        iframes = await page.query_selector_all('iframe')
        for iframe in iframes[:2]:  # Check first 2 iframes only
            try:
                src = await iframe.get_attribute('src')
                if src and src.startswith('http'):
                    await page.goto(src, wait_until="domcontentloaded", timeout=8000)
                    await page.wait_for_timeout(1500)
                    
                    # Try to click play button quickly
                    play_button = await page.query_selector('button.vjs-big-play-button, button.play-btn')
                    if play_button:
                        await play_button.click()
                        await page.wait_for_timeout(1000)
            except:
                pass
        
        # Wait for m3u8 with shorter timeouts
        for attempt in range(8):  # Reduced attempts
            if m3u8_url:
                break
            await page.wait_for_timeout(1000)
            
            # Quick page source check
            content = await page.content()
            m3u8_match = re.search(r'https?://[^\s"\']+\.m3u8[^\s"\']*', content)
            if m3u8_match:
                m3u8_url = m3u8_match.group(0)
                break
        
        page.remove_listener('request', handle_request)
        
        if m3u8_url:
            log.info(f"✓ [{url_num}] {event_name[:50]}")
            return m3u8_url
        else:
            log.debug(f"✗ [{url_num}] No stream: {event_name[:50]}")
            return None
            
    except Exception as e:
        log.debug(f"✗ [{url_num}] Error: {event_name[:50]} - {str(e)[:50]}")
        return None


async def process_event_batch(browser, events_batch, cached_urls, now, batch_num):
    """Process a batch of events concurrently"""
    tasks = []
    
    for idx, event in enumerate(events_batch):
        page = await browser.new_page()
        task = process_single_event(page, event, cached_urls, now, idx)
        tasks.append(task)
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Close all pages
    for task in tasks:
        if hasattr(task, 'page'):
            await task.page.close()
    
    return results


async def process_single_event(page, event, cached_urls, now, idx):
    """Process a single event"""
    try:
        m3u8_url = await extract_m3u8_from_event(page, event['url'], event['name'], idx)
        
        if m3u8_url:
            tvg_id, logo = leagues.get_tvg_info(event['sport'], event['name'])
            
            entry = {
                "url": m3u8_url,
                "logo": logo,
                "base": event['url'],
                "timestamp": now.timestamp(),
                "id": tvg_id or "Live.Event.us",
                "link": event['url'],
            }
            
            cached_urls[event['key']] = entry
            urls[event['key']] = entry
            return True
    except Exception as e:
        log.debug(f"Error processing {event['name'][:50]}: {e}")
    finally:
        await page.close()
    
    return False


async def scrape() -> None:
    """Main scraping function with concurrent processing"""
    cached_urls = CACHE_FILE.load() or {}
    
    # Load cached URLs
    valid_urls = {k: v for k, v in cached_urls.items() if v.get("url")}
    urls.update(valid_urls)
    log.info(f"Loaded {len(valid_urls)} event(s) from cache")
    
    # Get all event links from categories
    all_events = []
    
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, 
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
        )
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent=USER_AGENT
        )
        page = await context.new_page()
        
        try:
            for sport, category_path in SPORT_CATEGORIES.items():
                category_url = urljoin(BASE_URL, category_path)
                log.info(f"Scanning {sport}...")
                
                events = await get_event_links_from_category(page, category_url)
                log.info(f"Found {len(events)} events in {sport}")
                
                for event_name, event_url in events:
                    key = f"[{sport}] {event_name} ({TAG})"
                    if key in cached_urls and cached_urls[key].get("url"):
                        continue
                    
                    all_events.append({
                        "sport": sport,
                        "name": event_name,
                        "url": event_url,
                        "key": key
                    })
        finally:
            await browser.close()
    
    if not all_events:
        log.info("No new events to process")
        return
    
    log.info(f"Processing {len(all_events)} new events with {MAX_CONCURRENT_PAGES} concurrent workers")
    
    # Process events in batches
    now = Time.clean(Time.now())
    new_count = 0
    
    # Split events into batches
    batches = [all_events[i:i + MAX_CONCURRENT_PAGES] for i in range(0, len(all_events), MAX_CONCURRENT_PAGES)]
    
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, 
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
        )
        
        for batch_num, batch in enumerate(batches, 1):
            log.info(f"Processing batch {batch_num}/{len(batches)} ({len(batch)} events)")
            
            # Process batch concurrently
            tasks = []
            for event in batch:
                page = await browser.new_page()
                task = process_single_event(page, event, cached_urls, now, 0)
                tasks.append(task)
            
            results = await asyncio.gather(*tasks)
            new_count += sum(1 for r in results if r)
            
            # Small delay between batches
            await asyncio.sleep(1)
        
        await browser.close()
    
    # Save to cache
    CACHE_FILE.write(cached_urls)
    log.info(f"Collected {new_count} new streams, total: {len(urls)}")


def generate_playlists() -> None:
    """Generate VLC and TiviMate playlist files from collected events"""
    if not urls:
        log.warning("No events to generate playlists")
        with open(VLC_OUTPUT, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n# No events available\n")
        with open(TIVIMATE_OUTPUT, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n# No events available\n")
        return

    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    header = f'#EXTM3U x-tvg-url="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"\n# Last Updated: {ts}\n# Total Streams: {len(urls)}\n\n'

    # Generate VLC playlist
    try:
        with open(VLC_OUTPUT, "w", encoding="utf-8") as f:
            f.write(header)
            
            ch_no = 1
            for event_name, event_data in urls.items():
                url = event_data.get("url")
                logo = event_data.get("logo", "https://i.gyazo.com/1c4aa937f5ea01b0f29bb27adb59884c.png")
                tvg_id = event_data.get("id", "Live.Event.us")
                
                if not url:
                    continue
                
                clean_name = clean_event_name(event_name)
                
                f.write(f'#EXTINF:-1 tvg-chno="{ch_no}" tvg-id="{tvg_id}" tvg-name="{clean_name}" tvg-logo="{logo}" group-title="Live Events",{clean_name}\n')
                f.write(f'#EXTVLCOPT:http-referrer={REFERER}\n')
                f.write(f'#EXTVLCOPT:http-origin={ORIGIN}\n')
                f.write(f'#EXTVLCOPT:http-user-agent={USER_AGENT}\n')
                f.write(f'{url}\n\n')
                
                ch_no += 1
        
        log.info(f"Generated VLC playlist: {VLC_OUTPUT} with {ch_no - 1} streams")
    except Exception as e:
        log.error(f"Error generating VLC playlist: {e}")

    # Generate TiviMate playlist (WITHOUT encoding)
    try:
        with open(TIVIMATE_OUTPUT, "w", encoding="utf-8") as f:
            f.write(header)
            
            ch_no = 1
            for event_name, event_data in urls.items():
                url = event_data.get("url")
                logo = event_data.get("logo", "https://i.gyazo.com/1c4aa937f5ea01b0f29bb27adb59884c.png")
                tvg_id = event_data.get("id", "Live.Event.us")
                
                if not url:
                    continue
                
                clean_name = clean_event_name(event_name)
                
                # Write TiviMate format
                f.write(f'#EXTINF:-1 tvg-chno="{ch_no}" tvg-id="{tvg_id}" tvg-name="{clean_name}" tvg-logo="{logo}" group-title="Live Events",{clean_name}\n')
                f.write(f'{url}|referer={REFERER}|origin={ORIGIN}|user-agent={USER_AGENT}\n\n')
                
                ch_no += 1
        
        log.info(f"Generated TiviMate playlist: {TIVIMATE_OUTPUT} with {ch_no - 1} streams")
    except Exception as e:
        log.error(f"Error generating TiviMate playlist: {e}")


async def main() -> None:
    """Main function to run the scraper and generate playlists"""
    log.info("Starting SPFIT playlist generator")
    
    try:
        await scrape()
        generate_playlists()
        
        log.info("Playlist generation completed")
        print(f"\n SPFIT Playlists generated successfully!")
        print(f"    VLC: {VLC_OUTPUT}")
        print(f"    TiviMate: {TIVIMATE_OUTPUT}")
        print(f"    Total streams: {len(urls)}")
    except Exception as e:
        log.error(f"Error in main execution: {e}")
        print(f"\n Error: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
