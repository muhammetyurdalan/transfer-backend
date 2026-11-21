from decimal import ROUND_HALF_UP
from django.db.models import Q
from django.db.models.aggregates import Count
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
VerifyStatus = enums.VerifyStatus
ZERO = models.ZERO
EDITABLE_STATUSES = [Status.PENDING]
ASSIGNABLE_STATUSES = [Status.ACCEPTED]
CANCELLABLE_STATUSES = [Status.PENDING, Status.ACCEPTED]
DELETABLE_STATUSES = [Status.EXPIRED, Status.WITHDRAWN]
TIPPABLE_STATUSES = [Status.COMPLETED]
STATUS_TRANSITIONS = {
    Status.ACCEPTED: [Status.STARTED, Status.COMPLETED],
    Status.STARTED: [Status.COMPLETED],
}
ROUTE_FIELDS = [
    'pickup_time', 'distance', 'duration', 'passenger_count',
    'luggage_count', 'child_seat_count', 'needs_wheelchair'
]
DISTRICT_NAME_FIELDS = ['pickup_district_name', 'dropoff_district_name']
EXTERNAL_DRIVER_FIELDS = [
    'driver_name', 'driver_phone', 'plate', 'vehicle_brand', 'vehicle_model'
]
PRICE_FIELDS = ['driver_price', 'driver_price_currency']
DRIVER_EDITABLE_STATUSES = [Status.ACCEPTED, Status.STARTED]
IGNORED_CREATE_FIELDS = [*DISTRICT_NAME_FIELDS, 'legs']


async def get_transfer(user, id, statuses=None, expand=()):
    transfers = utils.get_transfers(user, expand=expand).filter(pk=id)
    transfer = await transfers.afirst()
    assert transfer, dumps({'code': 'ERR013'})
    if statuses is not None:
        assert transfer.status in statuses, dumps({'code': 'ERR014'})
    return transfer


def allows_multiple_vehicles(transfer):
    """
    State.allow_multiple_vehicles of the transfer's state, which must be
    loaded (expand=['state'] or set_districts).
    """
    return bool(transfer.state_id and transfer.state.allow_multiple_vehicles)


def get_vehicle_categories(transfer):
    multiple = allows_multiple_vehicles(transfer)
    manager = models.VehicleCategory.objects
    return manager.options_for(transfer, multiple), multiple


async def get_vehicle_options(transfer):
    """
    Eligible vehicle categories, each priced for this route, with the number
    of vehicles of the category the transfer needs (always 1 unless the
    state allows multiple vehicles).
    """
    categories, _ = get_vehicle_categories(transfer)
    return [
        outputs.VehicleOption(
            vehicle_category=category,
            count=category.count,
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
        activate(user.language)
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
        activate(user.language)
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
        transfer = await get_transfer(user, transfer_id, expand=['state'])
        activate(user.language)
        return await get_vehicle_options(transfer)
    
    @sbd.field(description='Public')
    async def check_districts(
        root, info: Info,
        data: inputs.CheckDistricts
    ) -> outputs.SuccessResponse:
        pickup = await get_district(None, data, 'pickup')
        dropoff = await get_district(None, data, 'dropoff')
        return outputs.SuccessResponse(success=bool(pickup or dropoff))

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
        expand = ['state']
        args = (user, transfer_id, ASSIGNABLE_STATUSES, expand)
        transfer = await get_transfer(*args)
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
        expand = ['state']
        args = (user, transfer_id, ASSIGNABLE_STATUSES, expand)
        transfer = await get_transfer(*args)
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


async def get_district(transfer, data, prefix, require_serviceable=True):
    """By name if given, otherwise the transfer's current district."""
    districts = models.District.objects.values('id', 'city__state_id')
    district_name = getattr(data, f'{prefix}_district_name')
    if district_name:
        filters = utils.get_district_filters(district_name)
        if require_serviceable:
            filters &= Q(**{
                'is_active': True, 
                'users__role': 'OPERATOR', 
                'users__is_active': True,
                'users__status': VerifyStatus.ACCEPTED
            })
        return await districts.filter(filters).distinct().afirst()
    district_id = getattr(transfer, f'{prefix}_district_id')
    if district_id:
        return await districts.filter(pk=district_id).afirst()
    return None


async def set_districts(transfer, data):
    """
    Sets the pickup and dropoff districts. At least one must be found, the
    state is the pickup district's state, or the dropoff's if not found.
    """
    pickup = await get_district(transfer, data, 'pickup')
    dropoff = await get_district(transfer, data, 'dropoff')
    assert pickup or dropoff, dumps({'code': 'ERR035'})
    transfer.pickup_district_id = pickup['id'] if pickup else None
    transfer.dropoff_district_id = dropoff['id'] if dropoff else None
    state_id = (pickup or dropoff)['city__state_id']
    # The state is loaded once here (get_vehicle_options reads it), and only
    # again when the districts move the transfer to another state
    loaded = models.Transfer.state.is_cached(transfer)
    if state_id != transfer.state_id or not loaded:
        transfer.state = await models.State.objects.aget(pk=state_id)
    await set_operator(transfer)


async def set_operator(transfer):
    """Sets the first active, ACCEPTED operator of the transfer's districts."""
    assert transfer.state_id, dumps({'code': 'ERR035'})
    district_ids = [transfer.pickup_district_id, transfer.dropoff_district_id]
    operators = User.objects.filter(
        role=Role.OPERATOR,
        is_active=True,
        status=VerifyStatus.ACCEPTED,
        districts__id__in=district_ids
    ).annotate(
        pickup_district=Count('districts', 
            filter=Q(districts__id=transfer.pickup_district_id), distinct=True
        ),
        transfer_count=Count('operator_transfers', 
            filter=Q(operator_transfers__status='ACCEPTED'), distinct=True
        )
    )
    operators = operators.order_by('-pickup_district', 'transfer_count')
    operator = await operators.afirst()
    assert operator, dumps({'code': 'ERR035'})
    transfer.operator = operator


def check_legs(legs):
    for leg in legs or []:
        valid = (
            leg.start_time < leg.end_time
            and (leg.order is sb.UNSET or leg.order is None or leg.order >= 0)
        )
        assert valid, dumps({'code': 'ERR030'})


async def set_legs(transfer, legs):
    """Saves the legs, order defaults to the position in the list."""
    instances = []
    for index, data in enumerate(legs or []):
        leg = models.Leg(transfer=transfer, order=index)
        utils.set_attributes(leg, data, set_nulls=False)
        instances.append(leg)
    if instances:
        await models.Leg.objects.abulk_create(instances)


def check_pickup_time(pickup_time):
    assert pickup_time > timezone.now(), dumps({'code': 'ERR016'})


def set_driver_fields(transfer_driver, data):
    """
    Price fields, and the name, phone, plate and vehicle brand/model of an
    external driver (an internal driver's are taken from the user and the
    vehicle).
    """
    external = transfer_driver.driver_id is None
    keys = PRICE_FIELDS
    if external:
        keys = keys + EXTERNAL_DRIVER_FIELDS
    fields = {
        key: value.strip() if isinstance(value, str) else value
        for key, value in utils.get_fields(data).items()
        if key in keys
    }
    utils.set_attributes(transfer_driver, fields, set_nulls=False)
    if price := data.driver_price:
        transfer_driver.driver_price = max(price, ZERO).quantize(ZERO)
    if external:
        valid = bool(transfer_driver.driver_name)
        valid = valid and len(transfer_driver.driver_name) <= 100
        valid = valid and len(transfer_driver.driver_phone) <= 20
        valid = valid and len(transfer_driver.plate) <= 20
        valid = valid and len(transfer_driver.vehicle_brand) <= 50
        valid = valid and len(transfer_driver.vehicle_model) <= 50
        assert valid, dumps({'code': 'ERR030'})


def get_driver_name(driver):
    return f'{driver.first_name} {driver.last_name}'.strip()[:100]


async def get_transfer_driver(user, id, statuses):
    """Assignment of a transfer the user can see, in one of the statuses."""
    transfer_drivers = models.TransferDriver.objects.select_related(
        'transfer', 'transfer__state'
    ).filter(pk=id, transfer__in=utils.get_transfers(user))
    transfer_driver = await transfer_drivers.afirst()
    assert transfer_driver, dumps({'code': 'ERR049'})
    status = transfer_driver.transfer.status
    assert status in statuses, dumps({'code': 'ERR014'})
    return transfer_driver


async def get_tip_driver(transfer, transfer_driver_id):
    """
    The internal driver to tip: the given assignment, or the only internal
    driver of the transfer. ERR033 if there is none (external drivers can't
    be tipped), ERR030 if there are several and none is given.
    """
    drivers = models.TransferDriver.objects.select_related('driver').filter(
        transfer=transfer, driver__isnull=False
    )
    if transfer_driver_id:
        drivers = drivers.filter(pk=transfer_driver_id)
    transfer_drivers = [transfer_driver async for transfer_driver in drivers[:2]]
    assert transfer_drivers, dumps({'code': 'ERR033'})
    assert len(transfer_drivers) == 1, dumps({'code': 'ERR030'})
    return transfer_drivers[0].driver


async def notify_unassigned(user, transfer, driver_id):
    await utils.notify_users(
        [driver_id],
        gettext_noop('Transfer Unassigned'),
        gettext_noop('Transfer #%(id)s was unassigned from you'),
        Action.TRANSFER_CANCELLED,
        transfer,
        sender_id=user.id
    )
    args = (user.id, Action.TRANSFER_CANCELLED, [driver_id])
    await utils.broadcast(*args, transfer_id=str(transfer.id))


def clear_vehicle_selection(transfer):
    transfer.vehicle_category = None
    transfer.vehicle_count = 1
    transfer.customer_price = None
    transfer.cancel_price = ZERO
    transfer.currency = None


def get_cancel_price(transfer, refund_option):
    """Cancellation protection fee, ZERO for the standard refund."""
    if refund_option != enums.RefundOption.UNCONDITIONAL:
        return ZERO
    state = transfer.state
    allowed = state is not None and state.allow_unconditional_refund
    assert allowed, dumps({'code': 'ERR055'})
    rate = state.cancel_price_rate
    # Half up, like the client's preview
    return (transfer.customer_price * rate).quantize(ZERO, ROUND_HALF_UP)


def get_payment_response(link, expire_time):
    if isinstance(expire_time, str):
        expire_time = parse_datetime(expire_time)
    return outputs.PaymentResponse(
        payment_link=link,
        link_expire_time=expire_time
    )


def cancellation_category(user, transfer):
    """Reason category a user may cancel this transfer with."""
    if user.role == Role.CUSTOMER:
        if transfer.status == Status.PENDING:
            return enums.ReasonCategory.CUSTOMER_TERMINATION
        return enums.ReasonCategory.CUSTOMER_CANCELLATION
    return enums.ReasonCategory.OPERATOR_CANCELLATION


async def set_cancellation_reason(user, transfer, data):
    """
    Exactly one of an active Reason (ERR050, wrong category ERR052) or a
    3-200 chars free text (ERR030). Neither: ERR051.
    """
    reason_id = data.cancellation_reason_id or None
    text = (data.cancellation_reason_text or '').strip()
    assert reason_id or text, dumps({'code': 'ERR051'})
    assert not (reason_id and text), dumps({'code': 'ERR030'})
    if text:
        assert 3 <= len(text) <= 200, dumps({'code': 'ERR030'})
        transfer.cancellation_reason_text = text
        return
    reasons = models.Reason.objects.filter(is_active=True)
    reason = await reasons.filter(pk=reason_id).afirst()
    assert reason, dumps({'code': 'ERR050'})
    category = cancellation_category(user, transfer)
    assert reason.category == category, dumps({'code': 'ERR052'})
    transfer.cancellation_reason = reason


@sb.type
class Mutation:
    @sb.mutation(description='CUSTOMER, OPERATOR, ADMIN, STAFF')
    @permission_required('CUSTOMER', 'OPERATOR', 'ADMIN', 'STAFF')
    async def create_transfer(
        root, info: Info,
        data: inputs.CreateTransfer
    ) -> outputs.TransferResponse:
        """Creates a PENDING transfer and returns its vehicle options."""
        user = info.context['user']
        customer_id = user.id
        if user.role != Role.CUSTOMER and data.customer_id:
            user = await User.objects.filter(pk=data.customer_id).afirst()
            assert user and user.is_active, dumps({'code': 'ERR010'})
            customer_id = data.customer_id
        assert user.phone.strip(), dumps({'code': 'ERR043'})
        transfer = models.Transfer(customer_id=customer_id)
        ignored = IGNORED_CREATE_FIELDS
        utils.set_attributes(transfer, data, ignored=ignored, set_nulls=False)
        check_transfer(transfer)
        check_pickup_time(transfer.pickup_time)
        check_legs(data.legs)
        await set_districts(transfer, data)
        await transfer.asave()
        await set_legs(transfer, data.legs)
        activate(user.language)
        vehicle_options = await get_vehicle_options(transfer)
        return outputs.TransferResponse(
            transfer=transfer,
            vehicle_options=vehicle_options
        )

    @sb.mutation(description='CUSTOMER, OPERATOR, ADMIN, STAFF')
    @permission_required('CUSTOMER', 'OPERATOR', 'ADMIN', 'STAFF')
    async def update_transfer(
        root, info: Info,
        data: inputs.UpdateTransfer
    ) -> outputs.TransferResponse:
        """
        Only while PENDING. A route change (route fields or legs) clears the
        chosen vehicle category and expires its payment link, the customer
        chooses again. Given legs replace all of the transfer's legs.
        """
        user = info.context['user']
        expand = ['customer', 'state']
        fields = {
            key: value for key, value in utils.get_fields(data).items()
            if value is not None
        }
        transfer = await get_transfer(user, data.id, EDITABLE_STATUSES, expand)
        ignored = ['id', 'legs', *DISTRICT_NAME_FIELDS]
        utils.set_attributes(transfer, data, ignored=ignored, set_nulls=False)
        check_transfer(transfer)
        check_pickup_time(transfer.pickup_time)
        legs_given = data.legs is not sb.UNSET and data.legs is not None
        if legs_given:
            check_legs(data.legs)
        if any(fields.get(key) for key in DISTRICT_NAME_FIELDS):
            await set_districts(transfer, data)
        route_changed = any(fields.get(key) is not None for key in ROUTE_FIELDS)
        if route_changed or legs_given:
            await psp.expire_payment_link(transfer)
            clear_vehicle_selection(transfer)
        await transfer.asave()
        if legs_given:
            await transfer.legs.all().adelete()
            await set_legs(transfer, data.legs)
        activate(user.language)
        vehicle_options = await get_vehicle_options(transfer)
        return outputs.TransferResponse(
            transfer=transfer,
            vehicle_options=vehicle_options
        )

    @sb.mutation(description='CUSTOMER, OPERATOR, ADMIN, STAFF')
    @permission_required('CUSTOMER', 'OPERATOR', 'ADMIN', 'STAFF')
    async def pay_transfer(
        root, info: Info,
        data: inputs.PayTransfer
    ) -> outputs.PaymentResponse:
        """
        Sets the chosen vehicle category, vehicle count (at least the
        category's count in vehicleOptions, only 1 unless the state allows
        multiple vehicles), customerPrice (unit price * count) and with
        refundOption UNCONDITIONAL cancelPrice (ERR055 if the state doesn't
        allow it), then returns a payment link for price (customerPrice +
        cancelPrice). The transfer becomes ACCEPTED when the payment service
        reports a successful payment (psp-return), then operators are
        notified.
        """
        user = info.context['user']
        expand = ['customer', 'state']
        transfer = await get_transfer(user, data.id, EDITABLE_STATUSES, expand)
        check_pickup_time(transfer.pickup_time)
        categories, multiple = get_vehicle_categories(transfer)
        categories = categories.filter(pk=data.vehicle_category_id)
        category = await categories.afirst()
        assert category, dumps({'code': 'ERR025'})
        vehicle_count = data.vehicle_count
        if vehicle_count in (sb.UNSET, None):
            vehicle_count = category.count
        assert vehicle_count >= category.count, dumps({'code': 'ERR046'})
        assert multiple or vehicle_count == 1, dumps({'code': 'ERR047'})
        price = category.get_price(transfer.distance, transfer.duration)
        transfer.vehicle_category = category
        transfer.vehicle_count = vehicle_count
        transfer.customer_price = price * vehicle_count
        transfer.cancel_price = get_cancel_price(transfer, data.refund_option)
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
        """
        Adds a driver to an ACCEPTED transfer, at most its vehicleCount
        (ERR048). With driverId an internal driver (one of availableDrivers)
        and one of their suitable vehicles, without it an external driver
        (driverName required). The assignment's vehicleCategory is the
        transfer's. The transfer's status does not change.
        """
        user = info.context['user']
        expand = ['state']
        args = (user, data.id, ASSIGNABLE_STATUSES, expand)
        transfer = await get_transfer(*args)
        count = await transfer.drivers.acount()
        assert count < transfer.vehicle_count, dumps({'code': 'ERR048'})
        transfer_driver = models.TransferDriver(
            transfer=transfer,
            vehicle_category_id=transfer.vehicle_category_id
        )
        driver_id = data.driver_id or None
        if driver_id:
            drivers = User.objects.available_drivers(transfer)
            drivers = drivers.filter(pk=driver_id)
            assert await drivers.aexists(), dumps({'code': 'ERR015'})
            vehicles = models.Vehicle.objects.suitable_for(transfer)
            vehicles = vehicles.filter(driver_id=driver_id)
            if data.vehicle_id:
                vehicles = vehicles.filter(pk=data.vehicle_id)
            vehicle = await vehicles.select_related('driver').afirst()
            assert vehicle, dumps({'code': 'ERR027'})
            transfer_driver.driver = vehicle.driver
            transfer_driver.vehicle = vehicle
            transfer_driver.driver_name = get_driver_name(vehicle.driver)
            transfer_driver.plate = vehicle.plate
            transfer_driver.vehicle_brand = vehicle.brand
            transfer_driver.vehicle_model = vehicle.model
        else:
            assert not data.vehicle_id, dumps({'code': 'ERR030'})
        set_driver_fields(transfer_driver, data)
        if user.role == Role.OPERATOR:
            transfer.operator = user
            await transfer.asave(update_fields=['operator', 'updated_at'])
        await transfer_driver.asave()
        receiver_ids = [transfer.customer_id]
        if driver_id:
            receiver_ids.append(transfer_driver.driver_id)
            await utils.notify_users(
                [transfer_driver.driver_id],
                gettext_noop('New Transfer'),
                gettext_noop('Transfer #%(id)s was assigned to you'),
                Action.TRANSFER_ASSIGNED,
                transfer,
                sender_id=user.id
            )
        args = (user.id, Action.TRANSFER_ASSIGNED, receiver_ids)
        await utils.broadcast(*args, transfer_id=str(transfer.id))
        return transfer

    @sb.mutation(description='OPERATOR, ADMIN, STAFF')
    @permission_required('OPERATOR', 'ADMIN', 'STAFF')
    async def update_transfer_driver(
        root, info: Info,
        data: inputs.UpdateTransferDriver
    ) -> outputs.TransferDriver:
        """
        ACCEPTED or STARTED transfer. Price fields, and the name, phone,
        plate and vehicle brand/model of an external driver. To change the driver, remove the
        assignment and assign again.
        """
        user = info.context['user']
        args = (user, data.id, DRIVER_EDITABLE_STATUSES)
        transfer_driver = await get_transfer_driver(*args)
        set_driver_fields(transfer_driver, data)
        await transfer_driver.asave()
        return transfer_driver

    @sb.mutation(description='OPERATOR, ADMIN, STAFF')
    @permission_required('OPERATOR', 'ADMIN', 'STAFF')
    async def remove_transfer_driver(
        root, info: Info,
        id: sb.ID
    ) -> outputs.Transfer:
        """ACCEPTED transfer, an internal driver is notified."""
        user = info.context['user']
        args = (user, id, ASSIGNABLE_STATUSES)
        transfer_driver = await get_transfer_driver(*args)
        transfer = transfer_driver.transfer
        driver_id = transfer_driver.driver_id
        await transfer_driver.adelete()
        if driver_id:
            await notify_unassigned(user, transfer, driver_id)
        args = (user.id, Action.TRANSFER_ASSIGNED, [transfer.customer_id])
        await utils.broadcast(*args, transfer_id=str(transfer.id))
        return transfer

    @sb.mutation(description='DRIVER, ADMIN, STAFF')
    @permission_required('DRIVER', 'ADMIN', 'STAFF')
    async def update_transfer_status(
        root, info: Info,
        data: inputs.UpdateTransferStatus
    ) -> outputs.Transfer:
        """
        ACCEPTED -> STARTED -> COMPLETED or ACCEPTED -> COMPLETED (start and
        end at once), drivers only their transfers.
        """
        user = info.context['user']
        statuses = list(STATUS_TRANSITIONS)
        transfer = await get_transfer(user, data.id, statuses)
        valid = data.status in STATUS_TRANSITIONS[transfer.status]
        assert valid, dumps({'code': 'ERR014'})
        transfer.status = data.status
        await transfer.asave()
        receiver_ids = await utils.get_receiver_ids(user.id, transfer)
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
        data: inputs.CancelTransfer
    ) -> outputs.Transfer:
        """
        Unpaid: the payment link is expired. Paid: the payment is refunded,
        a customer's cancellation keeps the CANCEL_PENALTY_RULES share.
        A reason is required: an active `reasons` item of the caller's
        category (customer PENDING: CUSTOMER_TERMINATION, customer ACCEPTED:
        CUSTOMER_CANCELLATION, others: OPERATOR_CANCELLATION) or a free text.
        """
        user = info.context['user']
        expand = ['state']
        args = (user, data.id, CANCELLABLE_STATUSES, expand)
        transfer = await get_transfer(*args)
        await set_cancellation_reason(user, transfer, data)
        cancel_time = timezone.now()
        if transfer.status == Status.PENDING:
            await psp.expire_payment_link(transfer)
            transfer.status = Status.WITHDRAWN
            action = Action.TRANSFER_WITHDRAWN
        else:
            await psp.refund_cancelled_transfer(user, transfer, cancel_time)
            transfer.status = Status.CANCELLED
            action = Action.TRANSFER_CANCELLED
        await transfer.asave()
        receiver_ids = await utils.get_receiver_ids(user.id, transfer)
        await utils.notify_users(
            receiver_ids,
            gettext_noop('Transfer Cancelled'),
            gettext_noop('Transfer #%(id)s was cancelled'),
            action,
            transfer,
            sender_id=user.id
        )
        args = (user.id, action, receiver_ids)
        await utils.broadcast(*args, transfer_id=str(transfer.id))
        # notify_users activates each receiver's language
        activate(user.language)
        return transfer

    @sb.mutation(description='CUSTOMER, OPERATOR, ADMIN, STAFF')
    @permission_required('CUSTOMER', 'OPERATOR', 'ADMIN', 'STAFF')
    async def delete_transfer(
        root, info: Info,
        id: sb.ID
    ) -> outputs.SuccessResponse:
        """
        Only EXPIRED and WITHDRAWN transfers (never paid) can be deleted
        (ERR014). Legs, messages and transactions are deleted with it.
        """
        user = info.context['user']
        transfer = await get_transfer(user, id, DELETABLE_STATUSES)
        await transfer.adelete()
        return outputs.SuccessResponse(success=True)

    @sb.mutation(description='CUSTOMER')
    @permission_required('CUSTOMER')
    async def tip_driver(
        root, info: Info,
        data: inputs.TipDriver
    ) -> outputs.PaymentResponse:
        """
        Tip link for a COMPLETED transfer, to one of its internal drivers
        (transferDriverId, required if it has more than one). The TIP
        transaction is created and the driver is notified when the payment
        service reports success.
        """
        user = info.context['user']
        expand = ['customer', 'state']
        transfer = await get_transfer(user, data.id, TIPPABLE_STATUSES, expand)
        amount = data.amount.quantize(ZERO)
        valid = amount > ZERO and transfer.currency
        assert valid, dumps({'code': 'ERR033'})
        driver = await get_tip_driver(transfer, data.transfer_driver_id)
        await psp.get_tip_link(transfer, amount, driver)
        await transfer.asave(update_fields=['psp_data', 'updated_at'])
        return get_payment_response(
            transfer.psp_data['tip_link'],
            transfer.psp_data['tip_link_expire_time']
        )
