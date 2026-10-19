"""Enforce security on actual FastMCP tool invocations."""

import asyncio
from typing import Any

from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware, MiddlewareContext

from ..config import SolidWorksMCPConfig
from .rate_limiting import RateLimiter
from .runtime import SecurityEnforcer, SecurityError


class ToolSecurityMiddleware(Middleware):
    """Authenticate before dispatch and rate limit authenticated callers."""

    def __init__(self, config: SolidWorksMCPConfig) -> None:
        self.enforcer = SecurityEnforcer(config)
        self.config = config
        self.limiter = RateLimiter(config.rate_limit_per_minute)
        self._workflow_lock = asyncio.Lock()

    async def on_call_tool(self, context: MiddlewareContext[Any], call_next: Any) -> Any:
        arguments = dict(context.message.arguments or {})
        # Credentials belong to the invocation envelope, never the CAD schema.
        key = arguments.pop("api_key", None)
        headers = get_http_headers(include_all=True)
        authorization = headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            key = authorization[7:].strip()
        if self.enforcer._is_auth_required():
            if not key or not self.enforcer.accepts_key(str(key)):
                raise SecurityError("authentication failed: missing or invalid API key")
        # Do not trust a caller-supplied client_id to bypass rate limits.
        identity = "authenticated" if key else "local"
        if self.config.enable_rate_limiting and not self.limiter.is_allowed(identity):
            raise SecurityError("rate limit exceeded")
        message = context.message.model_copy(update={"arguments": arguments})
        # Hold across the entire tool, including activation and multi-step imports.
        # STA serialization alone only protects individual native calls.
        async with self._workflow_lock:
            return await call_next(context.copy(message=message))
