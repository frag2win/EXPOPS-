"""ASGI body limit for the natural-language draft endpoint."""

from starlette.responses import JSONResponse


class DraftRequestSizeLimitMiddleware:
    def __init__(self, app, max_bytes: int = 32_768) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if not (
            scope["type"] == "http"
            and scope.get("method") == "POST"
            and scope.get("path") == "/api/v1/experiments/draft"
        ):
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_bytes:
                response = JSONResponse(
                    status_code=413,
                    content={"error": {"code": "REQUEST_TOO_LARGE", "message": "Draft request exceeds the 32 KiB limit"}},
                )
                await response(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        body = b"".join(chunks)
        delivered = False

        async def replay_body():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay_body, send)
