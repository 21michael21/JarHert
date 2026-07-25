import json, sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from src.config import load_config
from src.formatter import format_event
from src.google_calendar_client import GoogleCalendarClient, _event_start
payload = json.loads(sys.argv[1])
config = load_config(Path('.'))
calendar = GoogleCalendarClient(config, Path('.'))
when = str(payload.get('when') or 'today').strip().lower()
tz = ZoneInfo(config.timezone)
day = date.today() + (timedelta(days=1) if when in {'tomorrow', 'завтра'} else timedelta())
start = datetime.combine(day, time.min, tz)
end = start + timedelta(days=1)
if config.mock:
    events = [event for event in calendar._load_store()['events'] if _event_start(event) and start <= _event_start(event) < end]
else:
    events = list(calendar.authenticate().events().list(calendarId=config.google_calendar_id, timeMin=start.isoformat(), timeMax=end.isoformat(), singleEvents=True, orderBy='startTime').execute().get('items', []))
print('No events found.' if not events else '\n'.join(format_event(event) for event in events))
