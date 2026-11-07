from django.db.models import ProtectedError
from json import dumps
import strawberry as sb
import strawberry_django as sbd
from strawberry import Info
from strawberry.exceptions import StrawberryGraphQLError as Error
from strawberry_django.pagination import OffsetPaginationInput
from typing import List, Optional
from core import inputs, models, outputs, utils
from core.decorators import get_optional_user, permission_required


manager = models.City.objects


@sb.type
class Query:
    @sbd.field(description='Public')
    async def city(root, info: Info, id: sb.ID) -> Optional[outputs.City]:
        # Loads the user (if a token is sent) for permissions.json
        await get_optional_user(info)
        return await manager.filter(pk=id).afirst()

    @sbd.field(description='Public')
    async def cities(
        root, info: Info,
        filters: Optional[outputs.CityFilter] = sb.UNSET,
        ordering: Optional[List[outputs.CityOrder]] = sb.UNSET,
        pagination: Optional[OffsetPaginationInput] = sb.UNSET
    ) -> outputs.CitiesPaginated:
        await get_optional_user(info)
        cities = manager.all()
        if filters:
            cities = sbd.filters.apply(filters, cities, info)
        count = await utils.get_count(info, cities)
        if ordering:
            args = (outputs.CityOrder, ordering, info, cities)
            cities = sbd.ordering.apply_ordering(*args)
        if pagination:
            cities = sbd.pagination.apply(pagination, cities)
        return outputs.CitiesPaginated(cities=cities, count=count)


async def check_city(city):
    assert city.name.strip(), dumps({'code': 'ERR030'})
    states = models.State.objects.filter(pk=city.state_id)
    assert await states.aexists(), dumps({'code': 'ERR036'})


@sb.type
class Mutation:
    @sb.mutation(description='ADMIN, STAFF')
    @permission_required('ADMIN', 'STAFF')
    async def create_city(
        root, info: Info,
        data: inputs.CreateCity
    ) -> outputs.City:
        city = models.City()
        utils.set_attributes(city, data, set_nulls=False)
        await check_city(city)
        await city.asave()
        return city

    @sb.mutation(description='ADMIN, STAFF')
    @permission_required('ADMIN', 'STAFF')
    async def update_city(
        root, info: Info,
        data: inputs.UpdateCity
    ) -> outputs.City:
        city = await manager.filter(pk=data.id).afirst()
        assert city, dumps({'code': 'ERR039'})
        utils.set_attributes(city, data, ignored=['id'], set_nulls=False)
        await check_city(city)
        await city.asave()
        return city

    @sb.mutation(description='ADMIN, STAFF. Deletes its districts too')
    @permission_required('ADMIN', 'STAFF')
    async def delete_city(
        root, info: Info,
        id: sb.ID
    ) -> outputs.SuccessResponse:
        try:
            deleted, _ = await manager.filter(pk=id).adelete()
        except ProtectedError:
            # Transfers use its districts
            raise Error(dumps({'code': 'ERR040'}))
        assert deleted, dumps({'code': 'ERR039'})
        return outputs.SuccessResponse(success=True)
