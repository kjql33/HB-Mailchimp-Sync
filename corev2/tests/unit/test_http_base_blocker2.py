"""Unit tests for HTTP base client - specifically testing Blocker 2 fix."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from corev2.clients.http_base import HTTPBaseClient
import aiohttp


class FakeClosedResponse:
    """Mock response that tracks if it's been closed and fails if read after close."""
    
    def __init__(self, status=200, json_data=None, text_data=None):
        self.status = status
        self.headers = {"Content-Type": "application/json"}
        self.request_info = MagicMock()
        self.history = []
        self._json_data = json_data or {"test": "data"}
        self._text_data = text_data or "test text"
        self._closed = False
    
    async def json(self):
        if self._closed:
            raise RuntimeError("Cannot read from closed response!")
        return self._json_data
    
    async def text(self):
        if self._closed:
            raise RuntimeError("Cannot read from closed response!")
        return self._text_data
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, *args):
        self._closed = True  # Mark as closed when context exits


@pytest.mark.asyncio
async def test_request_never_returns_response_object():
    """CRITICAL: Verify _request never returns aiohttp response object."""
    client = HTTPBaseClient(
        service_name="Test",
        base_url="https://api.test.com",
        rate_limit=100.0
    )
    
    fake_response = FakeClosedResponse(status=200, json_data={"key": "value"})
    
    async with client:
        with patch.object(client.session, 'request', return_value=fake_response):
            result = await client.request_json("GET", "/test")
            
            # Verify result structure
            assert "status" in result
            assert "headers" in result
            assert "data" in result
            assert "url" in result
            assert "method" in result
            
            # CRITICAL: Verify no response object leaked
            assert "response" not in result
            
            # Verify data is already parsed (not a response object)
            assert isinstance(result["data"], dict)
            assert result["data"]["key"] == "value"
            
            # Verify we CAN'T accidentally read from response after return
            # (because response should be closed and data already extracted)
            assert fake_response._closed is True


@pytest.mark.asyncio
async def test_request_reads_body_inside_context():
    """Verify body is read INSIDE the async with block (before close)."""
    client = HTTPBaseClient(
        service_name="Test",
        base_url="https://api.test.com",
        rate_limit=100.0
    )
    
    fake_response = FakeClosedResponse(status=200, json_data={"test": "success"})
    
    async with client:
        with patch.object(client.session, 'request', return_value=fake_response):
            # This should succeed because body is read inside context
            result = await client.request_json("GET", "/test")
            
            assert result["data"]["test"] == "success"
            # Response is now closed
            assert fake_response._closed is True


@pytest.mark.asyncio
async def test_404_returns_none_data_not_response_object():
    """Verify 404 returns None data, not a response object."""
    client = HTTPBaseClient(
        service_name="Test",
        base_url="https://api.test.com",
        rate_limit=100.0
    )
    
    fake_response = FakeClosedResponse(status=404, text_data="Not Found")
    
    async with client:
        with patch.object(client.session, 'request', return_value=fake_response):
            result = await client.request_json("GET", "/test")
            
            assert result["status"] == 404
            assert result["data"] is None  # 404 returns None, not response object
            assert "response" not in result


@pytest.mark.asyncio
async def test_204_no_content_returns_none():
    """Verify 204 No Content returns None data."""
    client = HTTPBaseClient(
        service_name="Test",
        base_url="https://api.test.com",
        rate_limit=100.0
    )
    
    fake_response = FakeClosedResponse(status=204)
    
    async with client:
        with patch.object(client.session, 'request', return_value=fake_response):
            result = await client.request_json("POST", "/test")
            
            assert result["status"] == 204
            assert result["data"] is None
            assert "response" not in result


@pytest.mark.asyncio
async def test_text_request_returns_string_data():
    """Verify request_text returns string data, not response object."""
    client = HTTPBaseClient(
        service_name="Test",
        base_url="https://api.test.com",
        rate_limit=100.0
    )
    
    fake_response = FakeClosedResponse(status=200, text_data="plain text response")
    
    async with client:
        with patch.object(client.session, 'request', return_value=fake_response):
            result = await client.request_text("GET", "/test")
            
            assert result["status"] == 200
            assert result["data"] == "plain text response"
            assert isinstance(result["data"], str)
            assert "response" not in result


@pytest.mark.asyncio
async def test_convenience_methods_dont_leak_response():
    """Verify get/post/put/patch/delete don't leak response objects."""
    client = HTTPBaseClient(
        service_name="Test",
        base_url="https://api.test.com",
        rate_limit=100.0
    )
    
    async with client:
        # Create fresh response for each call
        def make_response(*args, **kwargs):
            return FakeClosedResponse(status=200, json_data={"method": "test"})
        
        with patch.object(client.session, 'request', side_effect=make_response):
            # Test all convenience methods
            get_result = await client.get("/test")
            assert "response" not in get_result
            assert isinstance(get_result["data"], dict)
            
            post_result = await client.post("/test", json={"key": "val"})
            assert "response" not in post_result
            
            put_result = await client.put("/test", json={"key": "val"})
            assert "response" not in put_result
            
            patch_result = await client.patch("/test", json={"key": "val"})
            assert "response" not in patch_result
            
            delete_result = await client.delete("/test")
            assert "response" not in delete_result


@pytest.mark.asyncio
async def test_multiple_requests_each_close_properly():
    """Verify multiple requests in sequence properly close each response."""
    client = HTTPBaseClient(
        service_name="Test",
        base_url="https://api.test.com",
        rate_limit=100.0
    )
    
    responses = []
    
    def make_response(*args, **kwargs):
        resp = FakeClosedResponse(status=200, json_data={"call": len(responses)})
        responses.append(resp)
        return resp
    
    async with client:
        with patch.object(client.session, 'request', side_effect=make_response):
            # Make multiple requests
            r1 = await client.get("/test1")
            r2 = await client.get("/test2")
            r3 = await client.get("/test3")
            
            # Each should have data
            assert r1["data"]["call"] == 0
            assert r2["data"]["call"] == 1
            assert r3["data"]["call"] == 2
            
            # ALL responses should be closed
            for resp in responses:
                assert resp._closed is True


@pytest.mark.asyncio
async def test_error_responses_also_close_properly():
    """Verify error responses (4xx, 5xx) also close properly."""
    client = HTTPBaseClient(
        service_name="Test",
        base_url="https://api.test.com",
        rate_limit=100.0,
        max_retries=1  # Don't retry forever
    )
    
    # Test 404 (does not retry, returns immediately)
    fake_404 = FakeClosedResponse(status=404)
    async with client:
        with patch.object(client.session, 'request', return_value=fake_404):
            result = await client.get("/notfound")
            assert result["status"] == 404
            assert fake_404._closed is True
    
    # Test 500 (will retry once, then raise exception after max_retries)
    fake_500_attempts = []
    def make_500(*args, **kwargs):
        resp = FakeClosedResponse(status=500, text_data="Server Error")
        fake_500_attempts.append(resp)
        return resp
    
    client2 = HTTPBaseClient(
        service_name="Test",
        base_url="https://api.test.com",
        rate_limit=100.0,
        max_retries=2  # Will retry once (attempt 0, attempt 1), then fail on attempt 2
    )
    
    async with client2:
        with patch.object(client2.session, 'request', side_effect=make_500):
            # Should raise exception after retries exhausted
            with pytest.raises(aiohttp.ClientResponseError):
                await client2.get("/error")
            
            # All retry attempts should be closed despite exception
            assert len(fake_500_attempts) == 2  # max_retries = 2
            for resp in fake_500_attempts:
                assert resp._closed is True


@pytest.mark.asyncio
async def test_type_validation_no_response_object_ever():
    """Paranoid test: verify return type can NEVER be response object."""
    client = HTTPBaseClient(
        service_name="Test",
        base_url="https://api.test.com",
        rate_limit=100.0
    )
    
    fake_response = FakeClosedResponse(status=200, json_data={"data": "test"})
    
    async with client:
        with patch.object(client.session, 'request', return_value=fake_response):
            result = await client.get("/test")
            
            # Verify result is dict (not response object)
            assert isinstance(result, dict)
            
            # Verify result does NOT have aiohttp response attributes
            assert not hasattr(result, 'json')
            assert not hasattr(result, 'text')
            assert not hasattr(result, 'read')
            assert not hasattr(result, 'content')
            assert not hasattr(result, 'cookies')
            
            # Verify it HAS the expected dict keys
            assert set(result.keys()) == {"status", "headers", "data", "url", "method"}
