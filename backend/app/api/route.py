"""Route class that commits the request's DB session BEFORE the response is sent.

With FastAPI >= 0.118 the cleanup of `yield` dependencies runs after the response has been
transmitted. Committing there let clients receive "201 Created" and immediately re-read stale data
(and a failed commit would have been reported as success). Committing here fixes both: if the commit
fails the client gets a 500, never a false success.
"""
from __future__ import annotations

from typing import Callable

from fastapi import Request, Response
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool


class CommitRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            response = await original(request)  # raises for HTTPException -> nothing is committed
            db = getattr(request.state, "db", None)
            if db is not None:
                await run_in_threadpool(db.commit)  # blocking SQLite I/O off the event loop
            return response

        return handler
