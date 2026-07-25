import json, sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from src.config import load_config
from src.google_calendar_client import GoogleCalendarClient, _event_start

payload = json.loads(sys.argv[1])
config = load_config(Path('.'))
calendar = GoogleCalendarClient(config, Path('.'))
days = max(1, min(int(payload.get('days') or 7), 31))
tz = ZoneInfo(config.timezone)
start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
end = start + timedelta(days=days)
if config.mock:
    events = [event for event in calendar._load_store()['events'] if _event_start(event) and start <= _event_start(event) < end]
else:
    events = list(calendar.authenticate().events().list(calendarId=config.google_calendar_id, timeMin=start.isoformat(), timeMax=end.isoformat(), singleEvents=True, orderBy='startTime').execute().get('items', []))
items = []
for event in events[:150]:
    start_value = event.get('start', {}).get('dateTime') or event.get('start', {}).get('date')
    end_value = event.get('end', {}).get('dateTime') or event.get('end', {}).get('date')
    items.append({'id': str(event.get('id') or ''), 'title': str(event.get('summary') or ''), 'start': start_value, 'end': end_value, 'url': event.get('htmlLink')})
print(json.dumps({'items': items, 'days': days}, ensure_ascii=False))
