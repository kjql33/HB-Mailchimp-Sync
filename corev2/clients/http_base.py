"""
Base HTTP client with retry/backoff, rate limiting, and circuit breaker.

Features:
- Exponential backoff with jitter (1s → 2s → 4s → 8s → 16s → 32s max)
- Token bucket rate limiting (prevents API bans)
- Circuit breaker (stops hammering a dead service)
- 400 errors are NOT retried (validation errors never resolve on retry)
- 429 rate limit respects Retry-After header
- 5xx errors are retried up to max_retries
"""

import asyncio
import logging
import time
import random
from enum import Enum
from typing import Optional, Dict, Any
from dataclasses import dataclass, field

import aiohttp

logger = logging.getLogger(__name__)

# HTTP status codes that should never be retried
_NON_RETRYABLE_4XX = {400, 401, 403, 404, 409, 410, 422}


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    threshold: int = 5
    timeout: float = 60.0
    state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    failures: int = field(default=0, init=False)
    last_failure_time: Optional[float] = field(default=None, init=False)

    def record_success(self):
        if self.state == CircuitState.HALF_OPEN:
            logger.info("Circuit breaker recovered")
            self.state = CircuitState.CLOSED
        self.failures = 0

    def record_failure(self):
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.threshold and self.state == CircuitState.CLOSED:
            logger.warning(f"Circuit breaker OPEN after {self.failures} failures")
            self.state = CircuitState.OPEN

    def allow_request(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if self.last_failure_time and (time.time() - self.last_failure_time) >= self.timeout:
                logger.info("Circuit breaker entering half-open state")
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True  # HALF_OPEN: allow one test request


@dataclass
class TokenBucket:
    rate: float
    capacity: float
    tokens: float = field(init=False)
    last_update: float = field(init=False)

    def __post_init__(self):
        self.tokens = self.capacity
        self.last_update = time.time()

    async def acquire(self, tokens: float = 1.0):
        while True:
            now = time.time()
            elapsed = now - self.last_update
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_update = now
            if self.tokens >= tokens:
                self.tokens -= tokens
                return
            wait_time = (tokens - self.tokens) / self.rate
            await asyncio.sleep(wait_time)


class HTTPBaseClient:
    """
    Production-grade async HTTP client.

    Usage:
        async with MyClient(...) as client:
            result = await client.get("/some/path")
            # result = {"status": 200, "data": {...}, "url": "...", "headers": {...}}
    """

    def __init__(
        self,
        service_name: str,
        base_url: str,
        max_retries: int = 5,
        rate_limit: Optional[float] = None,
        rate_burst: Optional[float] = None,
        circuit_threshold: int = 5,
        circuit_timeout: float = 60.0,
    ):
        self.service_name = service_name
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.default_headers: Dict[str, str] = {}
        self.session: Optional[aiohttp.ClientSession] = None
        self.circuit_breaker = CircuitBreaker(
            threshold=circuit_threshold, timeout=circuit_timeout
        )
        self.rate_limiter: Optional[TokenBucket] = None
        if rate_limit:
            capacity = rate_burst if rate_burst else rate_limit * 2
            self.rate_limiter = TokenBucket(rate=rate_limit, capacity=capacity)

    async def __aenter__(self):
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        self.session = aiohttp.ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff with ±20% jitter."""
        delay = min(1.0 * (2.0 ** attempt), 32.0)
        jitter = delay * 0.2 * (random.random() * 2 - 1)
        return max(0.1, delay + jitter)

    async def _request(
        self,
        method: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        expect_json: bool = True,
        **kwargs,
    ) -> Dict[str, Any]:
        if not self.session:
            raise RuntimeError(f"{self.service_name}: client not initialised (use async with)")

        if not self.circuit_breaker.allow_request():
            raise RuntimeError(f"{self.service_name} circuit breaker OPEN - service unavailable")

        if self.rate_limiter:
            await self.rate_limiter.acquire()

        merged_headers = {**self.default_headers, **(headers or {})}
        url = f"{self.base_url}{path}"

        for attempt in range(self.max_retries):
            try:
                async with self.session.request(
                    method, url, headers=merged_headers, **kwargs
                ) as response:
                    status = response.status
                    response_headers = dict(response.headers)

                    # -- 429 Rate limited ----------------------------------
                    if status == 429:
                        retry_after = float(response_headers.get("Retry-After", self._backoff(attempt)))
                        logger.warning(f"{self.service_name} rate limited - waiting {retry_after:.1f}s")
                        await asyncio.sleep(retry_after)
                        continue

                    # -- 5xx transient server errors (retryable) -----------
                    if 500 <= status < 600:
                        if attempt < self.max_retries - 1:
                            wait = self._backoff(attempt)
                            logger.warning(f"{self.service_name} {status} error, retrying in {wait:.1f}s")
                            await asyncio.sleep(wait)
                            continue
                        body = await response.text()
                        self.circuit_breaker.record_failure()
                        raise aiohttp.ClientResponseError(
                            request_info=response.request_info,
                            history=response.history,
                            status=status,
                            message=f"Server error after {self.max_retries} attempts: {body[:300]}",
                        )

                    # -- 4xx client errors (non-retryable) -----------------
                    if 400 <= status < 500:
                        body = await response.text()
                        # 404 is often expected - return structured response
                        if status == 404:
                            return {"status": 404, "headers": response_headers, "data": None, "url": url, "method": method}
                        # All other 4xx: raise immediately (retrying never helps)
                        self.circuit_breaker.record_success()  # Don't penalise circuit
                        raise aiohttp.ClientResponseError(
                            request_info=response.request_info,
                            history=response.history,
                            status=status,
                            message=f"Client error: {body[:300]}",
                        )

                    # -- 2xx/3xx success ------------------------------------
                    if status == 204:
                        data = None
                    elif expect_json:
                        try:
                            data = await response.json(content_type=None)
                        except Exception:
                            text = await response.text()
                            raise ValueError(f"{self.service_name}: invalid JSON response: {text[:200]}")
                    else:
                        data = await response.text()

                    self.circuit_breaker.record_success()
                    return {"status": status, "headers": response_headers, "data": data, "url": url, "method": method}

            except aiohttp.ClientResponseError:
                raise  # Already handled above

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                self.circuit_breaker.record_failure()
                logger.warning(f"{self.service_name} request failed ({type(e).__name__}): {e}")
                if attempt < self.max_retries - 1:
                    wait = self._backoff(attempt)
                    logger.info(f"Retrying in {wait:.1f}s... (attempt {attempt + 2}/{self.max_retries})")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"{self.service_name} max retries exhausted")
                    raise

        raise RuntimeError(f"{self.service_name}: request failed after {self.max_retries} attempts")

    async def request_json(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        return await self._request(method, path, expect_json=True, **kwargs)

    async def get(self, path: str, **kwargs) -> Dict[str, Any]:
        return await self.request_json("GET", path, **kwargs)

    async def post(self, path: str, **kwargs) -> Dict[str, Any]:
        return await self.request_json("POST", path, **kwargs)

    async def put(self, path: str, **kwargs) -> Dict[str, Any]:
        return await self.request_json("PUT", path, **kwargs)

    async def patch(self, path: str, **kwargs) -> Dict[str, Any]:
        return await self.request_json("PATCH", path, **kwargs)

    async def delete(self, path: str, **kwargs) -> Dict[str, Any]:
        return await self.request_json("DELETE", path, **kwargs)
