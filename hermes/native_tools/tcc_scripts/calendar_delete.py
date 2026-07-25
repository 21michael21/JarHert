import json, sys
from pathlib import Path
from src.config import load_config
from src.formatter import format_event
from src.google_calendar_client import GoogleCalendarClient
payload = json.loads(sys.argv[1]); config = load_config(Path('.')); calendar = GoogleCalendarClient(config, Path('.'))
event = calendar.delete_event_by_title(payload['title'])
print(format_event(event)); print(f"calendar_event_id={event.get('id')}")
if event.get('htmlLink'): print(event.get('htmlLink'))
