from datetime import timedelta
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import activate, gettext
from json import dumps
import strawberry as sb
import strawberry_django as sbd
from strawberry import Info
from typing import List, Optional
from core import enums, inputs, models, outputs, utils
from core.decorators import permission_required


User = utils.User
manager = models.Message.objects
Status = enums.TransferStatus
MESSAGING_STATUSES = [Status.ASSIGNED, Status.STARTED]


def get_visible_messages(user, transfer):
    messages = transfer.messages.all()
    if user.role in utils.STAFF_ROLES:
        return messages
    utils.check_participant(user, transfer)
    filters = Q(sender=user) | Q(receivers=user)
    return messages.filter(filters).distinct()


@sb.type
class Query:
    @sbd.field(description='ALL')
    @permission_required('ALL')
    async def messages(
        root, info: Info,
        transfer_id: sb.ID,
        ordering: Optional[List[outputs.MessageOrder]] = sb.UNSET
    ) -> List[outputs.Message]:
        user = info.context['user']
        transfer = await models.Transfer.objects.filter(pk=transfer_id).afirst()
        assert transfer, dumps({'code': 'ERR013'})
        messages = get_visible_messages(user, transfer)
        if ordering:
            args = (outputs.MessageOrder, ordering, info, messages)
            messages = sbd.ordering.apply_ordering(*args)
        return messages


def check_messaging_available(transfer):
    available = transfer.status in MESSAGING_STATUSES
    assert available, dumps({'code': 'ERR019'})


def check_file(file):
    if file:
        valid = file.size <= settings.MESSAGE_FILE_MAX_SIZE
        assert valid, dumps({'code': 'ERR024'})


async def send_message_notifications(receiver_ids, transfer, sender_id):
    receivers = User.objects.filter(id__in=receiver_ids, is_active=True)
    async for receiver in receivers.only('id', 'language'):
        activate(receiver.language)
        await utils.create_notifications(
            [receiver.id],
            gettext('New Message'),
            gettext('You have a new message'),
            action=enums.Action.MESSAGE,
            data=utils.get_transfer_data(transfer),
            sender_id=sender_id
        )


async def see_message_notifications(user_id, transfer):
    notifications = models.Notification.objects.filter(
        user_id=user_id,
        action=enums.Action.MESSAGE,
        data=utils.get_transfer_data(transfer),
        is_seen=False
    )
    await notifications.aupdate(is_seen=True)


async def get_message_receiver_ids(message):
    receiver_ids = message.receivers.values_list('id', flat=True)
    return [receiver_id async for receiver_id in receiver_ids]


def get_message_payload(message):
    return {
        'transfer_id': str(message.transfer_id),
        'message_id': str(message.id),
        'parent_id': str(message.parent_id) if message.parent_id else None,
        'message': message.message or '',
        'location': message.location or '',
        'file': message.file.url if message.file else '',
    }


@sb.type
class Mutation:
    @sb.mutation(description='CUSTOMER, DRIVER, OPERATOR')
    @permission_required('CUSTOMER', 'DRIVER', 'OPERATOR')
    async def send_message(
        root, info: Info,
        data: inputs.SendMessage
    ) -> outputs.Message:
        sender = info.context['user']
        message_valid = any([data.message, data.location, data.file])
        assert message_valid, dumps({'code': 'ERR017'})
        check_file(data.file)
        transfers = models.Transfer.objects.filter(pk=data.transfer_id)
        transfer = await transfers.afirst()
        assert transfer, dumps({'code': 'ERR013'})
        utils.check_participant(sender, transfer)
        check_messaging_available(transfer)
        if parent_id := data.parent_id:
            parents = get_visible_messages(sender, transfer)
            parents = parents.filter(pk=parent_id, is_deleted=False)
            assert await parents.aexists(), dumps({'code': 'ERR023'})
        receiver_ids = utils.get_receiver_ids(sender.id, transfer)
        assert receiver_ids, dumps({'code': 'ERR018'})
        message = models.Message(transfer=transfer, sender=sender)
        utils.set_attributes(message, data, ignored=['transfer_id'])
        await message.asave()
        defaults = {'transfer': transfer}
        await message.receivers.aadd(*receiver_ids, through_defaults=defaults)
        await send_message_notifications(receiver_ids, transfer, sender.id)
        args = (sender.id, enums.Action.MESSAGE, receiver_ids)
        await utils.broadcast(*args, **get_message_payload(message))
        return message

    @sb.mutation(description='CUSTOMER, DRIVER, OPERATOR')
    @permission_required('CUSTOMER', 'DRIVER', 'OPERATOR')
    async def update_message(
        root, info: Info,
        data: inputs.UpdateMessage
    ) -> outputs.Message:
        user = info.context['user']
        message = await manager.filter(pk=data.id, sender=user).afirst()
        assert message, dumps({'code': 'ERR001'})
        assert not message.is_deleted, dumps({'code': 'ERR021'})
        editable = not (message.file or message.location)
        assert editable, dumps({'code': 'ERR022'})
        elapsed = timezone.now() - message.time
        window = timedelta(hours=settings.MESSAGE_UPDATE_WINDOW_HOURS)
        assert elapsed <= window, dumps({'code': 'ERR020'})
        assert data.message, dumps({'code': 'ERR017'})
        message.message = data.message
        await message.asave()
        receiver_ids = await get_message_receiver_ids(message)
        args = (user.id, enums.Action.UPDATE_MESSAGE, receiver_ids)
        await utils.broadcast(*args, **get_message_payload(message))
        return message

    @sb.mutation(description='CUSTOMER, DRIVER, OPERATOR')
    @permission_required('CUSTOMER', 'DRIVER', 'OPERATOR')
    async def delete_message(root, info: Info, id: sb.ID) -> outputs.Message:
        user = info.context['user']
        messages = manager.filter(pk=id, sender=user, is_deleted=False)
        message = await messages.afirst()
        assert message, dumps({'code': 'ERR001'})
        message.is_deleted = True
        message.deleted_time = timezone.now()
        message.deleted_by = user
        message.file = ''
        await message.asave()
        receiver_ids = await get_message_receiver_ids(message)
        args = (user.id, enums.Action.DELETE_MESSAGE, receiver_ids)
        kwargs = {
            'transfer_id': str(message.transfer_id),
            'message_id': str(message.id)
        }
        await utils.broadcast(*args, **kwargs)
        return message

    @sb.mutation(description='CUSTOMER, DRIVER, OPERATOR')
    @permission_required('CUSTOMER', 'DRIVER', 'OPERATOR')
    async def see_messages(
        root, info: Info,
        transfer_id: sb.ID
    ) -> outputs.SuccessResponse:
        user = info.context['user']
        transfer = await models.Transfer.objects.filter(pk=transfer_id).afirst()
        assert transfer, dumps({'code': 'ERR013'})
        utils.check_participant(user, transfer)
        through_messages = user.through_messages.filter(transfer=transfer)
        await through_messages.aupdate(is_seen=True)
        await see_message_notifications(user.id, transfer)
        return outputs.SuccessResponse(success=True)
