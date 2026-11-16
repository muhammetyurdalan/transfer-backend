"""Social sign-in: verifies OpenID Connect id_tokens of the providers."""
from asgiref.sync import sync_to_async as s2a
from django.conf import settings
from json import dumps
from jwt import PyJWKClient, PyJWTError, decode
from strawberry.exceptions import StrawberryGraphQLError as Error
from core import enums


# Tenant of personal Microsoft accounts (outlook.com, hotmail.com, live.com)
MSA_TENANT = '9188040d-6c67-4c5b-b112-36a304b66dad'


def google_issuer(claims):
    return claims.get('iss') in ('https://accounts.google.com', 'accounts.google.com')


def google_email_verified(claims):
    return claims.get('email_verified') is True


def microsoft_issuer(claims):
    """`common` endpoint: the issuer contains the user's tenant (`tid`)."""
    tid = claims.get('tid')
    return bool(tid) and claims.get('iss') == f'https://login.microsoftonline.com/{tid}/v2.0'


def microsoft_email_verified(claims):
    """Work/school emails are set by tenant admins and can be any address (nOAuth),
    they are trusted only with the `xms_edov` (email domain owner verified) claim.
    Personal account emails are verified by Microsoft."""
    if claims.get('tid') == MSA_TENANT:
        return True
    return claims.get('xms_edov') in (True, 1, '1', 'true')


PROVIDERS = {
    enums.SocialProvider.GOOGLE: {
        'jwks_url': 'https://www.googleapis.com/oauth2/v3/certs',
        'audience': lambda: settings.GOOGLE_CLIENT_IDS,
        'issuer': google_issuer,
        'email_verified': google_email_verified,
    },
    enums.SocialProvider.MICROSOFT: {
        'jwks_url': 'https://login.microsoftonline.com/common/discovery/v2.0/keys',
        'audience': lambda: settings.MICROSOFT_CLIENT_IDS,
        'issuer': microsoft_issuer,
        'email_verified': microsoft_email_verified,
    },
}
_jwks_clients = {}


def signing_key(token, jwks_url):
    """Public key of the token's `kid`, keys are cached for an hour."""
    client = _jwks_clients.get(jwks_url)
    if not client:
        client = _jwks_clients[jwks_url] = PyJWKClient(jwks_url, lifespan=3600)
    return client.get_signing_key_from_jwt(token).key


def decode_id_token(token, config, audience):
    """Signature, aud and exp; the issuer is checked by the provider's rule."""
    key = signing_key(token, config['jwks_url'])
    return decode(
        token, key, algorithms=['RS256'], audience=audience, leeway=60,
        options={'require': ['exp', 'iat', 'iss', 'aud', 'sub']},
    )


def names_of(claims):
    first, last = claims.get('given_name', ''), claims.get('family_name', '')
    if not (first or last) and claims.get('name'):
        # Microsoft sends given/family name only as optional claims
        first, _, last = claims['name'].strip().partition(' ')
    return first, last


async def verify_id_token(provider, token, nonce):
    """Checks signature, aud, iss, exp, nonce and a verified email.
    Unconfigured provider: ERR030, invalid token: ERR045."""
    config = PROVIDERS.get(provider)
    assert config, dumps({'code': 'ERR030'})
    audience = config['audience']()
    assert audience, dumps({'code': 'ERR030'})
    try:
        claims = await s2a(decode_id_token)(token, config, audience)
    except PyJWTError:
        raise Error(dumps({'code': 'ERR045'}))
    assert config['issuer'](claims), dumps({'code': 'ERR045'})
    assert nonce and claims.get('nonce') == nonce, dumps({'code': 'ERR045'})
    verified = claims.get('email') and config['email_verified'](claims)
    assert verified, dumps({'code': 'ERR045'})
    first_name, last_name = names_of(claims)
    return {'email': claims['email'], 'first_name': first_name, 'last_name': last_name}
