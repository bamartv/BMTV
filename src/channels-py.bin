import requests
import re
from playwright.sync_api import sync_playwright

# ==========================================
# 1. CONFIGURATION
# ==========================================
CHANNELS_URL = "https://api.cdnlivetv.tv/api/v1/channels/?user=cdnlivetv&plan=free"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
SPOOF_IP = "77.238.79.111"

HEADERS = {
    "Accept": "application/json",
    "Origin": "https://streamsports99.su",
    "Referer": "https://streamsports99.su/",
    "User-Agent": USER_AGENT
}

# ==========================================
# 2. HIGH-SPEED CHANNELS PLAYLIST GENERATOR
# ==========================================
def build_channel_playlist():
    print("[*] Fetching Channels API...")
    try:
        resp = requests.get(CHANNELS_URL, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            print(f"[-] Failed to fetch API. Status: {resp.status_code}")
            return
        channels = resp.json().get("channels", [])
        print(f"[+] Found {len(channels)} total channels.")
    except Exception as e:
        print(f"[-] Error fetching API: {e}")
        return

    print("[*] Starting Playwright Browser (Blitz Mode)...")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, 
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-web-security']
        )
        
        context = browser.new_context(
            user_agent=USER_AGENT,
            extra_http_headers={
                "Referer": "https://streamsports99.su/",
                "Origin": "https://streamsports99.su"
            }
        )
        
        page = context.new_page()
        
        # Block images/css/fonts so the page loads instantly
        page.route("**/*", lambda route: 
            route.abort() if route.request.resource_type in ["image", "media", "font", "stylesheet"] 
            else route.continue_()
        )
        
        with open("live_tv_channels.m3u", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            channel_id = 1
            
            for ch in channels:
                # Skip offline channels to save time
                if ch.get("status") != "online":
                    continue
                    
                ch_name = ch.get("name", "Unknown Channel")
                country_code = ch.get("code", "xx").upper()
                logo = ch.get("image", "")
                player_url = ch.get("url")
                
                group_title = f"Live TV - {country_code}"
                
                if not player_url:
                    continue
                    
                print(f"-> Blitzing: [{country_code}] {ch_name}")
                
                try:
                    # Wait exactly for the m3u8 request to fire, fail instantly if it takes longer than 3 seconds
                    with page.expect_request(re.compile(r"\.m3u8"), timeout=3000) as m3u8_req:
                        page.goto(player_url)
                    
                    # Grab the URL the millisecond it generates
                    final_url = m3u8_req.value.url
                    
                    f.write(f'#EXTINF:-1 tvg-chno="{channel_id}" tvg-id="{ch_name.replace(" ", "")}.{country_code}" tvg-name="{ch_name}" tvg-logo="{logo}" group-title="{group_title}",{ch_name}\n')
                    f.write(f'#EXTVLCOPT:http-referrer={player_url}\n')
                    f.write(f'#EXTVLCOPT:http-origin={player_url}\n')
                    f.write(f'#EXTVLCOPT:http-user-agent={USER_AGENT}\n')
                    f.write(f'{final_url}|x-forwarded-for:{SPOOF_IP}\n\n')
                    
                    channel_id += 1
                    print(f"  [+] Snagged it.")
                    
                except Exception:
                    # If it hits the 3-second timeout, it skips and moves on with zero hesitation
                    print(f"  [-] Missed it. Moving on.")
                        
        browser.close()
        print("\n[+] Finished! Saved to 'live_tv_channels.m3u'")

if __name__ == "__main__":
    build_channel_playlist()
