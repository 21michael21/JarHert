from assistant.tool_result_ids import compact_result_meta, extract_tool_result_ids


def test_extracts_trello_and_calendar_ids_from_command_output() -> None:
    output = (
        "Created Trello card card_id=64fabc123def4567890abcde "
        "https://trello.com/c/abc12345/title\n"
        "Created calendar event calendar_event_id=event_123456 "
        "https://calendar.google.com/calendar/event?eid=abc"
    )

    meta = extract_tool_result_ids(output)

    assert meta["trello_card_id"] == "64fabc123def4567890abcde"
    assert meta["trello_card_url"].startswith("https://trello.com/c/")
    assert meta["calendar_event_id"] == "event_123456"
    assert meta["calendar_event_url"].startswith("https://calendar.google.com/")


def test_compacts_result_meta_for_status_lines() -> None:
    assert compact_result_meta({"trello_card_id": "abc123"}) == "trello_card_id=abc123"
