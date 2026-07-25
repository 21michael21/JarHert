from pathlib import Path
from src.config import load_config
from src.google_calendar_client import GoogleCalendarClient
config = load_config(Path('.'))
calendar = GoogleCalendarClient(config, Path('.'))
calendar.validate_setup()
print(f'calendar_ok events_today={len(calendar.list_today_events())}')
