# Google Docs webhook

> **Obsolete.** Этот документ относится к старому gateway-runtime (до
> [ADR 0002](adr/0002-hermes-native-personal-os.md)). В текущем Hermes-native
> профиле нет кода, который читает `GOOGLE_DOCS_WEBHOOK_URL/TOKEN` или
> вызывает этот webhook. Оставлено как справка на случай возврата функции.

Бот сохраняет идеи и напоминания локально всегда. Чтобы дополнительно писать их в Google Docs, сделай Google Apps Script webhook.

## 1. Создай Apps Script

Открой [script.google.com](https://script.google.com), создай проект и вставь код:

```javascript
const TOKEN = 'replace-with-long-random-token';
const DOC_ID = 'replace-with-google-doc-id';

function doPost(e) {
  const auth = e.parameter.token || '';
  if (auth !== TOKEN) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, error: 'unauthorized' }))
      .setMimeType(ContentService.MimeType.JSON);
  }

  const payload = JSON.parse(e.postData.contents || '{}');
  const kind = payload.kind || 'item';
  const text = payload.text || '';
  const createdAt = payload.created_at || new Date().toISOString();

  if (!text) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, error: 'empty text' }))
      .setMimeType(ContentService.MimeType.JSON);
  }

  const doc = DocumentApp.openById(DOC_ID);
  const body = doc.getBody();
  body.appendParagraph(`[${kind}] ${createdAt}`);
  body.appendParagraph(text);
  body.appendParagraph('');
  doc.saveAndClose();

  return ContentService
    .createTextOutput(JSON.stringify({ ok: true }))
    .setMimeType(ContentService.MimeType.JSON);
}
```

## 2. Deploy

Deploy → New deployment → Web app:

- Execute as: `Me`
- Who has access: `Anyone with the link`

Скопируй Web app URL.

## 3. Настрой `.env`

Apps Script плохо работает с кастомным `Authorization` header, поэтому для него используй token query:

```env
GOOGLE_DOCS_WEBHOOK_URL=https://script.google.com/macros/s/.../exec?token=replace-with-long-random-token
GOOGLE_DOCS_WEBHOOK_TOKEN=
```

После изменения `.env` перезапусти бота.

## 4. Проверь

В Telegram:

```text
/idea проверить запись в Google Docs
/remind через 10 минут проверить документ
```

В ответе должно появиться `Отправил в Google Docs.`
