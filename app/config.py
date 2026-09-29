"""Central configuration knobs."""
from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "cards.db"

# Refetch a card's price if the stored copy is older than this many days.
PRICE_TTL_DAYS = 7

# --- Scryfall client (app/scryfall.py) ---
SCRYFALL_BASE = "https://api.scryfall.com"
# Scryfall asks every client to send a descriptive, identifying User-Agent.
# No personal contact info required - name + version is enough.
USER_AGENT = "OCR-Card-Scanner/0.1"
# Minimum seconds between Scryfall requests (their guidance is 50-100ms).
SCRYFALL_MIN_INTERVAL = 0.1
