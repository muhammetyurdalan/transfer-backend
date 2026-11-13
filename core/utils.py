from asgiref.sync import sync_to_async as s2a
import strawberry as sb
from channels.layers import get_channel_layer
from cryptography.fernet import Fernet, InvalidToken
from datetime import timedelta
from django.apps import apps
from django.conf import settings
from django.db.models import DecimalField, OuterRef, Q, Subquery, Sum
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.translation import activate, gettext
from json import dumps
from jwt import encode, decode, DecodeError, ExpiredSignatureError
from strawberry.exceptions import StrawberryGraphQLError as Error
from core import enums, models


User = get_user_model()
fernet = Fernet(settings.FERNET_KEY)
channel_layer = get_channel_layer()
SUBSCRIPTION_TYPE = 'subscription'
AUTHORIZED_ROLES = [enums.Role.ADMIN, enums.Role.STAFF, enums.Role.OPERATOR]


# Input -> Model

def get_fields(data, ignored=[]):
    data = sb.asdict(data)
    fields = {}
    for key, value in data.items():
        if key == 'id' or key in ignored or value is sb.UNSET:
            continue
        fields[key] = value
    return fields


def check_phone(phone):
    """Users must have a phone: stripped, not empty, ERR043 otherwise."""
    phone = (phone or '').strip()
    assert phone, dumps({'code': 'ERR043'})
    assert len(phone) <= 20, dumps({'code': 'ERR030'})
    return phone


def set_attributes(instance, data, ignored=None, set_nulls=True):
    ignored = ignored or []
    if not isinstance(data, dict):
        data = sb.asdict(data)
    for attribute, value in data.items():
        if attribute in ignored or value is sb.UNSET:
            continue
        if not set_nulls and value is None:
            continue
        setattr(instance, attribute, value)


async def set_m2m(instance, data, m2m_fields, is_update=False):
    data = sb.asdict(data)
    for m2m_field in m2m_fields:
        if m2m_field[-3:] == 'ies':
            m2m_input = m2m_field[:-3] + 'y_ids'
        else:
            m2m_input = m2m_field[:-1] + '_ids'
        if data.get(m2m_input, sb.UNSET) is sb.UNSET:
            continue
        m2m = getattr(instance, m2m_field)
        if is_update:
            await m2m.aset(data[m2m_input], clear=True)
        else:
            await m2m.aadd(*data[m2m_input])


async def create_update_instances(items, model, parent, parent_id, update=False):
    if not items:
        return
    Model = apps.get_model('core', model)
    if update:
        kwargs = {f'{parent}_id': parent_id}
        await Model.objects.filter(**kwargs).adelete()
    instances = []
    for item in items:
        instance = Model()
        set_attributes(instance, item)
        setattr(instance, f'{parent}_id', parent_id)
        instances.append(instance)
    await Model.objects.abulk_create(instances)


def get_dict(instance):
    # Model -> plain dict for external services (PSP), password excluded
    if not instance:
        return {}
    data = {}
    for key, value in instance.__dict__.items():
        if key == 'password' or key.startswith('_'):
            continue
        if value.__class__.__name__ == 'FieldFile':
            try:
                data[key] = value.url
            except ValueError:
                data[key] = None
        else:
            data[key] = value
    return data


# Query

async def get_count(info, qs):
    requested_fields = {
        selection.name for field in info.selected_fields
        for selection in field.selections
    }
    count = None
    if 'count' in requested_fields:
        count = await qs.acount()
    return count


# Realtime

def get_group_id(user_id):
    return f'user_{user_id}'


async def broadcast(sender_id, action, receiver_ids, **kwargs):
    message = {'sender_id': sender_id, 'action': action}
    message.update(kwargs)
    payload = {'type': SUBSCRIPTION_TYPE, 'message': message}
    for receiver_id in receiver_ids:
        await channel_layer.group_send(get_group_id(receiver_id), payload)


# Transfer

STAFF_ROLES = (enums.Role.ADMIN, enums.Role.STAFF)
DISPATCH_ROLES = STAFF_ROLES + (enums.Role.OPERATOR, )


def get_participant_ids(transfer):
    user_ids = (transfer.customer_id, transfer.operator_id, transfer.driver_id)
    return {user_id for user_id in user_ids if user_id}


def check_participant(user, transfer):
    authorized = user.id in get_participant_ids(transfer)
    assert authorized, dumps({'code': 'ERR001'})


def get_receiver_ids(sender_id, transfer):
    return get_participant_ids(transfer) - {sender_id}


def check_messaging_access(user, transfer):
    '''ADMIN/STAFF can join every transfer's messaging.'''
    if user.role not in STAFF_ROLES:
        check_participant(user, transfer)


async def get_admin_ids():
    admins = User.objects.filter(role__in=STAFF_ROLES, is_active=True)
    admins = admins.values_list('id', flat=True)
    admin_ids = {user_id async for user_id in admins}
    return admin_ids


async def get_messaging_receiver_ids(sender_id, transfer):
    '''Participants and all active ADMIN/STAFF users, except the sender.'''
    admin_ids = await get_admin_ids()
    return (get_participant_ids(transfer) | admin_ids) - {sender_id}


def get_transfers(user, expand=()):
    transfers = models.Transfer.objects.select_related(*expand).all()
    if user.role in STAFF_ROLES:
        return transfers
    if user.role == enums.Role.OPERATOR:
        return transfers.filter(operator=user)
    if user.role == enums.Role.DRIVER:
        return transfers.filter(driver=user)
    return transfers.filter(customer=user)


def get_transactions(user):
    '''
    ADMIN/STAFF: all. OPERATOR: transactions of the transfers they operate.
    DRIVER: tips (and their refunds) of own transfers.
    Others: own payments, tips and refunds.
    completed_refund_amount is annotated for Transaction.refunded_amount
    (one query for the whole list instead of one per row).
    '''
    Category = enums.TransactionCategory
    completed_refunds = models.Transaction.objects.filter(
        parent=OuterRef('pk'), status=enums.TransactionStatus.COMPLETED
    ).order_by().values('parent').annotate(total=Sum('order_amount'))
    transactions = models.Transaction.objects.annotate(
        completed_refund_amount=Subquery(
            completed_refunds.values('total'),
            output_field=DecimalField(**models.DECIMAL_10)
        )
    )
    if user.role in STAFF_ROLES:
        return transactions
    if user.role == enums.Role.OPERATOR:
        return transactions.filter(transfer__operator=user)
    if user.role == enums.Role.DRIVER:
        tips = Q(category=Category.TIP) | Q(parent__category=Category.TIP)
        return transactions.filter(tips, transfer__driver=user)
    return transactions.filter(user=user)


async def get_operator_ids():
    operators = User.objects.filter(role=enums.Role.OPERATOR, is_active=True)
    return [user_id async for user_id in operators.values_list('id', flat=True)]


def get_transfer_data(transfer):
    return {'model': 'Transfer', 'id': str(transfer.id)}


# Notification

async def create_notifications(
    receiver_ids,
    title,
    body,
    action=None,
    data=None,
    sender_id=None
):
    receivers = User.objects.filter(id__in=receiver_ids, is_active=True)
    receiver_ids = [
        receiver_id async for receiver_id in
        receivers.values_list('id', flat=True)
    ]
    notifications = [
        models.Notification(
            user_id=receiver_id,
            title=title,
            body=body,
            action=action,
            data=data or {}
        )
        for receiver_id in receiver_ids
    ]
    created = await models.Notification.objects.abulk_create(notifications)
    for notification in created:
        await broadcast(
            sender_id,
            enums.Action.NOTIFICATION,
            [notification.user_id],
            notification_id=str(notification.id),
            message=notification.title
        )
    if settings.FCM_ENABLED and receiver_ids:
        from core.tasks import send_push
        push_data = {key: str(value) for key, value in (data or {}).items()}
        if action:
            push_data['action'] = str(action)
        args = (receiver_ids, title, body, push_data)
        await s2a(send_push.delay)(*args)
    return created


async def notify_users(
    receiver_ids,
    title,
    body,
    action,
    transfer,
    sender_id=None,
    email_data=None
):
    '''Notification in each receiver's language, title/body are gettext_noop.'''
    receivers = User.objects.filter(id__in=receiver_ids, is_active=True)
    async for receiver in receivers.only('id', 'language', 'email'):
        activate(receiver.language)
        await create_notifications(
            [receiver.id],
            gettext(title),
            gettext(body) % {'id': transfer.id},
            action=action,
            data=get_transfer_data(transfer),
            sender_id=sender_id
        )
        if email_data:
            await send_email_to(receiver, email_data)


# Email

EMAIL_TRANSFER_FIELDS = (
    'id', 'pickup_time', 'pickup_address', 'dropoff_address', 'price',
    'currency'
)


def get_email_transfer(transfer):
    return {field: getattr(transfer, field) for field in EMAIL_TRANSFER_FIELDS}


async def send_email_to(receiver, email_data):
    """
    Sends a copy of email_data to receiver (needs email, language loaded),
    subject (gettext_noop) is translated to the receiver's language.
    """
    from core.tasks import send_email
    activate(receiver.language)
    data = {
        **email_data,
        'subject': gettext(email_data.get('subject') or ''),
        'to_user': {'email': receiver.email, 'language': receiver.language},
    }
    await s2a(send_email.delay)(data)


# Auth

def normalize_email(email):
    return User.objects.normalize_email(email).lower()


def create_token(email):
    exp = timezone.now() + timedelta(days=settings.JWT_EXPIRE_DAYS)
    payload = {'email': email, 'exp': exp}
    return encode(payload, settings.JWT_SECRET, settings.JWT_ALGORITHM)


def encode_secret(payload):
    secret, algorithm = settings.JWT_SECRET, settings.JWT_ALGORITHM
    token = encode(payload, secret, algorithm)
    return fernet.encrypt(token.encode()).decode()


def decode_secret(secret):
    try:
        token = fernet.decrypt(secret.encode()).decode()
        data = decode(token, settings.JWT_SECRET, [settings.JWT_ALGORITHM])
    except (InvalidToken, DecodeError):
        raise Error(dumps({'code': 'ERR002'}))
    except ExpiredSignatureError:
        raise Error(dumps({'code': 'ERR009'}))
    return data


def uppercase(text: str, lang: str = 'en') -> str:
    TR_UPPER = str.maketrans({'i': 'İ', 'ı': 'I'})
    if lang.lower().startswith('tr'):
        text = text.translate(TR_UPPER)
    return text.upper()


def get_district_filters(district_name):
    filters = Q()
    district_name = district_name.strip()
    for lang in settings.LANGUAGES:
        name = uppercase(district_name, lang[0])
        filters |= Q(name__iexact=name)
    return filters