from django.utils import translation


ACCESS_CONTROL_ALLOW_ORIGIN = b'access-control-allow-origin'
ACCESS_CONTROL_ALLOW_HEADERS = b'access-control-allow-headers'
ACCESS_CONTROL_ALLOW_METHODS = b'access-control-allow-methods'
VARY = b'vary'
CORS_HEADERS = {
    ACCESS_CONTROL_ALLOW_ORIGIN,
    ACCESS_CONTROL_ALLOW_HEADERS,
    ACCESS_CONTROL_ALLOW_METHODS
}
ALLOWED_METHODS = b'GET, POST, OPTIONS'
# '*' does not cover the Authorization header in browsers, list it explicitly
DEFAULT_ALLOWED_HEADERS = ['authorization', 'content-type']


class CorsMiddleware:
    def __init__(self, app, allow_all=False, hosts=None, headers=None):
        self.app = app
        self.allow_all = allow_all
        self.hosts = {host.encode() for host in hosts or []}
        self.headers = b', '.join(
            header.encode() for header in headers or DEFAULT_ALLOWED_HEADERS
        )

    def get_allowed_origin(self, scope):
        if self.allow_all:
            return b'*'
        origin = dict(scope.get('headers') or []).get(b'origin')
        if origin in self.hosts:
            return origin
        return None

    def get_cors_headers(self, allowed_origin):
        headers = [
            (ACCESS_CONTROL_ALLOW_ORIGIN, allowed_origin),
            (ACCESS_CONTROL_ALLOW_HEADERS, self.headers),
            (ACCESS_CONTROL_ALLOW_METHODS, ALLOWED_METHODS),
        ]
        if allowed_origin != b'*':
            headers.append((VARY, b'Origin'))
        return headers

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        allowed_origin = self.get_allowed_origin(scope)
        if not allowed_origin:
            return await self.app(scope, receive, send)
        cors_headers = self.get_cors_headers(allowed_origin)
        if scope['method'] == 'OPTIONS':
            await send({
                'type': 'http.response.start',
                'status': 200,
                'headers': cors_headers
            })
            await send({'type': 'http.response.body', 'body': b''})
            return

        async def cors_send(event):
            if event['type'] == 'http.response.start':
                headers = [
                    header for header in event.get('headers') or []
                    if header[0].lower() not in CORS_HEADERS
                ]
                event = {**event, 'headers': headers + cors_headers}
            await send(event)

        return await self.app(scope, receive, cors_send)


class AdminLocaleMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith('/admin'):
            translation.activate('en')
            request.LANGUAGE_CODE = translation.get_language()
        return self.get_response(request)
