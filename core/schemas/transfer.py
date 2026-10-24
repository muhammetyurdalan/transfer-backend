from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import activate, gettext_noop
from json import dumps
import strawberry as sb
import strawberry_django as sbd
from strawberry import Info
from strawberry_django.pagination import OffsetPaginationInput
from typing import List, Optional
from core import enums, inputs, models, outputs, psp, utils
from core.decorators import permission_required


User = utils.User
Status = enums.TransferStatus
Role = enums.Role
Action = enums.Action
ZERO = models.ZERO
EDITABLE_STATUSES = [Status.PENDING]
ASSIGNABLE_STATUSES = [Status.PAID, Status.ASSIGNED]
CANCELLABLE_STATUSES = [Status.PENDING, Status.PAID, Status.ASSIGNED]
TIPPABLE_STATUSES = [Status.COMPLETED]
STATUS_TRANSITIONS = {
    Status.ASSIGNED: Status.STARTED,
    Status.STARTED: Status.COMPLETED,
}
# Fields that change the vehicle options or the price
ROUTE_FIELDS = [
    'pickup_time', 'distance', 'duration', 'passenger_count',
    'luggage_count', 'child_seat_count', 'needs_wheelchair'
]


async def get_transfer(user, id, statuses=None, expand=()):
    transfers = utils.get_transfers(user, expand=expand).filter(pk=id)
    transfer = await transfers.afirst()
    assert transfer, dumps({'code': 'ERR013'})
    if statuses is not None:
        assert transfer.status in statuses, dumps({'code': 'ERR014'})
    return transfer


async def get_vehicle_options(transfer):
    """Eligible vehicle categories, each priced for this route."""
    categories = models.VehicleCategory.objects.eligible_for(transfer)
    return [
        outputs.VehicleOption(
            vehicle_category=category,
            distance=transfer.distance,
            duration=transfer.duration,
            price=category.get_price(transfer.distance, transfer.duration),
            currency=category.currency
        )
        async for category in categories
    ]


@sb.type
class Query:
    @sbd.field(description='ALL')
    @permission_required('ALL')
    async def transfer(
        root, info: Info,
        id: sb.ID
    ) -> Optional[outputs.Transfer]:
        user = info.context['user']
        return await utils.get_transfers(user).filter(pk=id).afirst()

    @sbd.field(description='ALL')
    @permission_required('ALL')
    async def transfers(
        root, info: Info,
        filters: Optional[outputs.TransferFilter] = sb.UNSET,
        ordering: Optional[List[outputs.TransferOrder]] = sb.UNSET,
        pagination: Optional[OffsetPaginationInput] = sb.UNSET
    ) -> outputs.TransfersPaginated:
        user = info.context['user']
        transfers = utils.get_transfers(user)
        if filters:
            transfers = sbd.filters.apply(filters, transfers, info)
        count = await utils.get_count(info, transfers)
        if ordering:
            args = (outputs.TransferOrder, ordering, info, transfers)
            transfers = sbd.ordering.apply_ordering(*args)
        if pagination:
            transfers = sbd.pagination.apply(pagination, transfers)
        return outputs.TransfersPaginated(transfers=transfers, count=count)

    @sbd.field(description='CUSTOMER, OPERATOR, ADMIN, STAFF')
    @permission_required('CUSTOMER', 'OPERATOR', 'ADMIN', 'STAFF')
    async def vehicle_options(
        root, info: Info,
        transfer_id: sb.ID
    ) -> List[outputs.VehicleOption]:
        user = info.context['user']
        transfer = await get_transfer(user, transfer_id)
        activate(user.language)
        return await get_vehicle_options(transfer)

    @sbd.field(description='OPERATOR, ADMIN, STAFF')
    @permission_required('OPERATOR', 'ADMIN', 'STAFF')
    async def available_drivers(
        root, info: Info,
        transfer_id: sb.ID,
        filters: Optional[outputs.UserFilter] = sb.UNSET,
        ordering: Optional[List[outputs.UserOrder]] = sb.UNSET,
        pagination: Optional[OffsetPaginationInput] = sb.UNSET
    ) -> outputs.UsersPaginated:
        """
        Drivers with an active vehicle in the transfer's vehicle category and
        no other assigned/started transfer at that time.
        """
        user = info.context['user']
        transfer = await get_transfer(user, transfer_id, ASSIGNABLE_STATUSES)
        drivers = User.objects.available_drivers(transfer)
        if filters:
            drivers = sbd.filters.apply(filters, drivers, info)
        count = await utils.get_count(info, drivers)
        if ordering:
            args = (outputs.UserOrder, ordering, info, drivers)
            drivers = sbd.ordering.apply_ordering(*args)
        if pagination:
            drivers = sbd.pagination.apply(pagination, drivers)
        return outputs.UsersPaginated(users=drivers, count=count)

    @sbd.field(description='OPERATOR, ADMIN, STAFF')
    @permission_required('OPERATOR', 'ADMIN', 'STAFF')
    async def available_vehicles(
        root, info: Info,
        transfer_id: sb.ID
    ) -> List[outputs.Vehicle]:
        """Suitable vehicles of the available drivers, with their driver."""
        user = info.context['user']
        transfer = await get_transfer(user, transfer_id, ASSIGNABLE_STATUSES)
        activate(user.language)
        return models.Vehicle.objects.available_for(transfer)


def check_transfer(transfer):
    valid = (
        transfer.distance > ZERO
        and transfer.duration > 0
        and transfer.passenger_count >= 1
        and transfer.luggage_count >= 0
        and transfer.child_seat_count >= 0
    )
    assert valid, dumps({'code': 'ERR030'})
    transfer.distance = transfer.distance.quantize(ZERO)


def check_pickup_time(pickup_time):
    assert pickup_time > timezone.now(), dumps({'code': 'ERR016'})


def clear_vehicle_selection(transfer):
    transfer.vehicle_category = None
    transfer.price = None
    transfer.currency = None


def get_payment_response(link, expire_time):
    if isinstance(expire_time, str):
        expire_time = parse_datetime(expire_time)
    return outputs.PaymentResponse(
        payment_link=link,
        link_expire_time=expire_time
    )


@sb.type
class Mutation:
    @sb.mutation(description='CUSTOMER')
    @permission_required('CUSTOMER')
    async def create_transfer(
        root, info: Info,
        data: inputs.CreateTransfer
    ) -> outputs.TransferResponse:
        """Creates a PENDING transfer and returns its vehicle options."""
        user = info.context['user']
        transfer = models.Transfer(customer=user)
        utils.set_attributes(transfer, data, set_nulls=False)
        check_transfer(transfer)
        check_pickup_time(transfer.pickup_time)
        await transfer.asave()
        activate(user.language)
        vehicle_options = await get_vehicle_options(transfer)
        return outputs.TransferResponse(
            transfer=transfer,
            vehicle_options=vehicle_options
        )

    @sb.mutation(description='CUSTOMER')
    @permission_required('CUSTOMER')
    async def update_transfer(
        root, info: Info,
        data: inputs.UpdateTransfer
    ) -> outputs.TransferResponse:
        """
        Only while PENDING. A route change clears the chosen vehicle category
        and expires its payment link, the customer chooses again.
        """
        user = info.context['user']
        expand = ['customer']
        transfer = await get_transfer(user, data.id, EDITABLE_STATUSES, expand)
        ignored = ['id']
        utils.set_attributes(transfer, data, ignored=ignored, set_nulls=False)
        check_transfer(transfer)
        check_pickup_time(transfer.pickup_time)
        fields = utils.get_fields(data)
        if any(fields.get(key) is not None for key in ROUTE_FIELDS):
            await psp.expire_payment_link(transfer)
            clear_vehicle_selection(transfer)
        await transfer.asave()
        activate(user.language)
        vehicle_options = await get_vehicle_options(transfer)
        return outputs.TransferResponse(
            transfer=transfer,
            vehicle_options=vehicle_options
        )

    @sb.mutation(description='CUSTOMER')
    @permission_required('CUSTOMER')
    async def pay_transfer(
        root, info: Info,
        data: inputs.PayTransfer
    ) -> outputs.PaymentResponse:
        """
        Sets the chosen vehicle category and its price, then returns a payment
        link. The transfer becomes PAID when the payment service reports a
        successful payment (psp-return), then operators are notified.
        """
        user = info.context['user']
        expand = ['customer']
        transfer = await get_transfer(user, data.id, EDITABLE_STATUSES, expand)
        check_pickup_time(transfer.pickup_time)
        categories = models.VehicleCategory.objects.eligible_for(transfer)
        categories = categories.filter(pk=data.vehicle_category_id)
        category = await categories.afirst()
        assert category, dumps({'code': 'ERR025'})
        transfer.vehicle_category = category
        transfer.price = category.get_price(transfer.distance, transfer.duration)
        transfer.currency = category.currency
        await psp.get_payment_link(transfer)
        await transfer.asave()
        return get_payment_response(
            transfer.psp_data['payment_link'],
            transfer.psp_data['payment_link_expire_time']
        )

    @sb.mutation(description='OPERATOR, ADMIN, STAFF')
    @permission_required('OPERATOR', 'ADMIN', 'STAFF')
    async def assign_driver(
        root, info: Info,
        data: inputs.AssignDriver
    ) -> outputs.Transfer:
        """PAID or ASSIGNED (reassignment) transfer, driver must be available."""
        user = info.context['user']
        transfer = await get_transfer(user, data.id, ASSIGNABLE_STATUSES)
        drivers = User.objects.available_drivers(transfer)
        drivers = drivers.filter(pk=data.driver_id)
        assert await drivers.aexists(), dumps({'code': 'ERR015'})
        vehicles = models.Vehicle.objects.suitable_for(transfer)
        vehicles = vehicles.filter(driver_id=data.driver_id)
        if data.vehicle_id:
            vehicles = vehicles.filter(pk=data.vehicle_id)
        vehicle = await vehicles.afirst()
        assert vehicle, dumps({'code': 'ERR027'})
        previous_driver_id = transfer.driver_id
        transfer.driver_id = vehicle.driver_id
        transfer.vehicle = vehicle
        transfer.status = Status.ASSIGNED
        if user.role == Role.OPERATOR:
            transfer.operator = user
        await transfer.asave()
        driver_id, customer_id = transfer.driver_id, transfer.customer_id
        if previous_driver_id and previous_driver_id != driver_id:
            await utils.notify_users(
                [previous_driver_id],
                gettext_noop('Transfer Unassigned'),
                gettext_noop('Transfer #%(id)s was assigned to another driver'),
                Action.TRANSFER_CANCELLED,
                transfer,
                sender_id=user.id
            )
            args = (user.id, Action.TRANSFER_CANCELLED, [previous_driver_id])
            await utils.broadcast(*args, transfer_id=str(transfer.id))
        await utils.notify_users(
            [driver_id],
            gettext_noop('New Transfer'),
            gettext_noop('Transfer #%(id)s was assigned to you'),
            Action.TRANSFER_ASSIGNED,
            transfer,
            sender_id=user.id
        )
        await utils.notify_users(
            [customer_id],
            gettext_noop('Driver Assigned'),
            gettext_noop('A driver was assigned to transfer #%(id)s'),
            Action.TRANSFER_ASSIGNED,
            transfer,
            sender_id=user.id
        )
        args = (user.id, Action.TRANSFER_ASSIGNED, [driver_id, customer_id])
        await utils.broadcast(*args, transfer_id=str(transfer.id))
        return transfer

    @sb.mutation(description='DRIVER, ADMIN, STAFF')
    @permission_required('DRIVER', 'ADMIN', 'STAFF')
    async def update_transfer_status(
        root, info: Info,
        data: inputs.UpdateTransferStatus
    ) -> outputs.Transfer:
        """ASSIGNED -> STARTED -> COMPLETED, drivers only their transfers."""
        user = info.context['user']
        statuses = list(STATUS_TRANSITIONS)
        transfer = await get_transfer(user, data.id, statuses)
        valid = STATUS_TRANSITIONS[transfer.status] == data.status
        assert valid, dumps({'code': 'ERR014'})
        transfer.status = data.status
        await transfer.asave()
        receiver_ids = utils.get_receiver_ids(user.id, transfer)
        await utils.notify_users(
            receiver_ids,
            gettext_noop('Transfer Status Updated'),
            gettext_noop('Transfer #%(id)s status was updated'),
            Action.TRANSFER_STATUS_UPDATED,
            transfer,
            sender_id=user.id
        )
        args = (user.id, Action.TRANSFER_STATUS_UPDATED, receiver_ids)
        await utils.broadcast(*args, transfer_id=str(transfer.id))
        return transfer

    @sb.mutation(description='CUSTOMER, OPERATOR, ADMIN, STAFF')
    @permission_required('CUSTOMER', 'OPERATOR', 'ADMIN', 'STAFF')
    async def cancel_transfer(
        root, info: Info,
        id: sb.ID
    ) -> outputs.Transfer:
        """
        Unpaid: the payment link is expired. Paid: the payment is refunded,
        a customer's cancellation keeps the CANCEL_PENALTY_RULES share.
        """
        user = info.context['user']
        transfer = await get_transfer(user, id, CANCELLABLE_STATUSES)
        now = timezone.now()
        if transfer.status == Status.PENDING:
            await psp.expire_payment_link(transfer)
        else:
            penalty_rate = ZERO
            if user.role == Role.CUSTOMER:
                penalty_rate = psp.get_penalty_rate(transfer, now)
            await psp.refund_cancelled_transfer(transfer, penalty_rate)
        transfer.status = Status.CANCELLED
        await transfer.asave()
        receiver_ids = utils.get_receiver_ids(user.id, transfer)
        await utils.notify_users(
            receiver_ids,
            gettext_noop('Transfer Cancelled'),
            gettext_noop('Transfer #%(id)s was cancelled'),
            Action.TRANSFER_CANCELLED,
            transfer,
            sender_id=user.id
        )
        args = (user.id, Action.TRANSFER_CANCELLED, receiver_ids)
        await utils.broadcast(*args, transfer_id=str(transfer.id))
        return transfer

    @sb.mutation(description='CUSTOMER')
    @permission_required('CUSTOMER')
    async def tip_driver(
        root, info: Info,
        data: inputs.TipDriver
    ) -> outputs.PaymentResponse:
        """
        Tip link for a COMPLETED transfer. The TIP transaction is created and
        the driver is notified when the payment service reports success.
        """
        user = info.context['user']
        expand = ['customer', 'driver']
        transfer = await get_transfer(user, data.id, TIPPABLE_STATUSES, expand)
        amount = data.amount.quantize(ZERO)
        valid = amount > ZERO and transfer.driver_id and transfer.currency
        assert valid, dumps({'code': 'ERR033'})
        await psp.get_tip_link(transfer, amount)
        await transfer.asave(update_fields=['psp_data', 'updated_at'])
        return get_payment_response(
            transfer.psp_data['tip_link'],
            transfer.psp_data['tip_link_expire_time']
        )
