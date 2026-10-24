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


User = utils.User
manager = models.Vehicle.objects


def get_vehicles(user):
    if user.role in utils.DISPATCH_ROLES:
        return manager.all()
    return manager.filter(driver=user)


@sb.type
class Query:
    @sbd.field(description='ADMIN, STAFF, OPERATOR, DRIVER')
    @permission_required('ADMIN', 'STAFF', 'OPERATOR', 'DRIVER')
    async def vehicle(
        root, info: Info,
        id: sb.ID
    ) -> Optional[outputs.Vehicle]:
        user = info.context['user']
        return await get_vehicles(user).filter(pk=id).afirst()

    @sbd.field(description='ADMIN, STAFF, OPERATOR, DRIVER')
    @permission_required('ADMIN', 'STAFF', 'OPERATOR', 'DRIVER')
    async def vehicles(
        root, info: Info,
        filters: Optional[outputs.VehicleFilter] = sb.UNSET,
        ordering: Optional[List[outputs.VehicleOrder]] = sb.UNSET,
        pagination: Optional[OffsetPaginationInput] = sb.UNSET
    ) -> outputs.VehiclesPaginated:
        user = info.context['user']
        vehicles = get_vehicles(user)
        if filters:
            vehicles = sbd.filters.apply(filters, vehicles, info)
        count = await utils.get_count(info, vehicles)
        if ordering:
            args = (outputs.VehicleOrder, ordering, info, vehicles)
            vehicles = sbd.ordering.apply_ordering(*args)
        if pagination:
            vehicles = sbd.pagination.apply(pagination, vehicles)
        return outputs.VehiclesPaginated(vehicles=vehicles, count=count)


async def check_vehicle(data, id=None):
    if data.driver_id is not sb.UNSET:
        drivers = User.objects.drivers().filter(pk=data.driver_id)
        assert await drivers.aexists(), dumps({'code': 'ERR015'})
    if data.category_id is not sb.UNSET:
        categories = models.VehicleCategory.objects.filter(pk=data.category_id)
        assert await categories.aexists(), dumps({'code': 'ERR025'})
    if data.plate:
        plates = manager.filter(plate__iexact=data.plate.strip()).exclude(pk=id)
        assert not await plates.aexists(), dumps({'code': 'ERR028'})


def normalize_plate(vehicle):
    vehicle.plate = vehicle.plate.strip().upper()


@sb.type
class Mutation:
    @sb.mutation(description='ADMIN, STAFF, OPERATOR')
    @permission_required('ADMIN', 'STAFF', 'OPERATOR')
    async def create_vehicle(
        root, info: Info,
        data: inputs.CreateVehicle
    ) -> outputs.Vehicle:
        await check_vehicle(data)
        vehicle = models.Vehicle()
        utils.set_attributes(vehicle, data, set_nulls=False)
        normalize_plate(vehicle)
        await vehicle.asave()
        return vehicle

    @sb.mutation(description='ADMIN, STAFF, OPERATOR')
    @permission_required('ADMIN', 'STAFF', 'OPERATOR')
    async def update_vehicle(
        root, info: Info,
        data: inputs.UpdateVehicle
    ) -> outputs.Vehicle:
        vehicle = await manager.filter(pk=data.id).afirst()
        assert vehicle, dumps({'code': 'ERR027'})
        await check_vehicle(data, vehicle.id)
        ignored = ['id']
        utils.set_attributes(vehicle, data, ignored=ignored, set_nulls=False)
        normalize_plate(vehicle)
        await vehicle.asave()
        return vehicle

    @sb.mutation(description='ADMIN, STAFF, OPERATOR')
    @permission_required('ADMIN', 'STAFF', 'OPERATOR')
    async def delete_vehicle(
        root, info: Info,
        id: sb.ID
    ) -> outputs.SuccessResponse:
        try:
            await manager.filter(pk=id).adelete()
        except ProtectedError:
            # Used in transfers: deactivate instead
            raise Error(dumps({'code': 'ERR029'}))
        return outputs.SuccessResponse(success=True)
