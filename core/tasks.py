from asgiref.sync import async_to_sync as a2s
from celery import shared_task
from django.conf import settings
from django.utils import timezone
from fcm_django.models import FCMDevice
from firebase_admin.messaging import Message, Notification
from httpx import HTTPError, post
from json import dumps, loads
from logging import getLogger
from strawberry.exceptions import StrawberryGraphQLError as Error
from core import enums, models, psp, utils


logger = getLogger(__name__)
Status = enums.TransferStatus


@shared_task
def send_email(data):
    """
    Sends an email through the mail microservice (MAIL_MS_URL,
    /sendTransferEmail). data: action (template, e.g. 'verification'),
    to_user {'email', 'language'}, subject, message, link, transfer.
    Returns whether the microservice accepted it, failures are logged.
    """
    data = loads(dumps(data, default=str))
    action = data.get('action')
    try:
        response = post(
            settings.MAIL_MS_URL, headers=psp.get_headers(), json=data,
            timeout=30.0
        ).json()
    except (HTTPError, ValueError) as error:
        logger.error('Mail %s failed: %r', action, error)
        return False
    if not response.get('success'):
        logger.error('Mail %s failed: %s', action, response.get('error'))
    return bool(response.get('success'))


@shared_task
def send_notification(receiver_ids, title, body, action=None, data=None):
    # For sync callers (celery beat, other tasks). Async resolvers must await
    # utils.create_notifications directly instead of calling this task.
    args = (receiver_ids, title, body, action, data)
    created = a2s(utils.create_notifications)(*args)
    return [notification.id for notification in created]


@shared_task
def send_push(receiver_ids, title, body, data=None):
    # Called by utils.create_notifications, data values must be strings.
    # Inactive device tokens are removed (DELETE_INACTIVE_DEVICES).
    if not settings.FCM_ENABLED:
        return 0
    devices = FCMDevice.objects.filter(user_id__in=receiver_ids, active=True)
    message = Message(
        notification=Notification(title=title, body=body),
        data=data or {}
    )
    response = devices.send_message(message)
    return response.response.success_count


# Periodic (celery beat, CELERY_BEAT_SCHEDULE)

@shared_task
def run_hourly_tasks():
    return {'expired_transfers': a2s(check_transfers_expiring)()}


async def check_transfers_expiring():
    """
    PENDING (unpaid) transfers whose pickup time has passed become EXPIRED
    and their payment links are expired. Returns the expired transfer ids.
    """
    transfers = models.Transfer.objects.filter(
        status=Status.PENDING, pickup_time__lte=timezone.now()
    )
    expired_ids = []
    async for transfer in transfers.only('id', 'psp_data'):
        # Conditional update: a payment arriving meanwhile wins
        pending = models.Transfer.objects.filter(
            pk=transfer.pk, status=Status.PENDING
        )
        if not await pending.aupdate(status=Status.EXPIRED):
            continue
        expired_ids.append(transfer.pk)
        try:
            await psp.expire_payment_link(transfer)
        except Error:
            # Logged by psp, the link expires on its own anyway
            continue
        await models.Transfer.objects.filter(pk=transfer.pk).aupdate(
            psp_data=transfer.psp_data
        )
    if expired_ids:
        logger.info('Expired transfers: %s', expired_ids)
    return expired_ids
