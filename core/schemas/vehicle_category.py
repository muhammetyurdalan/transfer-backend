from django.db.models import ProtectedError
from django.utils.translation import activate
from json import dumps
import strawberry as sb
import strawberry_django as sbd
from strawberry import Info
from strawberry.exceptions import StrawberryGraphQLError as Error
from strawberry_django.pagination import OffsetPaginationInput
from typing import List, Optional
from core import enums, inputs, models, outputs, utils
from core.decorators import get_optional_user, permission_required


manager = models.VehicleCategory.objects
PRICE_FIELDS = ['base_fare', 'per_km_price', 'per_minute_price', 'minimum_fare']
COUNT_FIELDS = ['max_luggage', 'child_seat_capacity']


async def get_categories(info, language):
    """Public read: inactive categories only for ADMIN/STAFF."""
    user = await get_optional_user(info)
    activate(language or (user.language if user else enums.Language.EN))
    if user and user.role in utils.STAFF_ROLES:
        return manager.all()
    return manager.filter(is_active=True)


@sb.type
class Query:
    @sbd.field
    async def vehicle_category(
        root, info: Info,
        id: sb.ID,
        language: Optional[enums.Language] = sb.UNSET
    ) -> Optional[outputs.VehicleCategory]:
        categories = await get_categories(info, language)
        return await categories.filter(pk=id).afirst()

    @sbd.field
    async def vehicle_categories(
        root, info: Info,
        language: Optional[enums.Language] = sb.UNSET,
        filters: Optional[outputs.VehicleCategoryFilter] = sb.UNSET,
        ordering: Optional[List[outputs.VehicleCategoryOrder]] = sb.UNSET,
        pagination: Optional[OffsetPaginationInput] = sb.UNSET
    ) -> outputs.VehicleCategoriesPaginated:
        categories = await get_categories(info, language)
        if filters:
            categories = sbd.filters.apply(filters, categories, info)
        count = await utils.get_count(info, categories)
        if ordering:
            args = (outputs.VehicleCategoryOrder, ordering, info, categories)
            categories = sbd.ordering.apply_ordering(*args)
        if pagination:
            categories = sbd.pagination.apply(pagination, categories)
        return outputs.VehicleCategoriesPaginated(
            vehicle_categories=categories,
            count=count
        )


def check_category(data):
    fields = {
        key: value for key, value in utils.get_fields(data).items()
        if value is not None
    }
    valid = fields.get('max_passengers', 1) >= 1
    valid = valid and all(fields.get(key, 0) >= 0 for key in COUNT_FIELDS)
    valid = valid and all(fields.get(key, 0) >= 0 for key in PRICE_FIELDS)
    valid = valid and fields.get('name_en', 'name') != ''
    assert valid, dumps({'code': 'ERR030'})


@sb.type
class Mutation:
    @sb.mutation(description='ADMIN, STAFF')
    @permission_required('ADMIN', 'STAFF')
    async def create_vehicle_category(
        root, info: Info,
        data: inputs.CreateVehicleCategory
    ) -> outputs.VehicleCategory:
        check_category(data)
        category = models.VehicleCategory()
        utils.set_attributes(category, data, set_nulls=False)
        await category.asave()
        return category

    @sb.mutation(description='ADMIN, STAFF')
    @permission_required('ADMIN', 'STAFF')
    async def update_vehicle_category(
        root, info: Info,
        data: inputs.UpdateVehicleCategory
    ) -> outputs.VehicleCategory:
        check_category(data)
        category = await manager.filter(pk=data.id).afirst()
        assert category, dumps({'code': 'ERR025'})
        ignored = ['id']
        utils.set_attributes(category, data, ignored=ignored, set_nulls=False)
        await category.asave()
        return category

    @sb.mutation(description='ADMIN, STAFF')
    @permission_required('ADMIN', 'STAFF')
    async def delete_vehicle_category(
        root, info: Info,
        id: sb.ID
    ) -> outputs.SuccessResponse:
        try:
            await manager.filter(pk=id).adelete()
        except ProtectedError:
            # Vehicles or transfers use it: deactivate instead
            raise Error(dumps({'code': 'ERR026'}))
        return outputs.SuccessResponse(success=True)
