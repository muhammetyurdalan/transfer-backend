import strawberry as sb
import strawberry_django as sbd
from datetime import datetime
from decimal import Decimal
from django.db.models.fields.files import FieldFile
from strawberry import Info
from strawberry.permission import BasePermission, PermissionExtension
from strawberry_django.fields.types import field_type_map
from typing import Any, List, Optional
from core import enums, models
from core.decorators import get_user


for key, value in field_type_map.items():
    if 'AutoField' not in key.__name__:
        field_type_map[key] = Optional[value]


# Roles that see every user's contact details
AUTHORIZED_ROLES = [enums.Role.ADMIN, enums.Role.STAFF, enums.Role.OPERATOR]


class IsAuthorized(BasePermission):
    message = 'User is not authorized to see this field.'

    def has_permission(self, source: Any, info: Info, **kwargs) -> bool:
        user = get_user(info.context)
        if not user:
            return False
        return user.role in AUTHORIZED_ROLES or user.id == source.id


def private(permission=IsAuthorized, **kwargs):
    extension = PermissionExtension(
        permissions=[permission()], fail_silently=True
    )
    return sbd.field(extensions=[extension], **kwargs)


@sb.type
class SuccessResponse:
    success: bool


@sb.type
class SubscriptionResponse:
    action: enums.Action
    sender_id: Optional[sb.ID] = None
    message: Optional[str] = None
    notification_id: Optional[sb.ID] = None
    transfer_id: Optional[sb.ID] = None
    message_id: Optional[sb.ID] = None
    parent_id: Optional[sb.ID] = None
    location: Optional[str] = None
    file: Optional[str] = None


@sb.type
class CodeResponse:
    secret: str
    expire_seconds: int


# User

@sbd.filter_type(models.User)
class UserFilter:
    id: Optional[sbd.BaseFilterLookup[sb.ID]] = sb.UNSET
    role: Optional[sbd.BaseFilterLookup[enums.Role]] = sb.UNSET
    email: Optional[sbd.StrFilterLookup] = sb.UNSET
    first_name: Optional[sbd.StrFilterLookup] = sb.UNSET
    last_name: Optional[sbd.StrFilterLookup] = sb.UNSET
    phone: Optional[sbd.StrFilterLookup] = sb.UNSET
    is_active: Optional[bool] = sb.UNSET
    created_at: Optional[sbd.DatetimeFilterLookup] = sb.UNSET


@sbd.order_type(models.User)
class UserOrder:
    id: sb.auto
    email: sb.auto
    first_name: sb.auto
    last_name: sb.auto
    created_at: sb.auto


@sbd.type(models.User, exclude=['password', 'last_login', 'psp_data'])
class User:
    role: Optional[enums.Role]
    language: Optional[enums.Language]
    profile_image: Optional[str]
    name: Optional[str]
    email: Optional[str] = private()
    phone: Optional[str] = private()


@sb.type
class UsersPaginated:
    users: Optional[List[User]]
    count: Optional[int]


@sb.type
class SignInResponse:
    user: User
    token: str


# Notification

@sbd.filter_type(models.Notification)
class NotificationFilter:
    id: Optional[sbd.BaseFilterLookup[sb.ID]] = sb.UNSET
    action: Optional[sbd.BaseFilterLookup[enums.Action]] = sb.UNSET
    is_seen: Optional[bool] = sb.UNSET
    created_at: Optional[sbd.DatetimeFilterLookup] = sb.UNSET


@sbd.order_type(models.Notification)
class NotificationOrder:
    id: sb.auto
    created_at: sb.auto


@sbd.type(models.Notification, exclude=['user'])
class Notification:
    user_id: sb.ID
    action: Optional[enums.Action]


@sb.type
class NotificationsPaginated:
    notifications: Optional[List[Notification]]
    count: Optional[int]


# Vehicle category

@sbd.filter_type(models.VehicleCategory)
class VehicleCategoryFilter:
    id: Optional[sbd.BaseFilterLookup[sb.ID]] = sb.UNSET
    name: Optional[sbd.StrFilterLookup] = sb.UNSET
    currency: Optional[sbd.BaseFilterLookup[enums.Currency]] = sb.UNSET
    max_passengers: Optional[sbd.ComparisonFilterLookup[int]] = sb.UNSET
    max_luggage: Optional[sbd.ComparisonFilterLookup[int]] = sb.UNSET
    is_wheelchair_accessible: Optional[bool] = sb.UNSET
    is_active: Optional[bool] = sb.UNSET


@sbd.order_type(models.VehicleCategory)
class VehicleCategoryOrder:
    id: sb.auto
    order: sb.auto
    max_passengers: sb.auto
    created_at: sb.auto


@sbd.type(
    models.VehicleCategory,
    exclude=['vehicles', 'transfers']
)
class VehicleCategory:
    # Active language (user's language or the language argument). The
    # translation descriptor reads the language columns, so load them too.
    name: Optional[str] = sbd.field(only=['name', 'name_en', 'name_tr'])
    description: Optional[str] = sbd.field(
        only=['description', 'description_en', 'description_tr']
    )
    name_en: Optional[str]
    name_tr: Optional[str]
    description_en: Optional[str]
    description_tr: Optional[str]
    image: Optional[str]
    currency: Optional[enums.Currency]


@sb.type
class VehicleCategoriesPaginated:
    vehicle_categories: Optional[List[VehicleCategory]]
    count: Optional[int]


# Location

@sbd.type(models.State, exclude=['cities', 'transfers'])
class State:
    pass


@sbd.type(models.City, exclude=['districts'])
class City:
    state_id: sb.ID
    state: Optional[State]


@sbd.type(
    models.District,
    exclude=['pickup_transfers', 'dropoff_transfers']
)
class District:
    city_id: sb.ID
    city: Optional[City]


# Vehicle

@sbd.filter_type(models.Vehicle)
class VehicleFilter:
    id: Optional[sbd.BaseFilterLookup[sb.ID]] = sb.UNSET
    driver_id: Optional[sbd.BaseFilterLookup[sb.ID]] = sb.UNSET
    category_id: Optional[sbd.BaseFilterLookup[sb.ID]] = sb.UNSET
    plate: Optional[sbd.StrFilterLookup] = sb.UNSET
    brand: Optional[sbd.StrFilterLookup] = sb.UNSET
    is_active: Optional[bool] = sb.UNSET


@sbd.order_type(models.Vehicle)
class VehicleOrder:
    id: sb.auto
    plate: sb.auto
    created_at: sb.auto


@sbd.type(models.Vehicle, exclude=['transfers'])
class Vehicle:
    driver_id: sb.ID
    category_id: sb.ID
    driver: Optional[User]
    category: Optional[VehicleCategory]
    image: Optional[str]


@sb.type
class VehiclesPaginated:
    vehicles: Optional[List[Vehicle]]
    count: Optional[int]


# Transfer

@sbd.filter_type(models.Transfer)
class TransferFilter:
    id: Optional[sbd.BaseFilterLookup[sb.ID]] = sb.UNSET
    status: Optional[sbd.BaseFilterLookup[enums.TransferStatus]] = sb.UNSET
    customer_id: Optional[sbd.BaseFilterLookup[sb.ID]] = sb.UNSET
    operator_id: Optional[sbd.BaseFilterLookup[sb.ID]] = sb.UNSET
    driver_id: Optional[sbd.BaseFilterLookup[sb.ID]] = sb.UNSET
    vehicle_id: Optional[sbd.BaseFilterLookup[sb.ID]] = sb.UNSET
    vehicle_category_id: Optional[sbd.BaseFilterLookup[sb.ID]] = sb.UNSET
    state_id: Optional[sbd.BaseFilterLookup[sb.ID]] = sb.UNSET
    pickup_district_id: Optional[sbd.BaseFilterLookup[sb.ID]] = sb.UNSET
    dropoff_district_id: Optional[sbd.BaseFilterLookup[sb.ID]] = sb.UNSET
    pickup_time: Optional[sbd.DatetimeFilterLookup] = sb.UNSET
    passenger_name: Optional[sbd.StrFilterLookup] = sb.UNSET
    flight_number: Optional[sbd.StrFilterLookup] = sb.UNSET
    created_at: Optional[sbd.DatetimeFilterLookup] = sb.UNSET


@sbd.order_type(models.Transfer)
class TransferOrder:
    id: sb.auto
    pickup_time: sb.auto
    created_at: sb.auto


@sbd.type(models.Leg, exclude=['transfer'])
class Leg:
    transfer_id: sb.ID


@sbd.type(
    models.Transfer,
    exclude=['messages', 'through_messages', 'transactions', 'psp_data']
)
class Transfer:
    customer_id: sb.ID
    operator_id: Optional[sb.ID]
    driver_id: Optional[sb.ID]
    vehicle_id: Optional[sb.ID]
    vehicle_category_id: Optional[sb.ID]
    state_id: Optional[sb.ID]
    pickup_district_id: Optional[sb.ID]
    dropoff_district_id: Optional[sb.ID]
    customer: Optional[User]
    operator: Optional[User]
    driver: Optional[User]
    vehicle: Optional[Vehicle]
    vehicle_category: Optional[VehicleCategory]
    state: Optional[State]
    pickup_district: Optional[District]
    dropoff_district: Optional[District]
    legs: Optional[List[Leg]]
    status: Optional[enums.TransferStatus]
    currency: Optional[enums.Currency]


@sb.type
class TransfersPaginated:
    transfers: Optional[List[Transfer]]
    count: Optional[int]


@sb.type
class VehicleOption:
    vehicle_category: VehicleCategory
    distance: Decimal
    duration: int
    price: Decimal
    currency: enums.Currency


@sb.type
class TransferResponse:
    transfer: Transfer
    vehicle_options: List[VehicleOption]


@sb.type
class PaymentResponse:
    payment_link: Optional[str] = None
    link_expire_time: Optional[datetime] = None


# Transaction

@sbd.filter_type(models.Transaction)
class TransactionFilter:
    id: Optional[sbd.BaseFilterLookup[sb.ID]] = sb.UNSET
    transfer_id: Optional[sbd.BaseFilterLookup[sb.ID]] = sb.UNSET
    user_id: Optional[sbd.BaseFilterLookup[sb.ID]] = sb.UNSET
    parent_id: Optional[sbd.BaseFilterLookup[sb.ID]] = sb.UNSET
    category: Optional[
        sbd.BaseFilterLookup[enums.TransactionCategory]
    ] = sb.UNSET
    order_time: Optional[sbd.DatetimeFilterLookup] = sb.UNSET


@sbd.order_type(models.Transaction)
class TransactionOrder:
    id: sb.auto
    order_time: sb.auto
    order_amount: sb.auto


@sbd.type(models.Transaction, exclude=['refunds'])
class Transaction:
    transfer_id: sb.ID
    user_id: sb.ID
    parent_id: Optional[sb.ID]
    transfer: Optional[Transfer]
    user: Optional[User]
    category: Optional[enums.TransactionCategory]
    currency: Optional[enums.Currency]


@sb.type
class TransactionsPaginated:
    transactions: Optional[List[Transaction]]
    count: Optional[int]


# Message

@sbd.order_type(models.Message)
class MessageOrder:
    id: sb.auto
    time: sb.auto


def mask_deleted(root, info: Info) -> Optional[str]:
    value = getattr(root, info.python_name)
    if root.is_deleted:
        return ''
    if isinstance(value, FieldFile):
        return value.url if value else ''
    return value


@sbd.type(
    models.Message,
    exclude=['transfer', 'sender', 'receivers', 'deleted_by', 'parent',
             'replies', 'through_messages']
)
class ParentMessage:
    sender_id: sb.ID
    is_deleted: Optional[bool]
    message: Optional[str] = sbd.field(
        resolver=mask_deleted, only=['message', 'is_deleted']
    )
    location: Optional[str] = sbd.field(
        resolver=mask_deleted, only=['location', 'is_deleted']
    )
    file: Optional[str] = sbd.field(
        resolver=mask_deleted, only=['file', 'is_deleted']
    )


@sbd.type(
    models.Message,
    exclude=['transfer', 'deleted_by', 'replies', 'through_messages']
)
class Message:
    transfer_id: sb.ID
    sender_id: sb.ID
    parent_id: Optional[sb.ID]
    sender: Optional[User]
    receivers: Optional[List[User]]
    parent: Optional[ParentMessage] = sbd.field(select_related=['parent'])
    is_deleted: Optional[bool]
    message: Optional[str] = sbd.field(
        resolver=mask_deleted, only=['message', 'is_deleted']
    )
    location: Optional[str] = sbd.field(
        resolver=mask_deleted, only=['location', 'is_deleted']
    )
    file: Optional[str] = sbd.field(
        resolver=mask_deleted, only=['file', 'is_deleted']
    )
