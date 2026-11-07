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


manager = models.District.objects


async def get_districts(info):
    """Public read: inactive districts only for ADMIN/STAFF."""
    user = await get_optional_user(info)
    if user and user.role in utils.STAFF_ROLES:
        return manager.all()
    return manager.filter(is_active=True)


@sb.type
class Query:
    @sbd.field(description='Public, inactive ones only for ADMIN, STAFF')
    async def district(
        root, info: Info,
        id: sb.ID
    ) -> Optional[outputs.District]:
        districts = await get_districts(info)
        return await districts.filter(pk=id).afirst()

    @sbd.field(description='Public, inactive ones only for ADMIN, STAFF')
    async def districts(
        root, info: Info,
        filters: Optional[outputs.DistrictFilter] = sb.UNSET,
        ordering: Optional[List[outputs.DistrictOrder]] = sb.UNSET,
        pagination: Optional[OffsetPaginationInput] = sb.UNSET
    ) -> outputs.DistrictsPaginated:
        districts = await get_districts(info)
        if filters:
            districts = sbd.filters.apply(filters, districts, info)
        count = await utils.get_count(info, districts)
        if ordering:
            args = (outputs.DistrictOrder, ordering, info, districts)
            districts = sbd.ordering.apply_ordering(*args)
        if pagination:
            districts = sbd.pagination.apply(pagination, districts)
        return outputs.DistrictsPaginated(districts=districts, count=count)


async def check_district(district):
    assert district.name.strip(), dumps({'code': 'ERR030'})
    cities = models.City.objects.filter(pk=district.city_id)
    assert await cities.aexists(), dumps({'code': 'ERR039'})


@sb.type
class Mutation:
    @sb.mutation(description='ADMIN, STAFF')
    @permission_required('ADMIN', 'STAFF')
    async def create_district(
        root, info: Info,
        data: inputs.CreateDistrict
    ) -> outputs.District:
        district = models.District()
        utils.set_attributes(district, data, set_nulls=False)
        await check_district(district)
        await district.asave()
        return district

    @sb.mutation(description='ADMIN, STAFF')
    @permission_required('ADMIN', 'STAFF')
    async def update_district(
        root, info: Info,
        data: inputs.UpdateDistrict
    ) -> outputs.District:
        district = await manager.filter(pk=data.id).afirst()
        assert district, dumps({'code': 'ERR035'})
        utils.set_attributes(district, data, ignored=['id'], set_nulls=False)
        await check_district(district)
        await district.asave()
        return district

    @sb.mutation(description='ADMIN, STAFF. Deactivate it if transfers use it')
    @permission_required('ADMIN', 'STAFF')
    async def delete_district(
        root, info: Info,
        id: sb.ID
    ) -> outputs.SuccessResponse:
        try:
            deleted, _ = await manager.filter(pk=id).adelete()
        except ProtectedError:
            raise Error(dumps({'code': 'ERR041'}))
        assert deleted, dumps({'code': 'ERR035'})
        return outputs.SuccessResponse(success=True)
