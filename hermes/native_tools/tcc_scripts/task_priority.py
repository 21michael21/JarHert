import json, sys
from pathlib import Path
from src.config import load_config
from src.formatter import format_card
from src.trello_client import TrelloClient

payload = json.loads(sys.argv[1])
config = load_config(Path('.'))
priority = str(payload['priority'])
if priority not in config.priorities:
    raise SystemExit('Unknown priority.')
trello = TrelloClient(config, Path('.'))
card = trello.find_card_by_name(payload['title'])
priority_names = set(config.priorities)
existing_labels = card.get('labels') or []
remaining = [item for item in existing_labels if not (isinstance(item, dict) and str(item.get('name')) in priority_names)]
new_label = next(item for item in trello.get_labels() if str(item.get('name')) == priority)
labels = [*remaining, new_label]
if config.mock:
    updated = trello.update_card(card, labels=labels)
else:
    updated = trello.update_card(card, idLabels=','.join(str(item['id']) for item in labels))
print(format_card(updated))
print('trello_card_id=' + str(updated.get('id') or card.get('id')))
if updated.get('shortUrl') or updated.get('url'):
    print(str(updated.get('shortUrl') or updated.get('url')))
