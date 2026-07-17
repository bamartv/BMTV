import requests
import json
import base64
import re
from typing import List, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

APP_PASSWORD = "oAR80SGuX3EEjUGFRwLFKBTiris="


@dataclass
class SportzxChannel:
    event_title: str
    event_id: str
    event_cat: str
    event_name: str
    event_time: str
    channel_title: Optional[str] = None
    stream_url: str = ""
    keyid: Optional[str] = None
    key: Optional[str] = None
    api: Optional[str] = None
    headers: Optional[str] = None
    referer: Optional[str] = None
    origin: Optional[str] = None


class SportzxClient:
    def __init__(self, excluded_categories: List[str] = None, timeout: int = 12):
        self.excluded_categories = set(c.lower() for c in (excluded_categories or []))
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Dalvik/2.1.0 (Linux; Android 13)",
            "Accept-Encoding": "gzip",
        })

    def _generate_aes_key_iv(self, s: str) -> tuple[bytes, bytes]:
        CHARSET = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+!@#$%&="

        def u32(x: int) -> int:
            return x & 0xFFFFFFFF

        data = s.encode("utf-8")
        n = len(data)

        u = 0x811c9dc5
        for b in data:
            u = u32((u ^ b) * 0x1000193)

        key = bytearray(16)
        for i in range(16):
            b = data[i % n]
            u = u32(u * 0x1f + (i ^ b))
            key[i] = CHARSET[u % len(CHARSET)]

        u = 0x811c832a
        for b in data:
            u = u32((u ^ b) * 0x1000193)

        iv = bytearray(16)
        idx = 0
        acc = 0
        while idx != 0x30:
            b = data[idx % n]
            u = u32(u * 0x1d + (acc ^ b))
            iv[idx // 3] = CHARSET[u % len(CHARSET)]
            idx += 3
            acc = u32(acc + 7)

        return bytes(key), bytes(iv)

    def _decrypt_data(self, b64_data: str) -> str:
        if not b64_data.strip():
            return ""

        try:
            ct = base64.b64decode(b64_data)
            key, iv = self._generate_aes_key_iv(APP_PASSWORD)

            from Crypto.Cipher import AES

            cipher = AES.new(key, AES.MODE_CBC, iv)
            pt = cipher.decrypt(ct)

            pad = pt[-1]
            if 1 <= pad <= 16:
                pt = pt[:-pad]

            return pt.decode("utf-8", errors="replace")
        except Exception as e:
            print(f"Decryption error: {e}")
            return ""

    def _fetch_and_decrypt(self, url: str) -> dict:
        try:
            r = self.session.get(url, timeout=self.timeout)
            r.raise_for_status()
            encrypted = r.json().get("data", "")
            decrypted = self._decrypt_data(encrypted)
            if not decrypted:
                return {}
            return json.loads(decrypted)
        except Exception as e:
            print(f"Fetch/decrypt failed {url}: {e}")
            return {}

    def _get_api_url(self) -> Optional[str]:
        install_url = "https://firebaseinstallations.googleapis.com/v1/projects/sportzx-7cc3f/installations"
        install_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Dalvik/2.1.0 (Linux; Android 13)",
            "X-Android-Cert": "A0047CD121AE5F71048D41854702C52814E2AE2B",
            "X-Android-Package": "com.sportzx.live",
            "x-firebase-client": "H4sIAAAAAAAAAKtWykhNLCpJSk0sKVayio7VUSpLLSrOzM9TslIyUqoFAFyivEQfAAAA",
            "x-goog-api-key": "AIzaSyBa5qiq95T97xe4uSYlKo0Wosmye_UEf6w",
        }
        install_body = {
            "fid": "eOaLWBo8S7S1oN-vb23mkf",
            "appId": "1:446339309956:android:b26582b5d2ad841861bdd1",
            "authVersion": "FIS_v2",
            "sdkVersion": "a:18.0.0"
        }

        try:
            r = self.session.post(install_url, json=install_body, headers=install_headers, timeout=self.timeout)
            r.raise_for_status()
            auth_token = r.json()["authToken"]["token"]
        except Exception as e:
            print(f"Firebase Install error: {e}")
            return None

        config_url = "https://firebaseremoteconfig.googleapis.com/v1/projects/446339309956/namespaces/firebase:fetch"
        config_headers = {
            "Content-Type": "application/json",
            "User-Agent": "Dalvik/2.1.0 (Linux; Android 13)",
            "X-Android-Cert": "A0047CD121AE5F71048D41854702C52814E2AE2B",
            "X-Android-Package": "com.sportzx.live",
            "X-Firebase-RC-Fetch-Type": "BASE/1",
            "X-Goog-Api-Key": "AIzaSyBa5qiq95T97xe4uSYlKo0Wosmye_UEf6w",
            "X-Goog-Firebase-Installations-Auth": auth_token,
        }

        config_body = {
            "appVersion": "2.1",
            "firstOpenTime": "2025-11-10T16:00:00.000Z",
            "timeZone": "Europe/Rome",
            "appInstanceIdToken": auth_token,
            "languageCode": "it-IT",
            "appBuild": "12",
            "appInstanceId": "eOaLWBo8S7S1oN-vb23mkf",
            "countryCode": "IT",
            "appId": "1:446339309956:android:b26582b5d2ad841861bdd1",
            "platformVersion": "33",
            "sdkVersion": "22.1.2",
            "packageName": "com.sportzx.live"
        }

        try:
            r = self.session.post(config_url, json=config_body, headers=config_headers, timeout=self.timeout)
            r.raise_for_status()
            return r.json().get("entries", {}).get("api_url")
        except Exception as e:
            print(f"Remote Config error: {e}")
            return None

    def get_channels(self) -> List[SportzxChannel]:
        api_url = self._get_api_url()
        if not api_url:
            print("Non è stato possibile ottenere l'URL API")
            return []  # SEMPRE lista, mai None

        channels_list: List[SportzxChannel] = []

        events_url = f"{api_url.rstrip('/')}/events.json"
        events = self._fetch_and_decrypt(events_url)

        if not isinstance(events, list):
            events = []

        valid_events = [
            e for e in events
            if isinstance(e, dict) and e.get("cat") and e["cat"].lower() not in self.excluded_categories
        ]

        for event in valid_events:
            eid = event.get("id")
            if not eid:
                continue

            ch_url = f"{api_url.rstrip('/')}/channels/{eid}.json"
            channels = self._fetch_and_decrypt(ch_url)

            if not isinstance(channels, list):
                continue

            start_time = event.get("eventInfo", {}).get("startTime", "")
            event_time_full = start_time[:16].replace("/", "-") if start_time else ""

            for ch in channels:
                if not isinstance(ch, dict):
                    continue

                link = ch.get("link", "")
                if not link:
                    continue

                parts = link.split("|", 1)
                stream_url = parts[0].strip()

                keyid = key = None
                api_val = ch.get("api")
                if api_val and ":" in api_val:
                    keyid, key = api_val.split(":", 1)

                channels_list.append(SportzxChannel(
                    event_title=event.get("title", "Evento senza titolo"),
                    event_id=eid,
                    event_cat=event.get("cat", ""),
                    event_name=event.get("eventInfo", {}).get("eventName", ""),
                    event_time=event_time_full,
                    channel_title=ch.get("title"),
                    stream_url=stream_url,
                    keyid=keyid,
                    key=key,
                    api=api_val,
                ))

        return channels_list

    def _increase_time_by_one_hour(self, time_str: str) -> str:
        if not time_str or len(time_str) < 5 or ':' not in time_str:
            return time_str

        try:
            time_part = time_str.split()[-1][:5]
            hh, mm = map(int, time_part.split(':'))
            if not (0 <= hh <= 23 and 0 <= mm <= 59):
                return time_part

            new_time = datetime(2000, 1, 1, hh, mm) + timedelta(hours=1)
            return new_time.strftime("%H:%M")
        except:
            return time_str

    def _get_custom_logo(self, event_title: str, base_url: str, default_logo: str) -> str:
        match = re.match(r"^(.*?)\s+vs\s+(.*?)(?:\s+\d+:\d+|$)", event_title, re.IGNORECASE)
        if not match:
            return default_logo

        squadra1, squadra2 = match.groups()
        clean1 = re.sub(r'\W+', '', squadra1.lower())
        clean2 = re.sub(r'\W+', '', squadra2.lower())
        filename = f"{clean1}{clean2}.png"
        potential_logo = f"{base_url}{filename}"

        try:
            r = self.session.head(potential_logo, timeout=5)
            if r.status_code == 200:
                return potential_logo
        except Exception:
            pass

        return default_logo

    def generate_m3u(
        self,
        channels: List[SportzxChannel],
        filename: str = "SerieA_Sportzx.m3u8",
        generic_logo: str = "https://i.postimg.cc/kMpRZ1dn/sportzx-combined.png",
        serie_a_only: bool = True,
        serie_a_keywords: List[str] = None
    ) -> str:
        base_logo_url = "https://raw.githubusercontent.com/wasimud/simud/refs/heads/main/LoghiFiniti/"

        if serie_a_keywords is None:
            serie_a_keywords = [
                "serie a",
                "serie-a",
                "seriea",
                "tim serie a",
                "serie a tim",
                "lega serie a",
                "lega calcio a",
            ]

        keywords = set(k.lower() for k in serie_a_keywords)

        lines = ["#EXTM3U", "#EXT-X-VERSION:3", ""]

        included = 0
        matched_channels = []

        for ch in channels:
            if not ch.stream_url or not ch.stream_url.lower().endswith((".mpd", ".m3u8")):
                continue

            cat_lower   = ch.event_cat.lower().strip()
            title_lower = ch.event_title.lower().strip()
            name_lower  = ch.event_name.lower().strip()

            is_match = False
            if not serie_a_only:
                is_match = True
            else:
                matching_info = []
                for kw in sorted(keywords):
                    found_in = []
                    if kw in cat_lower:   found_in.append("cat")
                    if kw in title_lower: found_in.append("title")
                    if kw in name_lower:  found_in.append("name")
                    if found_in:
                        matching_info.append(f"  • '{kw}' → {', '.join(found_in)}")
                        is_match = True

                if matching_info:
                    print("\n" + "═" * 80)
                    print(f"INCLUSO → {ch.event_title}")
                    print(f"  Cat:  {ch.event_cat}")
                    print(f"  Tit:  {ch.event_title}")
                    print(f"  Nome: {ch.event_name or '(vuoto)'}")
                    print("  Keyword che hanno matchato:")
                    for line in matching_info:
                        print(line)
                    print("═" * 80 + "\n")

            if is_match:
                included += 1
                matched_channels.append(ch)

        # ────────────────────────────────────────────────
        # SE NON C'È NESSUN CANALE → inseriamo placeholder
        # ────────────────────────────────────────────────
        if included == 0:
            print("NESSUN evento trovato → genero playlist con placeholder 'Nessun Evento'")
            
            placeholder_line = (
                '#EXTINF:-1 tvg-id="noevent" '
                'tvg-logo="https://i.postimg.cc/fTHBgY5d/Chat-GPT-Image-9-feb-2026-13-35-25.png" '
                'group-title="NoEvent",Nessun Evento'
            )
            lines.append(placeholder_line)
            lines.append("https://github.com/wasimud/simud/raw/refs/heads/main/Skymud.mp4")
            lines.append("")
        else:
            # Caso normale: aggiungiamo i canali che hanno matchato
            for ch in matched_channels:
                custom_logo = self._get_custom_logo(ch.event_title, base_logo_url, generic_logo)

                evento = (ch.event_title or "Evento").strip()

                orario_originale = ""
                if ch.event_time and len(ch.event_time) >= 11:
                    parti = ch.event_time.split()
                    if len(parti) >= 2:
                        orario_originale = parti[1][:5]

                orario_aumentato = self._increase_time_by_one_hour(orario_originale)
                orario_part = f" {orario_aumentato}" if orario_aumentato else ""

                canale = ""
                if ch.channel_title and ch.channel_title.strip():
                    tit_canale = ch.channel_title.strip()
                    if tit_canale.lower() not in evento.lower():
                        canale = f" ({tit_canale})"

                nome_finale = f"{evento}{orario_part}{canale}".strip()
                nome_pulito = re.sub(r'[^\w\s\-\:\(\)\,\.\']', ' ', nome_finale).strip()

                gruppo = "Serie A" if serie_a_only else (ch.event_cat.capitalize() or "Sportzx")

                tvg = re.sub(r'[^a-z0-9]', '', nome_pulito.lower())
                tvg_id = tvg[:50] if tvg else f"seriea-{ch.event_id[:8]}"

                extinf = (
                    f'#EXTINF:-1 tvg-id="{tvg_id}" '
                    f'tvg-logo="{custom_logo}" '
                    f'group-title="{gruppo}",{nome_pulito}'
                )

                lines.append(extinf)

                if ch.keyid and ch.key:
                    lines.append("#KODIPROP:inputstream.adaptive.license_type=clearkey")
                    lines.append(f"#KODIPROP:inputstream.adaptive.license_key={ch.keyid}:{ch.key}")

                lines.append(ch.stream_url)
                lines.append("")

        contenuto = "\n".join(lines).rstrip()

        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(contenuto + "\n")
            print(f"\nPlaylist salvata: {filename}")
            print(f"Canali inclusi: {included if included > 0 else 1} (placeholder se zero)")
        except Exception as e:
            print(f"Errore durante il salvataggio: {e}")

        return contenuto


if __name__ == "__main__":
    client = SportzxClient(
        excluded_categories=["adult", "test", "xxx", "movie", "series"],
        timeout=15
    )

    print("Recupero canali in corso...")
    canali = client.get_channels()

    print(f"Trovati {len(canali)} canali/eventi in totale\n")

    if canali:
        print("Generazione playlist SOLO SERIE A con debug keyword attivo...\n")
        client.generate_m3u(
            channels=canali,
            filename="SerieA_Sportzx.m3u8",
            generic_logo="https://i.postimg.cc/kMpRZ1dn/sportzx-combined.png",
            serie_a_only=True,
        )
    else:
        print("Nessun canale trovato → verrà generato placeholder 'Nessun Evento'")
        # Forza comunque la generazione con lista vuota → scatterà il placeholder
        client.generate_m3u(
            channels=[],
            filename="SerieA_Sportzx.m3u8",
            generic_logo="https://i.postimg.cc/kMpRZ1dn/sportzx-combined.png",
            serie_a_only=True,
        )
