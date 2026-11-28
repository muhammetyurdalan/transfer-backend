from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from functools import wraps
from json import dumps
from jwt import decode, DecodeError, ExpiredSignatureError
from strawberry.exceptions import StrawberryGraphQLError as Error
from typing import Any, AsyncGenerator


User = get_user_model()


def get_headers(context):
    if isinstance(context, dict):
        if 'connection_params' in context:
            return context['connection_params'] or {}
        return context['request'].headers
    try:
        return context.connection_params or {}
    except AttributeError:
        return context.request.headers


def get_user(context):
    if isinstance(context, dict):
        return context.get('user')
    return getattr(context, 'user', None)


def append_user(context, user):
    if isinstance(context, dict):
        context['user'] = user
    else:
        setattr(context, 'user', user)


def check_role(user, roles):
    if getattr(user, 'guest_transfer_id', None) is not None:
        return 'GUEST' in roles
    return 'ALL' in roles or user.role in roles


async def check_guest_session(user, decoded_token):
    """
    A guest session works for its transfer only while the booking link
    (access nonce) is unchanged, ERR063 otherwise.
    """
    Transfer = apps.get_model('core', 'Transfer')
    transfer_id = str(decoded_token.get('tid') or '')
    transfers = Transfer.objects.filter(pk=transfer_id, customer=user)
    nonce = None
    if transfer_id.isdigit():
        nonce = await transfers.values_list('access_nonce', flat=True).afirst()
    valid = bool(nonce) and nonce[:8] == decoded_token.get('an')
    assert valid, dumps({'code': 'ERR063'})
    user.guest_transfer_id = transfer_id


async def authenticate(info, expand=()):
    headers = get_headers(info.context)
    assert 'authorization' in headers, dumps({'code': 'ERR001'})
    token = headers['authorization']
    secret, algorithm = settings.JWT_SECRET, settings.JWT_ALGORITHM
    try:
        decoded_token = decode(token, secret, algorithms=[algorithm])
    except DecodeError:
        raise Error(dumps({'code': 'ERR002'}))
    except ExpiredSignatureError:
        raise Error(dumps({'code': 'ERR003'}))
    filters = {'email': decoded_token['email'], 'is_active': True}
    users = User.objects.select_related(*expand).filter(**filters)
    user = await users.afirst()
    assert user, dumps({'code': 'ERR010'})
    valid = decoded_token.get('tv', 0) == user.token_version
    assert valid, dumps({'code': 'ERR003'})
    if decoded_token.get('scope') == 'guest':
        await check_guest_session(user, decoded_token)
    append_user(info.context, user)
    return user


async def get_optional_user(info):
    # For public resolvers: the user when a token is sent, None otherwise
    if 'authorization' not in get_headers(info.context):
        return None
    return await authenticate(info)


async def get_public_user(info):
    """
    For public resolvers that only adjust their result to the user: the user
    of a valid token, None without a token or with an invalid one (a stale
    token in a guest's browser must not fail them).
    """
    if 'authorization' not in get_headers(info.context):
        return None
    try:
        return await authenticate(info)
    except (AssertionError, Error):
        return None


def permission_required(*roles, expand=[]):
    def wrapper(func):
        @wraps(func)
        async def wrapped(root, info, **kwargs: Any):
            user = await authenticate(info, expand)
            assert check_role(user, roles), dumps({'code': 'ERR001'})
            func_instance = func(root, info, **kwargs)
            if isinstance(func_instance, AsyncGenerator):
                return func_instance
            return await func_instance
        return wrapped
    return wrapper
