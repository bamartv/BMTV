import requests
import sys
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# URL delle API
API_MATCHES_URL = "https://streamed.pk/api/matches/all-today"
API_STREAM_BASE_URL = "https://streamed.pk/api/stream/{source}/{stream_id}"

# Intestazioni per simulare un browser
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Referer": "https://streamed.pk/"
}

def find_repo_root():
    """
    Trova la root del repository Git partendo dalla cartella corrente.
    Se lo script viene lanciato da fuori repo, prova anche dalla cartella dello script.
    """
    start_dirs = [Path.cwd().resolve(), Path(__file__).resolve().parent]
    for start_dir in start_dirs:
        for directory in (start_dir, *start_dir.parents):
            if (directory / ".git").exists():
                return directory

    return Path.cwd().resolve()

def get_output_path(filename):
    """
    Salva nella root del repository Git, anche se lo script e' in una sottocartella.
    Se non trova un repository Git, salva nella cartella corrente.
    """
    return find_repo_root() / filename

OUTPUT_FILE = str(get_output_path("streamed.m3u"))

def process_match(match, session):
    """
    Processa una singola partita e restituisce i dati.
    """
    title = match.get('title', 'Titolo Sconosciuto')
    category = match.get('category', 'Sport').capitalize()
    sources = match.get('sources', [])
    poster_path = match.get('poster', '')
    logo_url = f"https://streamed.pk{poster_path}" if poster_path else ""

    time_str = ""
    timestamp_ms = match.get('date')
    if timestamp_ms:
        try:
            time_str = datetime.fromtimestamp(timestamp_ms / 1000).strftime('%H:%M')
        except (ValueError, TypeError):
            pass
    
    print(f"\nAnalizzo partita: {title} ({category})")
    
    for source_info in sources:
        source = source_info.get('source')
        stream_id = source_info.get('id')

        if not source or not stream_id:
            continue

        print(f"  - Provo la fonte: {source}")
        
        stream_api_url = API_STREAM_BASE_URL.format(source=source, stream_id=stream_id)
        
        try:
            # Logica di retry per gestire errori temporanei del server (es. 522)
            stream_details_response = None
            for attempt in range(3): # Prova fino a 3 volte
                try:
                    response = session.get(stream_api_url, timeout=20)
                    response.raise_for_status() # Solleva un'eccezione per errori 4xx/5xx
                    stream_details_response = response
                    break # Se la richiesta ha successo, esci dal ciclo
                except requests.exceptions.RequestException as e:
                    is_server_error = hasattr(e, 'response') and e.response is not None and 500 <= e.response.status_code < 600
                    if is_server_error and attempt < 2:
                        wait_time = (attempt + 1) * 2
                        print(f"  - Errore server ({e.response.status_code}). Riprovo tra {wait_time} secondi...")
                        time.sleep(wait_time)
                    else:
                        raise # Se non è un errore server o è l'ultimo tentativo, solleva l'eccezione
            
            streams = stream_details_response.json() if stream_details_response else []

            if not streams:
                continue

            streams_with_embed = [stream for stream in streams if stream.get('embedUrl')]
            if not streams_with_embed:
                continue

            target_stream = None
            for stream in streams_with_embed:
                if stream.get('language', '').lower() in ('italiano', 'italian', 'ita'):
                    target_stream = stream
                    break

            if not target_stream:
                target_stream = streams_with_embed[0]

            if not target_stream:
                continue # Passa alla prossima fonte
                
            language = target_stream.get('language', 'N/A')
            embed_url = target_stream.get('embedUrl')
                
            print(f"  - Trovato stream in '{language}'. Uso embed: {embed_url}")

            channel_name = f"{category} | {title} | {time_str}"
            m3u_entry = (
                f'#EXTINF:-1 tvg-id="" tvg-logo="{logo_url}" tvg-name="{channel_name} ({language})" group-title="Eventi Live STREAMED",{channel_name} ({language})\n'
                f'{embed_url}'
            )
            
            return m3u_entry # Trovato un embed valido, esci e restituisci per questa partita

        except Exception as e:
            print(f"  - Errore: {e}")
            continue
    
    return None

def generate_m3u(output_file: str = OUTPUT_FILE) -> str:
    """
    Genera la playlist M3U e restituisce il percorso del file creato.
    """
    m3u_content = ["#EXTM3U"]
    
    # Crea una sessione per riutilizzare le connessioni e mantenere gli header
    session = requests.Session()
    session.headers.update(HEADERS)
    
    try:
        # 1. Ottieni la lista delle partite
        print("Recupero la lista delle partite di oggi...")
        matches_response = session.get(API_MATCHES_URL, timeout=15)
        matches_response.raise_for_status()
        matches = matches_response.json()
        print(f"Trovate {len(matches)} partite.")

        # 2. Processa le partite in parallelo (max 5 contemporaneamente)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(process_match, match, session): match for match in matches}
            
            for future in as_completed(futures):
                result = future.result()
                if result:
                    m3u_content.append(result)
        
        # Se non 蠳tato trovato nessun evento, aggiungi un canale di fallback
        if len(m3u_content) == 1: # Solo #EXTM3U 蠰resente
            print("\nNessun evento trovato. Aggiungo un canale di fallback 'NESSUN EVENTO'.")
            fallback_entry = (
                '#EXTINF:-1 tvg-id="" tvg-logo="" tvg-name="NESSUN EVENTO" group-title="Eventi Live STREAMED",NESSUN EVENTO\n'
                'https://example.com/no_event' # Link di esempio
            )
            m3u_content.append(fallback_entry)
        # Codice vecchio rimosso
        if False:
            match = None
            pass

    except requests.exceptions.RequestException as e:
        print(f"Errore critico nel recuperare la lista delle partite: {e}")
        return ""

    # 7. Scrivi il file M3U nella root del repository Git
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Nessuna cartella da creare per file root
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(m3u_content))

    print(f"\nFinito! Playlist salvata: {output_path}")
    return str(output_path)

def create_m3u_playlist():
    """
    Compatibilita' con il vecchio nome della funzione.
    """
    return generate_m3u()

if __name__ == "__main__":
    generate_m3u()
