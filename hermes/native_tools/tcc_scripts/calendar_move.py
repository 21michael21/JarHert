import json, sys
from pathlib import Path
from src.config import load_config
from src.date_parser import parse_datetime
from src.formatter import format_event
from src.google_calendar_client import GoogleCalendarClient
from src.models import CalendarEventInput
payload = json.loads(sys.argv[1]); config = load_config(Path('.')); calendar = GoogleCalendarClient(config, Path('.'))
event = calendar.find_event_by_title(payload['title'])
body = calendar.build_event_body(CalendarEventInput(title=str(event.get('summary') or payload['title']), start=parse_datetime(payload['start'], config.timezone), end=parse_datetime(payload['end'], config.timezone), reminder_minutes=None, description=str(event.get('description') or '')))
event_id = str(event['id'])
if config.mock:
    store = calendar._load_store(); moved = next((item for item in store['events'] if str(item.get('id')) == event_id), None)
    if moved is None: raise SystemExit('Calendar event not found.')
    moved.update(body); moved['id'] = event_id; moved['htmlLink'] = event.get('htmlLink'); calendar._save_store(store)
else:
    moved = calendar.authenticate().events().update(calendarId=config.google_calendar_id, eventId=event_id, body=body).execute()
print(format_event(moved)); print(f"calendar_event_id={moved.get('id')}")
if moved.get('htmlLink'): print(moved.get('htmlLink'))
