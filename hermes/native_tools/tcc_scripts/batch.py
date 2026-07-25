import json, sys
from pathlib import Path
from src.config import load_config
from src.date_parser import parse_date, parse_datetime
from src.google_calendar_client import GoogleCalendarClient
from src.models import CalendarEventInput, TaskCardInput
from src.trello_client import TrelloClient

payload = json.loads(sys.argv[1])
config = load_config(Path('.'))
trello = None
calendar = None

def get_trello():
    global trello
    if trello is None:
        trello = TrelloClient(config, Path('.'))
    return trello

def get_calendar():
    global calendar
    if calendar is None:
        calendar = GoogleCalendarClient(config, Path('.'))
        if not config.mock:
            service = calendar.authenticate()
            calendar.authenticate = lambda: service
    return calendar

def task_result(card):
    parts = [f"trello_card_id={card.get('id')}"]
    if card.get('shortUrl') or card.get('url'):
        parts.append(str(card.get('shortUrl') or card.get('url')))
    return "\n".join(parts)

def calendar_result(event):
    parts = [f"calendar_event_id={event.get('id')}"]
    if event.get('htmlLink'):
        parts.append(str(event.get('htmlLink')))
    return "\n".join(parts)

results = []
for action in payload['actions']:
    kind = action['type']; data = action['payload']
    try:
        if kind == 'task.create':
            card = get_trello().create_card(TaskCardInput(
                title=data['title'], project=data.get('project'), priority=data.get('priority'),
                list_name=data.get('list_name') or 'Inbox', due=parse_date(data.get('due')),
                description=data.get('description') or '', criteria=[],
            ))
            result = task_result(card)
        elif kind == 'task.move':
            client = get_trello(); result = task_result(client.move_card(client.find_card_by_name(data['title']), data['target_list']))
        elif kind == 'task.priority':
            client = get_trello(); card = client.find_card_by_name(data['title'])
            priority = str(data['priority'])
            if priority not in config.priorities: raise ValueError('Unknown priority.')
            priority_names = set(config.priorities)
            remaining = [item for item in (card.get('labels') or []) if not (isinstance(item, dict) and str(item.get('name')) in priority_names)]
            new_label = next(item for item in client.get_labels() if str(item.get('name')) == priority)
            labels = [*remaining, new_label]
            if config.mock:
                updated = client.update_card(card, labels=labels)
            else:
                updated = client.update_card(card, idLabels=','.join(str(item['id']) for item in labels))
            result = task_result(updated)
        elif kind == 'task.done':
            client = get_trello(); card = client.find_card_by_name(data['title'])
            client.add_comment(card, f"Done summary: {data.get('summary') or 'Готово.'}")
            result = task_result(client.move_card(card, 'Done'))
        elif kind == 'task.delete':
            client = get_trello(); card = client.find_card_by_name(data['title']); client.delete_card(card)
            result = task_result(card)
        elif kind == 'calendar.create':
            event = get_calendar().create_event(CalendarEventInput(
                title=data['title'], start=parse_datetime(data['start'], config.timezone),
                end=parse_datetime(data['end'], config.timezone), reminder_minutes=data.get('reminder_minutes'),
                description=data.get('description') or '',
            ))
            result = calendar_result(event)
        elif kind == 'calendar.move':
            client = get_calendar(); event = client.find_event_by_title(data['title']); event_id = str(event['id'])
            body = client.build_event_body(CalendarEventInput(
                title=str(event.get('summary') or data['title']), start=parse_datetime(data['start'], config.timezone),
                end=parse_datetime(data['end'], config.timezone), reminder_minutes=None,
                description=str(event.get('description') or ''),
            ))
            if config.mock:
                store = client._load_store(); moved = next(item for item in store['events'] if str(item.get('id')) == event_id)
                moved.update(body); moved['id'] = event_id; moved['htmlLink'] = event.get('htmlLink'); client._save_store(store)
            else:
                moved = client.authenticate().events().update(calendarId=config.google_calendar_id, eventId=event_id, body=body).execute()
            result = calendar_result(moved)
        elif kind == 'calendar.delete':
            result = calendar_result(get_calendar().delete_event_by_title(data['title']))
        else:
            raise ValueError(f"Unsupported action: {kind}")
        results.append({'ok': True, 'result': result})
    except Exception as error:
        results.append({'ok': False, 'error': str(error)[:500] or type(error).__name__})
print(json.dumps(results, ensure_ascii=False, separators=(',', ':')))
