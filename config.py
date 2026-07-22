import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not DISCORD_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN が .env に設定されていません。")

ALERT_CHANNEL_ID = int(os.getenv("DISCORD_ALERT_CHANNEL_ID", 0))

WARNING_TARGET_AREAS = []

JMA_ATOM_NON_REGULAR_URL = (
    "https://www.data.jma.go.jp/developer/xml/feed/non_regular.xml"
)
JMA_ATOM_REGULAR_URL = "https://www.data.jma.go.jp/developer/xml/feed/extra.xml"

DATA_DIR = "data"
PROCESSED_EVENTS_FILE = os.path.join(DATA_DIR, "processed_events.json")

os.makedirs(DATA_DIR, exist_ok=True)
