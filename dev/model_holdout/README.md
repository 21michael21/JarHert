# Закрытый holdout для сравнения моделей

Сгенерируй локальный файл один раз:

```bash
.venv/bin/python scripts/generate_model_holdout.py
```

`holdout.json` игнорируется Git. Не добавляй его в fine-tune, style distillation,
prompt-профиль или обычные тестовые фикстуры. После начала серии сравнений не меняй
его между вариантами: только так latency и качество остаются сопоставимыми.
