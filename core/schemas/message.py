from datetime import timedelta
from asgiref.sync import sync_to_async as s2a
from django.conf import settings
from django.db import transaction as db_transaction
from django.db.models import F, Q
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
MessageStatus = enums.MessageStatus
Status = enums.TransferStatus
MESSAGING_STATUSES = [Status.ACCEPTED, Status.STARTED]


async def get_visible_messages(user, transfer):
    messages = transfer.messages.all()
    if user.role in utils.STAFF_ROLES:
        return messages
    await utils.check_messaging_access(user, transfer)
    filters = Q(sender=user) | Q(receivers=user)
    return messages.filter(filters).distinct()


@sb.type
class Query:
    @sbd.field(description='ALL, GUEST')
    @permission_required('ALL', 'GUEST')
    async def messages(
        root, info: Info,
        transfer_id: sb.ID,
        ordering: Optional[List[outputs.MessageOrder]] = sb.UNSET
    ) -> List[outputs.Message]:
        user = info.context['user']
        transfer = await models.Transfer.objects.filter(pk=transfer_id).afirst()
        assert transfer, dumps({'code': 'ERR013'})
        messages = await get_visible_messages(user, transfer)
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


async def get_reaction_receiver_ids(user, message):
    '''Those who can still see the message, except user.'''
    viewer_ids = {message.sender_id} | await utils.get_admin_ids()
    viewer_ids |= set(await get_message_receiver_ids(message))
    args = (user.id, message.transfer)
    return viewer_ids & await utils.get_messaging_receiver_ids(*args)


def get_message_payload(message):
    return {
        'transfer_id': str(message.transfer_id),
        'message_id': str(message.id),
        'parent_id': str(message.parent_id) if message.parent_id else None,
        'message': message.message or '',
        'location': message.location or '',
        'file': message.file.url if message.file else '',
    }


PREVIOUS_STATUSES = {
    MessageStatus.DELIVERED: [MessageStatus.SENT],
    MessageStatus.SEEN: [MessageStatus.SENT, MessageStatus.DELIVERED],
}


def get_status_messages(user):
    '''Messages of others that advance when user receives/sees them.'''
    dispatch = Q(sender__role__in=utils.DISPATCH_ROLES)
    by_customer = Q(sender=F('transfer__customer'))
    filters = Q(transfer__customer=user) & dispatch
    if user.role in utils.DISPATCH_ROLES:
        filters |= ~dispatch | by_customer
    messages = manager.filter(
        filters, transfer__in=utils.get_transfers(user).values('pk')
    )
    if user.role not in utils.STAFF_ROLES:
        messages = messages.filter(receivers=user)
    return messages.exclude(sender=user)


@s2a
def update_message_status(messages, status):
    """
    Returns (id, sender_id, transfer_id) of the messages it really changed:
    concurrent requests wait for the row locks and find nothing to change.
    """
    with db_transaction.atomic():
        pending = manager.select_for_update().filter(
            pk__in=messages.values('pk'),
            status__in=PREVIOUS_STATUSES[status]
        )
        pending = pending.order_by('pk')
        rows = list(pending.values_list('pk', 'sender_id', 'transfer_id'))
        if rows:
            ids = [row[0] for row in rows]
            manager.filter(pk__in=ids).update(status=status)
    return rows


async def advance_messages(user, messages, status):
    changed = {}
    for pk, *key in await update_message_status(messages, status):
        changed.setdefault(tuple(key), []).append(str(pk))
    for (sender_id, transfer_id), message_ids in changed.items():
        await utils.broadcast(
            user.id,
            enums.Action.MESSAGE_STATUS,
            [sender_id],
            transfer_id=str(transfer_id),
            message_ids=message_ids,
            message_status=status
        )


@sb.type
class Mutation:
    @sb.mutation(description='CUSTOMER, DRIVER, OPERATOR, ADMIN, STAFF, GUEST')
    @permission_required(
        'CUSTOMER', 'DRIVER', 'OPERATOR', 'ADMIN', 'STAFF', 'GUEST'
    )
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
        await utils.check_messaging_access(sender, transfer)
        check_messaging_available(transfer)
        if parent_id := data.parent_id:
            parents = await get_visible_messages(sender, transfer)
            parents = parents.filter(pk=parent_id, is_deleted=False)
            assert await parents.aexists(), dumps({'code': 'ERR023'})
        args = (sender.id, transfer)
        receiver_ids = await utils.get_messaging_receiver_ids(*args)
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

    @sb.mutation(description='CUSTOMER, DRIVER, OPERATOR, ADMIN, STAFF, GUEST')
    @permission_required(
        'CUSTOMER', 'DRIVER', 'OPERATOR', 'ADMIN', 'STAFF', 'GUEST'
    )
    async def update_message(
        root, info: Info,
        data: inputs.UpdateMessage
    ) -> outputs.Message:
        user = info.context['user']
        message = await manager.filter(pk=data.id, sender=user).afirst()
        assert message, dumps({'code': 'ERR001'})
        utils.check_guest_scope(user, message.transfer_id)
        assert not message.is_deleted, dumps({'code': 'ERR021'})
        editable = not (message.file or message.location)
        assert editable, dumps({'code': 'ERR022'})
        elapsed = timezone.now() - message.time
        window = timedelta(hours=settings.MESSAGE_UPDATE_WINDOW_HOURS)
        assert elapsed <= window, dumps({'code': 'ERR020'})
        assert data.message, dumps({'code': 'ERR017'})
        message.message = data.message
        message.is_edited = True
        await message.asave(update_fields=['message', 'is_edited'])
        receiver_ids = await get_message_receiver_ids(message)
        args = (user.id, enums.Action.UPDATE_MESSAGE, receiver_ids)
        await utils.broadcast(*args, **get_message_payload(message))
        return message

    @sb.mutation(description='CUSTOMER, DRIVER, OPERATOR, ADMIN, STAFF, GUEST')
    @permission_required(
        'CUSTOMER', 'DRIVER', 'OPERATOR', 'ADMIN', 'STAFF', 'GUEST'
    )
    async def delete_message(root, info: Info, id: sb.ID) -> outputs.Message:
        user = info.context['user']
        messages = manager.filter(pk=id, sender=user, is_deleted=False)
        message = await messages.afirst()
        assert message, dumps({'code': 'ERR001'})
        utils.check_guest_scope(user, message.transfer_id)
        message.is_deleted = True
        message.deleted_time = timezone.now()
        message.deleted_by = user
        message.file = ''
        await message.asave(update_fields=[
            'is_deleted', 'deleted_time', 'deleted_by', 'file'
        ])
        await message.reactions.all().adelete()
        receiver_ids = await get_message_receiver_ids(message)
        args = (user.id, enums.Action.DELETE_MESSAGE, receiver_ids)
        kwargs = {
            'transfer_id': str(message.transfer_id),
            'message_id': str(message.id)
        }
        await utils.broadcast(*args, **kwargs)
        return message

    @sb.mutation(description='CUSTOMER, DRIVER, OPERATOR, ADMIN, STAFF, GUEST')
    @permission_required(
        'CUSTOMER', 'DRIVER', 'OPERATOR', 'ADMIN', 'STAFF', 'GUEST'
    )
    async def see_messages(
        root, info: Info,
        transfer_id: sb.ID,
        up_to_message_id: Optional[sb.ID] = None
    ) -> outputs.SuccessResponse:
        user = info.context['user']
        transfer = await models.Transfer.objects.filter(pk=transfer_id).afirst()
        assert transfer, dumps({'code': 'ERR013'})
        await utils.check_messaging_access(user, transfer)
        through_messages = user.through_messages.filter(
            transfer=transfer, is_seen=False
        )
        messages = get_status_messages(user).filter(transfer=transfer)
        if up_to_message_id:
            through_messages = through_messages.filter(
                message_id__lte=up_to_message_id
            )
            messages = messages.filter(pk__lte=up_to_message_id)
        await through_messages.aupdate(is_seen=True)
        await advance_messages(user, messages, MessageStatus.SEEN)
        await see_message_notifications(user.id, transfer)
        return outputs.SuccessResponse(success=True)

    @sb.mutation(description='CUSTOMER, DRIVER, OPERATOR, ADMIN, STAFF, GUEST')
    @permission_required(
        'CUSTOMER', 'DRIVER', 'OPERATOR', 'ADMIN', 'STAFF', 'GUEST'
    )
    async def deliver_messages(
        root, info: Info,
        transfer_id: Optional[sb.ID] = None
    ) -> outputs.SuccessResponse:
        """
        The client calls it when it gets a MESSAGE event (transfer_id) and when
        it connects (all transfers). Calling it again has no effect.
        """
        user = info.context['user']
        messages = get_status_messages(user)
        if transfer_id:
            messages = messages.filter(transfer_id=transfer_id)
        await advance_messages(user, messages, MessageStatus.DELIVERED)
        return outputs.SuccessResponse(success=True)

    @sb.mutation(description='CUSTOMER, DRIVER, OPERATOR, ADMIN, STAFF, GUEST')
    @permission_required(
        'CUSTOMER', 'DRIVER', 'OPERATOR', 'ADMIN', 'STAFF', 'GUEST'
    )
    async def react_message(
        root, info: Info,
        id: sb.ID,
        emoji: Optional[enums.ReactionEmoji] = None
    ) -> outputs.Message:
        """
        One reaction per user and message: emoji adds or replaces it, null
        removes it. No notification, only a MESSAGE_REACTION event.
        """
        user = info.context['user']
        messages = manager.select_related('transfer').filter(pk=id)
        message = await messages.afirst()
        assert message, dumps({'code': 'ERR001'})
        visible = await get_visible_messages(user, message.transfer)
        visible = await visible.filter(pk=message.pk).aexists()
        assert visible, dumps({'code': 'ERR001'})
        assert not message.is_deleted, dumps({'code': 'ERR021'})
        check_messaging_available(message.transfer)
        if emoji:
            await models.MessageReaction.objects.aupdate_or_create(
                message=message, user=user, defaults={'emoji': emoji}
            )
        else:
            reactions = message.reactions.filter(user=user)
            deleted, _ = await reactions.adelete()
            if not deleted:
                return message
        receiver_ids = await get_reaction_receiver_ids(user, message)
        args = (user.id, enums.Action.MESSAGE_REACTION, receiver_ids)
        kwargs = {
            'transfer_id': str(message.transfer_id),
            'message_id': str(message.id),
            'message': emoji.value if emoji else ''
        }
        await utils.broadcast(*args, **kwargs)
        return message
