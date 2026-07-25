import json
from pathlib import Path
from src.config import load_config
from src.trello_client import TrelloClient

config = load_config(Path('.'))
trello = TrelloClient(config, Path('.'))
cards = trello.list_cards()
items = []
for card in cards[:150]:
    labels = [str(item.get('name')) for item in card.get('labels', []) if isinstance(item, dict) and item.get('name')]
    priority = next((name for name in labels if name in config.priorities), None)
    items.append({
        'id': str(card.get('id') or ''),
        'title': str(card.get('name') or ''),
        'list_name': str(card.get('listName') or ''),
        'priority': priority,
        'labels': labels,
        'due': card.get('due'),
        'url': card.get('shortUrl') or card.get('url'),
    })
board = trello.get_board()
print(json.dumps({'items': items, 'lists': [str(item.get('name')) for item in trello.get_lists()], 'priorities': list(config.priorities), 'board_url': board.get('url')}, ensure_ascii=False))
