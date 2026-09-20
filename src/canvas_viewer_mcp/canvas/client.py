"""Async Canvas LMS REST client.

Two Canvas-specific behaviours drive the design here:

1. **Pagination lives in the ``Link`` header**, not the body. A caller that
   ignores ``rel="next"`` receives the first page and no indication that more
   exist. That is a silent truncation, and this project exists to stop things
   going missing, so pagination is never optional and never partial.

2. **Throttling returns 403, not 429.** Canvas uses a leaky-bucket quota and
   reports exhaustion with a 403 whose body mentions the rate limit. Treating
   every 403 as an auth failure would misdiagnose throttling as a bad token.
"""

from __future__ import annotations

import asyncio
import random
from types import TracebackType
from typing import Any

import httpx

from ..config import Config
from .errors import (
    CanvasAuthError,
    CanvasError,
    CanvasNotFoundError,
    CanvasPaginationError,
    CanvasRateLimitError,
)

DEFAULT_PER_PAGE = 100
MAX_RETRIES = 4
JsonObject = dict[str, Any]


class CanvasClient:
    """Thin async wrapper over the Canvas ``/api/v1`` surface.

    Read-only by construction: the only HTTP verb this class can issue is GET.
    """

    def __init__(
        self, config: Config, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=f"{config.base_url}/api/v1",
            headers={
                "Authorization": f"Bearer {config.token}",
                "Accept": "application/json",
            },
            timeout=config.timeout_seconds,
            follow_redirects=True,
            transport=transport,
        )

    async def __aenter__(self) -> CanvasClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- core request handling -------------------------------------------------

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return

        status = response.status_code
        body = response.text[:500]

        if status == 403 and "rate limit" in body.lower():
            raise CanvasRateLimitError(f"Canvas rate limit hit: {body}")
        if status == 401:
            raise CanvasAuthError(
                "Canvas rejected the token (401). It may be expired or revoked; "
                "regenerate it under Account -> Settings -> Approved Integrations."
            )
        if status == 403:
            raise CanvasAuthError(f"Canvas denied access (403): {body}")
        if status == 404:
            raise CanvasNotFoundError(f"Not found: {response.request.url.path}")
        raise CanvasError(f"Canvas returned {status} for {response.request.url.path}: {body}")

    async def _request(self, url: str, params: JsonObject | None = None) -> httpx.Response:
        """GET with retry on throttling and transient server errors.

        Backs off exponentially with jitter. Jitter matters because several
        tool calls often fan out concurrently against the same quota; without
        it they would retry in lockstep and collide again.
        """
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            try:
                response = await self._client.get(url, params=params)
            except httpx.TransportError as exc:
                last_error = exc
                await self._sleep_backoff(attempt)
                continue

            if response.status_code >= 500 or (
                response.status_code == 403 and "rate limit" in response.text.lower()
            ):
                last_error = CanvasError(f"transient {response.status_code}")
                if attempt < MAX_RETRIES - 1:
                    await self._sleep_backoff(attempt, response)
                    continue

            self._raise_for_status(response)
            return response

        raise CanvasError(f"Giving up after {MAX_RETRIES} attempts: {last_error}")

    @staticmethod
    async def _sleep_backoff(attempt: int, response: httpx.Response | None = None) -> None:
        if response is not None and (retry_after := response.headers.get("Retry-After")):
            try:
                await asyncio.sleep(min(float(retry_after), 30.0))
                return
            except ValueError:
                pass
        await asyncio.sleep(min(2**attempt, 8) + random.uniform(0, 0.5))

    # ---- public API ------------------------------------------------------------

    async def get(self, path: str, params: JsonObject | None = None) -> Any:
        """Fetch a single (non-paginated) resource."""
        response = await self._request(path.lstrip("/"), params)
        return response.json()

    async def paginate(self, path: str, params: JsonObject | None = None) -> list[Any]:
        """Fetch every page of a collection by following ``Link: rel="next"``.

        Raises rather than truncating if the page budget is exhausted.
        """
        query: JsonObject | None = {"per_page": DEFAULT_PER_PAGE, **(params or {})}
        url: str | None = path.lstrip("/")
        items: list[Any] = []
        pages = 0

        while url is not None:
            if pages >= self._config.max_pages:
                raise CanvasPaginationError(
                    f"{path} exceeded the {self._config.max_pages}-page budget after "
                    f"{len(items)} items. Refusing to return a partial list."
                )

            response = await self._request(url, query)
            payload = response.json()

            if not isinstance(payload, list):
                raise CanvasError(
                    f"Expected a list from {path}, got {type(payload).__name__}. "
                    "Use get() for single resources."
                )
            items.extend(payload)
            pages += 1

            # The next URL is absolute and already carries its own query string.
            # httpx REPLACES a URL's query when `params` is passed -- and an empty
            # dict still counts as passed -- so the cursor must be handed back as
            # None, not {}. With {} the cursor is silently stripped, page 1 is
            # refetched forever, and the only symptom is the page budget tripping.
            next_link = response.links.get("next", {}).get("url")
            url, query = next_link, None

        return items

    # ---- convenience accessors -------------------------------------------------

    async def current_user(self) -> JsonObject:
        result: JsonObject = await self.get("users/self")
        return result

    async def active_courses(self) -> list[JsonObject]:
        """Courses with an active enrolment, including score totals."""
        courses: list[JsonObject] = await self.paginate(
            "courses",
            {
                "enrollment_state": "active",
                "include[]": ["total_scores", "term", "course_image"],
                "state[]": ["available"],
            },
        )
        return courses
