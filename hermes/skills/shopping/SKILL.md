---
name: shopping
description: Keep a practical personal shopping list: add items, show what is needed, mark purchases, and remove mistakes.
---

# Shopping List

Use the native shopping tools for explicit requests about what to buy. This is
not Trello and does not need a plan or an LLM call.

1. `добавь молоко в покупки` → `mcp_jarhert_native_shopping_add`. Preserve an
   optional quantity, category and named project. Use the Telegram update id as
   `idempotency_key`.
2. `что купить` / `покажи покупки` → `mcp_jarhert_native_shopping_list` with
   `needed` status.
3. `купил молоко` → first list or use the visible item id, then call
   `mcp_jarhert_native_shopping_mark_bought`.
4. `убери из покупок` means a soft removal through
   `mcp_jarhert_native_shopping_remove`; never delete database history.
5. Do not convert ordinary tasks into shopping items. For chores that repeat,
   use the existing recurring reminder tools instead.

If the same active item is added again, the store returns the existing item
instead of cluttering the list with a duplicate.
