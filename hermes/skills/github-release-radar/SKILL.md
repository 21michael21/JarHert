---
name: github-release-radar
description: Watch one public GitHub repository for release changes and notify only when the stated condition is met.
---

# GitHub Release Radar

Use only for a public GitHub repository that the owner explicitly names.

## Contract

- Input: one `owner/repository` pair, a short trigger condition, optional quiet
  hours and timezone.
- Read: `mcp_jarhert_native_monitor_list`.
- Write: `mcp_jarhert_native_monitor_add_github_releases`, then optionally
  `mcp_jarhert_native_monitor_schedule_update` after one confirmation.
- Output: monitor id, repository, condition, schedule, and whether it is
  enabled. Never claim that a release changed until the monitor records it.

## Procedure

1. Check whether the same repository and condition already have an enabled
   monitor. Reuse it rather than creating a duplicate.
2. Ask one question only if the trigger condition is missing. A default such as
   “напиши только при новом релизе” is acceptable when the user stated it.
3. Create one monitor. It uses hash/diff first; the LLM is consulted only for a
   changed release payload.
4. Set quiet hours only when the user requests them. Do not create recurring
   Calendar events or reminders for release monitoring.
5. To stop it, use `mcp_jarhert_native_monitor_disable`. Do not delete history.

## Boundaries

- This skill watches releases only. It does not scrape arbitrary pages, open
  pull requests, push code, or use GitHub write permissions.
- One repository per monitor keeps the state and resulting notification clear.
