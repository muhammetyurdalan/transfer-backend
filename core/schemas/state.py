from decimal import Decimal, InvalidOperation
from django.db.models import ProtectedError
from json import dumps
import strawberry as sb
import strawberry_django as sbd
from strawberry import Info
from strawberry.exceptions import StrawberryGraphQLError as Error
from strawberry_django.pagination import OffsetPaginationInput
from typing import List, Optional
from core import inputs, models, outputs, utils
from core.decorators import permission_required


manager = models.State.objects


@sb.type
class Query:
    @sbd.field(description='ADMIN, STAFF, OPERATOR')
    @permission_required('ADMIN', 'STAFF', 'OPERATOR')
    async def state(
        root, info: Info,
        id: sb.ID
    ) -> Optional[outputs.State]:
        return await manager.filter(pk=id).afirst()

    @sbd.field(description='ADMIN, STAFF, OPERATOR')
    @permission_required('ADMIN', 'STAFF', 'OPERATOR')
    async def states(
        root, info: Info,
        filters: Optional[outputs.StateFilter] = sb.UNSET,
        ordering: Optional[List[outputs.StateOrder]] = sb.UNSET,
        pagination: Optional[OffsetPaginationInput] = sb.UNSET
    ) -> outputs.StatesPaginated:
        states = manager.all()
        if filters:
            states = sbd.filters.apply(filters, states, info)
        count = await utils.get_count(info, states)
        if ordering:
            args = (outputs.StateOrder, ordering, info, states)
            states = sbd.ordering.apply_ordering(*args)
        if pagination:
            states = sbd.pagination.apply(pagination, states)
        return outputs.StatesPaginated(states=states, count=count)


def check_penalty_rules(hours, rates):
    """Hours ascending and positive, rates in [0, 1], one rate per hour."""
    valid = len(hours) == len(rates)
    valid = valid and all(hour > 0 for hour in hours)
    valid = valid and all(a < b for a, b in zip(hours, hours[1:]))
    valid = valid and all(0 <= rate <= 1 for rate in rates)
    assert valid, dumps({'code': 'ERR030'})


async def check_state(state):
    valid = bool(state.name.strip() and state.code.strip() and state.psp)
    valid = valid and state.driver_buffer_minutes >= 0
    assert valid, dumps({'code': 'ERR030'})
    try:
        # Saved rates are strings (DjangoJSONEncoder), new ones Decimal
        rates = [Decimal(str(rate)) for rate in state.customer_imposed_rates]
    except InvalidOperation:
        raise Error(dumps({'code': 'ERR030'}))
    check_penalty_rules(state.customer_imposed_hours, rates)
    state.customer_imposed_rates = [str(rate) for rate in rates]
    codes = manager.filter(code__iexact=state.code).exclude(pk=state.pk)
    assert not await codes.aexists(), dumps({'code': 'ERR038'})


@sb.type
class Mutation:
    @sb.mutation(description='ADMIN, STAFF')
    @permission_required('ADMIN', 'STAFF')
    async def create_state(
        root, info: Info,
        data: inputs.CreateState
    ) -> outputs.State:
        state = models.State()
        utils.set_attributes(state, data, set_nulls=False)
        await check_state(state)
        await state.asave()
        return state

    @sb.mutation(description='ADMIN, STAFF')
    @permission_required('ADMIN', 'STAFF')
    async def update_state(
        root, info: Info,
        data: inputs.UpdateState
    ) -> outputs.State:
        state = await manager.filter(pk=data.id).afirst()
        assert state, dumps({'code': 'ERR036'})
        utils.set_attributes(state, data, ignored=['id'], set_nulls=False)
        await check_state(state)
        await state.asave()
        return state

    @sb.mutation(
        description='ADMIN, STAFF. Deletes its cities and districts too'
    )
    @permission_required('ADMIN', 'STAFF')
    async def delete_state(
        root, info: Info,
        id: sb.ID
    ) -> outputs.SuccessResponse:
        try:
            deleted, _ = await manager.filter(pk=id).adelete()
        except ProtectedError:
            # Transfers use the state or its districts
            raise Error(dumps({'code': 'ERR037'}))
        assert deleted, dumps({'code': 'ERR036'})
        return outputs.SuccessResponse(success=True)
