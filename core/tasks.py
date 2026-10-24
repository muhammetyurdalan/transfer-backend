from asgiref.sync import async_to_sync as a2s
from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from fcm_django.models import FCMDevice
from firebase_admin.messaging import Message, Notification
from core import utils


@shared_task
def send_email(subject, message, recipients):
    return send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, recipients)


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
