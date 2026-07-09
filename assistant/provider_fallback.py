from __future__ import annotations

from collections.abc import Sequence

from assistant.provider_clients import HermesClient
from assistant.provider_diagnostics import HermesClientError
from assistant.quality_gates import check_output
from assistant.types import HermesRequest, HermesResponse


class FallbackHermesClient:
    def __init__(self, clients: Sequence[HermesClient]) -> None:
        self.clients = list(clients)
        if not self.clients:
            raise ValueError("FallbackHermesClient requires at least one client")

    def ask(self, request: HermesRequest) -> HermesResponse:
        failures: list[str] = []
        for index, client in enumerate(self.clients):
            try:
                response = client.ask(request)
            except HermesClientError as error:
                failures.append(str(error))
                continue
            output_gate = check_output(response.text)
            if not output_gate.ok:
                failures.append(f"{response.model}: {output_gate.reason}")
                continue
            return HermesResponse(
                text=response.text,
                provider=response.provider,
                model=response.model,
                latency_ms=response.latency_ms,
                fallback_count=index,
                fallback_reason="; ".join(failures[-2:]) if failures else response.fallback_reason,
                diagnostics=response.diagnostics,
            )
        raise HermesClientError("; ".join(failures) or "all Hermes providers failed")
