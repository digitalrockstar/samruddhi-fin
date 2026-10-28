"""HTTP Basic auth for the dashboard and API.

Everything is protected except /health (Render's health check) and /webhook/*
(Telegram cannot send credentials; the webhook has its own secret-token check).
Browsers cache Basic credentials per origin, so the pages and their fetch()
calls to /api keep working after one login prompt.
"""
import base64
import hmac

OPEN_PATHS = {"/health", "/favicon.ico"}
OPEN_PREFIXES = ("/webhook/",)


class BasicAuthMiddleware:
    def __init__(self, app, username: str, password: str):
        self.app = app
        self._user = username.encode()
        self._pass = password.encode()

    def _ok(self, headers) -> bool:
        raw = dict(headers).get(b"authorization", b"")
        scheme, _, token = raw.partition(b" ")
        if scheme.lower() != b"basic" or not token:
            return False
        try:
            user, _, pwd = base64.b64decode(token).partition(b":")
        except Exception:
            return False
        # Evaluate both so timing does not reveal which one was wrong.
        user_ok = hmac.compare_digest(user, self._user)
        pass_ok = hmac.compare_digest(pwd, self._pass)
        return user_ok and pass_ok

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES) or self._ok(scope["headers"]):
            return await self.app(scope, receive, send)
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"www-authenticate", b'Basic realm="Samruddhi Fin"'),
                (b"content-type", b"text/plain; charset=utf-8"),
            ],
        })
        await send({"type": "http.response.body", "body": b"Authentication required"})
