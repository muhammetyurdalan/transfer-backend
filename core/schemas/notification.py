import strawberry as sb
import strawberry_django as sbd
from strawberry import Info
from strawberry_django.pagination import OffsetPaginationInput
from typing import List, Optional
from core import inputs, outputs, utils
from core.decorators import permission_required


@sb.type
class Query:
    @sbd.field(description='ALL')
    @permission_required('ALL')
    async def notifications(
        root, info: Info,
        filters: Optional[outputs.NotificationFilter] = sb.UNSET,
        ordering: Optional[List[outputs.NotificationOrder]] = sb.UNSET,
        pagination: Optional[OffsetPaginationInput] = sb.UNSET
    ) -> outputs.NotificationsPaginated:
        user = info.context['user']
        notifications = user.notifications.all()
        if filters:
            args = (filters, notifications, info)
            notifications = sbd.filters.apply(*args)
        count = await utils.get_count(info, notifications)
        if ordering:
            args = (outputs.NotificationOrder, ordering, info, notifications)
            notifications = sbd.ordering.apply_ordering(*args)
        if pagination:
            notifications = sbd.pagination.apply(pagination, notifications)
        kwargs = {'notifications': notifications, 'count': count}
        return outputs.NotificationsPaginated(**kwargs)

    @sbd.field(description='ALL')
    @permission_required('ALL')
    async def unseen_notification_count(root, info: Info) -> int:
        user = info.context['user']
        return await user.notifications.filter(is_seen=False).acount()


@sb.type
class Mutation:
    @sb.mutation(description='ALL')
    @permission_required('ALL')
    async def see_notifications(
        root, info: Info,
        ids: Optional[List[sb.ID]] = sb.UNSET
    ) -> outputs.SuccessResponse:
        user = info.context['user']
        notifications = user.notifications.filter(is_seen=False)
        if ids:
            notifications = notifications.filter(id__in=ids)
        await notifications.aupdate(is_seen=True)
        return outputs.SuccessResponse(success=True)

    @sb.mutation(description='ALL')
    @permission_required('ALL')
    async def delete_notifications(
        root, info: Info,
        ids: Optional[List[sb.ID]] = sb.UNSET
    ) -> outputs.SuccessResponse:
        user = info.context['user']
        notifications = user.notifications.all()
        if ids:
            notifications = notifications.filter(id__in=ids)
        await notifications.adelete()
        return outputs.SuccessResponse(success=True)

    @sb.mutation(description='ADMIN, STAFF')
    @permission_required('ADMIN', 'STAFF')
    async def send_notification(
        root, info: Info,
        data: inputs.SendNotification
    ) -> outputs.SuccessResponse:
        sender = info.context['user']
        kwargs = utils.get_fields(data, ignored=['user_ids'])
        await utils.create_notifications(
            data.user_ids, sender_id=sender.id, **kwargs
        )
        return outputs.SuccessResponse(success=True)
