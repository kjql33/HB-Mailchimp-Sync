"""
Base HTTP client with retry/backoff, rate limiting, and circuit breaker.
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


class CircuitState(str, Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreaker:
    """
    Circuit breaker to prevent cascading failures.
    
    After threshold failures, opens circuit and rejects requests.
    After timeout, enters half-open state to test recovery.
    """
    threshold: int = 5  # Failures before opening
    timeout: float = 60.0  # Seconds before trying half-open
    
    state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    failures: int = field(default=0, init=False)
    last_failure_time: Optional[float] = field(default=None, init=False)
    
    def record_success(self):
        """Record successful request."""
        if self.state == CircuitState.HALF_OPEN:
            logger.info("Circuit breaker recovered, closing")
            self.state = CircuitState.CLOSED
        self.failures = 0
        self.last_failure_time = None
    
    def record_failure(self):
        """Record failed request."""
        self.failures += 1
        self.last_failure_time = time.time()
        
        if self.failures >= self.threshold and self.state == CircuitState.CLOSED:
            logger.warning(f"Circuit breaker opened after {self.failures} failures")
            self.state = CircuitState.OPEN
    
    def allow_request(self) -> bool:
        """Check if request should be allowed."""
        if self.state == CircuitState.CLOSED:
            return True
        
        if self.state == CircuitState.OPEN:
            # Check if timeout expired
            if self.last_failure_time and (time.time() - self.last_failure_time) >= self.timeout:
                logger.info("Circuit breaker timeout expired, entering half-open state")
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        
        # HALF_OPEN: allow one test request
        return True


@dataclass
class TokenBucket:
    """
    Token bucket rate limiter.
    
    Allows burst traffic up to bucket capacity, then enforces rate limit.
    """
    rate: float  # Tokens per second
    capacity: float  # Max tokens in bucket
    
    tokens: float = field(init=False)
    last_update: float = field(init=False)
    
    def __post_init__(self):
        self.tokens = self.capacity
        self.last_update = time.time()
    
    async def acquire(self, tokens: float = 1.0):
        """Acquire tokens, waiting if necessary."""
        while True:
            now = time.time()
            elapsed = now - self.last_update
            
            # Refill tokens
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_update = now
            
            if self.tokens >= tokens:
                self.tokens -= tokens
                return
            
            # Wait for tokens
            wait_time = (tokens - self.tokens) / self.rate
            await asyncio.sleep(wait_time)


class HTTPBaseClient:
    """
    Base HTTP client with resilience features:
    - Exponential backoff with jitter
    - Rate limiting (token bucket)
    - Circuit breaker
    - Request/response logging
    """
    
    def __init__(
        self,
        service_name: str,
        base_url: str,
        max_retries: int = 5,
        rate_limit: Optional[float] = None,  # requests per second
        rate_burst: Optional[float] = None,  # burst capacity
        circuit_threshold: int = 5,
        circuit_timeout: float = 60.0
    ):
        self.service_name = service_name
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.default_headers: Dict[str, str] = {}  # Child classes can set this
        
        self.session: Optional[aiohttp.ClientSession] = None
        self.circuit_breaker = CircuitBreaker(threshold=circuit_threshold, timeout=circuit_timeout)
        
        # Rate limiter (optional)
        self.rate_limiter: Optional[TokenBucket] = None
        if rate_limit:
            capacity = rate_burst if rate_burst else rate_limit * 2
            self.rate_limiter = TokenBucket(rate=rate_limit, capacity=capacity)
    
    async def __aenter__(self):
        """Async context manager entry."""
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self.session:
            await self.session.close()
    
    def _calculate_backoff(self, attempt: int) -> float:
        """
        Exponential backoff with jitter: 1s → 2s → 4s → 8s → 16s → 32s (max)
        Jitter: ±20% randomization to prevent thundering herd
        """
        delay = min(1.0 * (2.0 ** attempt), 32.0)
        jitter = delay * 0.2 * (random.random() * 2 - 1)  # ±20%
        return max(0, delay + jitter)
    
    async def request_json(
        self,
        method: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Make HTTP request expecting JSON response.
        
        Returns:
            Dict with: {"status": int, "headers": dict, "data": parsed_json, "url": str, "method": str}
        """
        return await self._request(method, path, headers, expect_json=True, **kwargs)
    
    async def request_text(
        self,
        method: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Make HTTP request expecting text response.
        
        Returns:
            Dict with: {"status": int, "headers": dict, "data": str, "url": str, "method": str}
        """
        return await self._request(method, path, headers, expect_json=False, **kwargs)
    
    async def _request(
        self,
        method: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        expect_json: bool = True,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Make HTTP request with retry/backoff and circuit breaker.
        
        CRITICAL: Reads response body INSIDE the context manager to ensure
        the connection is not closed before body is read.
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            path: URL path (will be appended to base_url)
            headers: Optional headers dict (merged with default_headers)
            expect_json: If True, parse as JSON; if False, return as text
            **kwargs: Additional args passed to aiohttp request
            
        Returns:
            Dict with: {"status": int, "headers": dict, "data": (dict|list|str|None), "url": str, "method": str}
            NEVER returns aiohttp response object.
            
        Raises:
            aiohttp.ClientError: After max retries exhausted
            RuntimeError: Circuit breaker open
        """
        if not self.session:
            raise RuntimeError("Client not initialized (use async with)")
        
        # Circuit breaker check
        if not self.circuit_breaker.allow_request():
            raise RuntimeError(f"{self.service_name} circuit breaker OPEN")
        
        # Rate limiting
        if self.rate_limiter:
            await self.rate_limiter.acquire()
        
        # Merge headers (request headers override defaults)
        merged_headers = {**self.default_headers, **(headers or {})}
        
        url = f"{self.base_url}{path}"
        
        for attempt in range(self.max_retries):
            try:
                logger.debug(f"{self.service_name} {method} {path} (attempt {attempt + 1}/{self.max_retries})")
                
                async with self.session.request(method, url, headers=merged_headers, **kwargs) as response:
                    # Store status and headers before body read
                    status = response.status
                    response_headers = dict(response.headers)
                    
                    # Handle 429 rate limit
                    if status == 429:
                        retry_after = response_headers.get("Retry-After")
                        if retry_after:
                            wait_time = float(retry_after)
                            logger.warning(f"{self.service_name} rate limited, waiting {wait_time}s")
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            # No Retry-After header, use exponential backoff
                            wait_time = self._calculate_backoff(attempt)
                            logger.warning(f"{self.service_name} rate limited (no Retry-After), "
                                         f"backing off {wait_time:.1f}s")
                            await asyncio.sleep(wait_time)
                            continue
                    
                    # Handle 5xx server errors (transient)
                    if 500 <= status < 600:
                        if attempt < self.max_retries - 1:
                            wait_time = self._calculate_backoff(attempt)
                            logger.warning(f"{self.service_name} {status} error, "
                                         f"retrying in {wait_time:.1f}s")
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            # Last attempt - read error body for debugging
                            error_body = await response.text()
                            raise aiohttp.ClientResponseError(
                                request_info=response.request_info,
                                history=response.history,
                                status=status,
                                message=f"Server error: {error_body[:200]}"
                            )
                    
                    # 4xx client errors (non-retryable, except 429 handled above)
                    if 400 <= status < 500:
                        error_body = await response.text()
                        if status == 404:
                            # 404 is often expected (e.g., member not found)
                            # Return it as normal response with empty data
                            data = None if expect_json else error_body
                        else:
                            # Other 4xx errors - DO NOT trip circuit breaker (expected errors)
                            # Circuit breaker is for service failures, not validation errors
                            self.circuit_breaker.record_success()  # Reset circuit breaker
                            
                            # Check if this is a compliance error - don't retry
                            if "compliance state" in error_body.lower() or "member in compliance" in error_body.lower():
                                logger.warning(f"{self.service_name} compliance state error (not retrying)")
                                raise aiohttp.ClientResponseError(
                                    request_info=response.request_info,
                                    history=response.history,
                                    status=status,
                                    message=f"Client error: {error_body[:200]}"
                                )
                            
                            # Now raise the error
                            raise aiohttp.ClientResponseError(
                                request_info=response.request_info,
                                history=response.history,
                                status=status,
                                message=f"Client error: {error_body[:200]}"
                            )
                    elif status < 400:
                        # Success (2xx/3xx) - read body INSIDE context
                        if status == 204:
                            # No content
                            data = None
                        elif expect_json:
                            try:
                                data = await response.json()
                            except Exception as e:
                                text = await response.text()
                                logger.error(f"Failed to parse JSON: {e}, body: {text[:200]}")
                                raise ValueError(f"Invalid JSON response: {text[:200]}")
                        else:
                            data = await response.text()
                    else:
                        # Unexpected status code
                        error_body = await response.text()
                        raise aiohttp.ClientResponseError(
                            request_info=response.request_info,
                            history=response.history,
                            status=status,
                            message=f"Unexpected status: {error_body[:200]}"
                        )
                    
                    # Success - record and return
                    self.circuit_breaker.record_success()
                    return {
                        "status": status,
                        "headers": response_headers,
                        "data": data,
                        "url": url,
                        "method": method
                    }
            
            except aiohttp.ClientError as e:
                error_msg = str(e)
                
                # Compliance errors: don't retry, don't trip circuit breaker
                if "compliance state" in error_msg.lower() or "member in compliance" in error_msg.lower():
                    logger.warning(f"{self.service_name} compliance state error (not retrying)")
                    self.circuit_breaker.record_success()  # Don't trip circuit breaker
                    raise  # Raise immediately without retry
                
                # Other errors: record failure and retry
                logger.warning(f"{self.service_name} request failed: {e}")
                self.circuit_breaker.record_failure()
                
                if attempt < self.max_retries - 1:
                    wait_time = self._calculate_backoff(attempt)
                    logger.info(f"Retrying in {wait_time:.1f}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"{self.service_name} max retries exhausted")
                    raise
        
        raise RuntimeError(f"{self.service_name} request failed after {self.max_retries} attempts")
    
    async def get(self, path: str, **kwargs) -> Dict[str, Any]:
        """GET request, returns JSON."""
        return await self.request_json("GET", path, **kwargs)
    
    async def post(self, path: str, **kwargs) -> Dict[str, Any]:
        """POST request, returns JSON."""
        return await self.request_json("POST", path, **kwargs)
    
    async def put(self, path: str, **kwargs) -> Dict[str, Any]:
        """PUT request, returns JSON."""
        return await self.request_json("PUT", path, **kwargs)
    
    async def patch(self, path: str, **kwargs) -> Dict[str, Any]:
        """PATCH request, returns JSON."""
        return await self.request_json("PATCH", path, **kwargs)
    
    async def delete(self, path: str, **kwargs) -> Dict[str, Any]:
        """DELETE request, returns JSON."""
        return await self.request_json("DELETE", path, **kwargs)

