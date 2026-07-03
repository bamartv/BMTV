import urllib.request
import os
import sys

# Try to load environment variables from a .env file (for local development)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# The URL and any other sensitive data can be passed via environment variables
# In GitHub Actions, these are set in the workflow file using secrets
PLAYLIST_URL = os.getenv("PLAYLIST_URL") or "https://raw.githubusercontent.com/opensourceflix/Friday/refs/heads/main/tmp/%25/privates.m3u8"
MY_SECRET = os.getenv("MY_SECRET")

if not PLAYLIST_URL:
    print("Error: PLAYLIST_URL not set.")
    print("If running locally, create a .env file or set the environment variable.")
    sys.exit(1)

print(f"Fetching from hidden source...")

req = urllib.request.Request(PLAYLIST_URL, headers={"User-Agent": "Mozilla/5.0"})

try:
    with urllib.request.urlopen(req) as response:
        content = response.read().decode("utf-8")

    # Save the file in the root directory (one level up from this script)
    output_path = os.path.join(os.path.dirname(__file__), "../../Sayan.m3u")
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    print("Successfully updated Sayan.m3u")
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
