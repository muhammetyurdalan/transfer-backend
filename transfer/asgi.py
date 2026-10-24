from os import environ
from django.core.asgi import get_asgi_application


environ.setdefault('DJANGO_SETTINGS_MODULE', 'transfer.settings.local')
django_application = get_asgi_application()


from channels.routing import ProtocolTypeRouter, URLRouter
from django.conf import settings
from django.urls import re_path
from strawberry.channels import GraphQLHTTPConsumer, GraphQLWSConsumer
from core.middlewares import CorsMiddleware
from core.schema import schema


ide = {} if settings.DEBUG else {'graphql_ide': None}

application = ProtocolTypeRouter({
    'http': URLRouter([
        re_path(
            '^graphql',
            GraphQLHTTPConsumer.as_asgi(
                schema=schema,
                multipart_uploads_enabled=True,
                **ide
            )
        ),
        re_path('^', django_application),
    ]),
    'websocket': URLRouter([
        re_path('^graphql', GraphQLWSConsumer.as_asgi(schema=schema)),
    ]),
})
application = CorsMiddleware(
    application,
    allow_all=settings.DEBUG,
    hosts=settings.CORS_ALLOWED_ORIGINS
)
