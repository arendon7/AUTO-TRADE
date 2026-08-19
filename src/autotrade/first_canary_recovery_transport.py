from __future__ import annotations

from dataclasses import dataclass

from autotrade.brokers.alpaca_paper_crypto_reconciliation import _ExactReadPolicy
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperHttpResponse,
    AlpacaPaperReadRequest,
    AlpacaPaperUnavailable,
    UrllibAlpacaPaperReadTransport,
)


@dataclass(frozen=True, slots=True)
class FirstCanaryRecoveryReadTransport:
    """GET-only adapter that preserves broker HTTP 404 as reconciliation evidence.

    urllib raises HTTPError for 404 before the crypto reconciler can inspect the
    status code. The hardened PAPER transport deliberately wraps that exception
    as AlpacaPaperUnavailable. This adapter unwraps *only* an exact urllib 404,
    re-validates the exact request/final URL, bounds the body, and returns it as
    immutable read evidence. It owns no writer/POST/retry authority.
    """

    max_response_bytes: int = 128 * 1024

    def __post_init__(self) -> None:
        if not 1 <= self.max_response_bytes <= 1_048_576:
            raise ValueError("max_response_bytes must be between 1 and 1048576")

    def read(self, request: AlpacaPaperReadRequest) -> AlpacaPaperHttpResponse:
        policy = _ExactReadPolicy(request.url)
        policy.validate(request)
        transport = UrllibAlpacaPaperReadTransport(
            policy=policy,  # type: ignore[arg-type]
            max_response_bytes=self.max_response_bytes,
        )
        try:
            return transport.read(request)
        except AlpacaPaperUnavailable as exc:
            cause = exc.__cause__
            if (
                cause is None
                or type(cause).__module__ != "urllib.error"
                or type(cause).__name__ != "HTTPError"
                or getattr(cause, "code", None) != 404
            ):
                raise
            final_url = str(cause.geturl())
            policy.validate_final_url(final_url)
            body = cause.read(self.max_response_bytes + 1)
            if len(body) > self.max_response_bytes:
                raise AlpacaPaperUnavailable("PAPER recovery response exceeded size limit") from exc
            headers_obj = getattr(cause, "headers", None)
            if headers_obj is None:
                raise AlpacaPaperUnavailable("PAPER recovery 404 lacks response headers") from exc
            return AlpacaPaperHttpResponse(
                status_code=404,
                body=body,
                final_url=final_url,
                headers={key.lower(): value for key, value in headers_obj.items()},
            )
