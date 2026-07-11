---
name: trips
description: Organize one personal trip using factual routes, bookings, documents, checklists, and linked Telegram reminders.
---

# Trips

Use native trip tools when the user wants to organize their own travel. Keep
facts separate from advice: do not invent bookings, document requirements, or
dates that the user did not state.

1. `создай поездку в Амстердам` → `mcp_jarhert_native_trip_create`. Preserve
   the destination and timezone-aware dates when provided.
2. Add a route, booking, document or checklist point with
   `mcp_jarhert_native_trip_add_item`. A stated deadline becomes one normal
   Telegram reminder; it is not a separate calendar event.
3. `покажи поездку` → `mcp_jarhert_native_trip_details`; `какие поездки` →
   `mcp_jarhert_native_trip_list`.
4. Mark finished points with `mcp_jarhert_native_trip_item_complete`; it only
   cancels the linked reminder for that point.
5. Cancelling an entire trip uses `mcp_jarhert_native_trip_cancel_confirmed`
   and requires one clear confirmation because it cancels its pending reminders.

Do not buy tickets, access external booking accounts, or send itinerary data to
other people. This workspace is local planning only.
