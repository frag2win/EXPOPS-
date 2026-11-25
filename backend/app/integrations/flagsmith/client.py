"""Minimal asynchronous client for verified Flagsmith Management API reads."""

from urllib.parse import quote, urljoin, urlparse

import httpx

from app.integrations.flagsmith.exceptions import (
    FlagsmithAuthenticationError,
    FlagsmithConfigurationError,
    FlagsmithInvalidResponseError,
    FlagsmithNotFoundError,
    FlagsmithPermissionError,
    FlagsmithRateLimitError,
    FlagsmithUnavailableError,
)


class FlagsmithClient:
    """Authenticated HTTP client; credentials and transport details stay private."""

    def __init__(self, base_url: str, api_key: str | None, timeout: float = 10.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key.strip() if api_key else ""
        self.timeout = timeout
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _client(self) -> httpx.AsyncClient:
        if not self.configured:
            raise FlagsmithConfigurationError("Flagsmith API credentials are not configured")
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Api-Key {self._api_key}", "Accept": "application/json"},
            timeout=httpx.Timeout(self.timeout),
            transport=self._transport,
        )

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if status in (401, 403):
            if status == 401:
                raise FlagsmithAuthenticationError("Flagsmith rejected the configured credential")
            raise FlagsmithPermissionError("Flagsmith denied access to the requested resource")
        if status == 404:
            raise FlagsmithNotFoundError("The requested Flagsmith resource was not found")
        if status == 429:
            raise FlagsmithRateLimitError("Flagsmith rate limit was reached")
        if status >= 500:
            raise FlagsmithUnavailableError("Flagsmith is temporarily unavailable")
        if status >= 400:
            raise FlagsmithInvalidResponseError("Flagsmith rejected the read request")

    async def get_json(self, path: str) -> object:
        """Fetch JSON from a fixed, internally constructed relative API path."""
        if path.startswith(("http://", "https://")) or ".." in path.split("/"):
            raise ValueError("Only fixed relative Flagsmith API paths are allowed")
        try:
            async with self._client() as client:
                response = await client.get(path)
        except httpx.TimeoutException as exc:
            raise FlagsmithUnavailableError("Flagsmith request timed out") from exc
        except httpx.RequestError as exc:
            raise FlagsmithUnavailableError("Could not connect to Flagsmith") from exc
        self._raise_for_status(response)
        try:
            return response.json()
        except ValueError as exc:
            raise FlagsmithInvalidResponseError("Flagsmith returned invalid JSON") from exc

    async def get_all(self, path: str) -> list[object]:
        """Read every page, accepting the API's array or paginated response form."""
        items: list[object] = []
        next_url: str | None = path
        origin = urlparse(self.base_url)
        while next_url:
            target = next_url if next_url.startswith("https://") or next_url.startswith("http://") else urljoin(self.base_url + "/", next_url.lstrip("/"))
            parsed = urlparse(target)
            if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc) or not parsed.path.startswith("/api/v1/"):
                raise FlagsmithInvalidResponseError("Flagsmith pagination returned an unsafe next link")
            payload = await self.get_json(parsed.path + ("?" + parsed.query if parsed.query else ""))
            if isinstance(payload, list):
                items.extend(payload)
                next_url = None
            elif isinstance(payload, dict) and isinstance(payload.get("results"), list):
                items.extend(payload["results"])
                next_url = payload.get("next") if isinstance(payload.get("next"), str) else None
            else:
                raise FlagsmithInvalidResponseError("Flagsmith returned an unexpected list response")
        return items

    @staticmethod
    def path_segment(value: str) -> str:
        """Encode an upstream identifier as a single URL path segment."""
        return quote(value, safe="")

    async def close(self) -> None:
        """Stateless client; provided for adapter lifecycle symmetry."""
        return None
