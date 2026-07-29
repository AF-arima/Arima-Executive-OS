import asyncio
from unittest.mock import patch

import pytest
from fastapi import Request, Response

from app.main import add_request_context, logger


def test_unhandled_request_error_is_logged_without_a_duplicate_traceback(
) -> None:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/test-error",
        "raw_path": b"/test-error",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }

    async def failing_call_next(_: Request) -> Response:
        raise RuntimeError("expected test failure")

    async def exercise() -> None:
        with patch.object(logger, "error") as request_error:
            with pytest.raises(RuntimeError, match="expected test failure"):
                await add_request_context(Request(scope), failing_call_next)

            request_error.assert_called_once()
            arguments, keyword_arguments = request_error.call_args
            assert arguments == ("request_failed",)
            assert keyword_arguments.get("exc_info") is None
            metadata = keyword_arguments["extra"]
            assert metadata["correlation_id"]
            assert metadata["method"] == "POST"
            assert metadata["path"] == "/test-error"

    asyncio.run(exercise())
