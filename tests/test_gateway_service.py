from assistant.hermes_client import FakeHermesClient
from assistant.limits import DailyLimitStore
from assistant.pipeline import AssistantPipeline
from gateway_bot.service import GatewayService


def make_service(*, allowed: set[int] | None = None) -> GatewayService:
    return GatewayService(
        pipeline=AssistantPipeline(FakeHermesClient(), DailyLimitStore()),
        allowed_tg_user_ids=allowed,
    )


def test_gateway_service_preserves_memory_between_messages() -> None:
    service = make_service()
    saved = service.handle_text(1001, "/remember важная мысль")
    listed = service.handle_text(1001, "/memories")

    assert "Сохранил" in saved.text
    assert "важная мысль" in listed.text


def test_gateway_service_blocks_user_not_in_allowlist() -> None:
    service = make_service(allowed={1001})
    reply = service.handle_text(2002, "/ask привет")
    assert reply.blocked_reason == "user_not_allowed"
    assert "закрыт" in reply.text


def test_gateway_service_allows_user_in_allowlist() -> None:
    service = make_service(allowed={1001})
    reply = service.handle_text(1001, "/ask привет")
    assert reply.blocked_reason is None
    assert "привет" in reply.text


def test_admin_status_requires_admin() -> None:
    service = make_service()
    reply = service.handle_text(1001, "/admin_status")
    assert reply.blocked_reason == "admin_required"


def test_admin_status_for_admin() -> None:
    service = GatewayService(
        pipeline=AssistantPipeline(FakeHermesClient(), DailyLimitStore()),
        admin_tg_user_ids={1001},
    )
    reply = service.handle_text(1001, "/admin_status")
    assert reply.blocked_reason is None
    assert "Admin status" in reply.text


def test_telegram_app_imports_without_aiogram_runtime() -> None:
    import gateway_bot.telegram_app as telegram_app

    assert telegram_app.START_TEXT


def test_handle_local_text_preserves_process_state() -> None:
    import gateway_bot.main as gateway_main

    gateway_main._gateway_service = None
    assert "Сохранил" in gateway_main.handle_local_text(3003, "/remember локальная память")
    assert "локальная память" in gateway_main.handle_local_text(3003, "/memories")


def test_handle_local_plain_text_goes_to_ai_by_default(tmp_path) -> None:
    import gateway_bot.main as gateway_main

    gateway_main._gateway_service = None
    gateway_main._session_factory = None
    object.__setattr__(gateway_main.settings, "database_url", f"sqlite:///{tmp_path / 'gateway.sqlite3'}")
    object.__setattr__(gateway_main.settings, "hermes_mode", "fake")
    reply = gateway_main.handle_local_text(3004, "объясни MVP")
    assert "объясни MVP" in reply
