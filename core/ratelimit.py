from django.core.cache import cache
from json import dumps
from logging import getLogger
from time import time


logger = getLogger(__name__)


def get_client_ip(context):
    """
    The client's IP. Over channels (production ASGI) it is the scope's client,
    which daphne --proxy-headers takes from X-Forwarded-For set by Caddy
    (Caddy ignores the X-Forwarded-For a client sends). Over the Django view
    (tests) it is REMOTE_ADDR.
    """
    request = context.get('request') if isinstance(context, dict) else (
        getattr(context, 'request', None)
    )
    consumer = getattr(request, 'consumer', None)
    if consumer is not None:
        client = consumer.scope.get('client')
        return client[0] if client else None
    meta = getattr(request, 'META', None) or {}
    return meta.get('REMOTE_ADDR')


async def check_rate_limit(info, scope, limit, window):
    """
    Fixed window counter per client IP: at most `limit` calls of `scope` in
    `window` seconds, otherwise ERR056. It counts every resolver call, so
    aliases in one request (q1: quoteTransfer q2: quoteTransfer) count
    separately. If the cache is unreachable the call is allowed (logged), a
    Redis outage must not break booking.
    """
    ip = get_client_ip(info.context) or 'unknown'
    await check_key_limit(f'{scope}:{ip}', limit, window)


async def check_key_limit(key, limit, window):
    """
    Fixed window counter of any key (e.g. a transfer's code attempts): at
    most `limit` calls in `window` seconds, otherwise ERR056. An unreachable
    cache allows the call (logged).
    """
    bucket = int(time() // window)
    key = f'ratelimit:{key}:{bucket}'
    try:
        if await cache.aadd(key, 1, timeout=window):
            count = 1
        else:
            try:
                count = await cache.aincr(key)
            except ValueError:
                await cache.aset(key, 1, timeout=window)
                count = 1
    except Exception:
        logger.exception('Rate limit cache unavailable')
        return
    assert count <= limit, dumps({'code': 'ERR056'})
