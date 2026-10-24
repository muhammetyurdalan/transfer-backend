import strawberry as sb
from datetime import datetime
from decimal import Decimal
from strawberry.file_uploads import Upload
from strawberry.scalars import JSON
from typing import List, Optional
from core import enums


def required(description=None):
    return sb.field(description=description)


def optional(description=None):
    return sb.field(description=description, default=sb.UNSET)


# User

@sb.input
class GetCode:
    email: str = required('100 chars max')
    language: enums.Language
    is_user: Optional[bool] = optional(
        'true: existing user (reset password), false: new user (sign up)'
    )


@sb.input
class ValidateCode:
    code: str = required('6 chars')
    secret: str


@sb.input
class CreateUser:
    secret: str = required('validated secret from validateCode')
    password: str = required('128 chars max')
    first_name: str = required('50 chars max')
    last_name: str = required('50 chars max')
    phone: Optional[str] = optional('20 chars max')
    profile_image: Optional[Upload] = optional()
    role: Optional[enums.SignUpRole] = optional('default CUSTOMER')


@sb.input
class SignIn:
    email: str = required('100 chars max')
    password: str = required('128 chars max')


@sb.input
class ResetPassword:
    secret: str = required('validated secret from validateCode')
    password: str = required('128 chars max')


@sb.input
class UpdateUser:
    language: Optional[enums.Language] = optional()
    first_name: Optional[str] = optional('50 chars max')
    last_name: Optional[str] = optional('50 chars max')
    phone: Optional[str] = optional('20 chars max')
    profile_image: Optional[Upload] = optional()


@sb.input
class UpdateAdminUser:
    id: sb.ID
    role: Optional[enums.Role] = optional()
    is_active: Optional[bool] = optional()


@sb.input
class UpdateFcmToken:
    registration_id: str = required('FCM registration token')
    type: enums.DeviceType


# Notification

@sb.input
class SendNotification:
    user_ids: List[sb.ID]
    title: str = required('100 chars max')
    body: str = required()
    action: Optional[enums.Action] = optional()
    data: Optional[JSON] = optional()


# Vehicle category

@sb.input
class CreateVehicleCategory:
    name_en: str = required('50 chars max')
    name_tr: Optional[str] = optional('50 chars max, falls back to name_en')
    description_en: Optional[str] = optional()
    description_tr: Optional[str] = optional()
    image: Optional[Upload] = optional()
    max_passengers: int = required('>= 1')
    max_luggage: int = required('>= 0')
    child_seat_capacity: Optional[int] = optional('default 0')
    is_wheelchair_accessible: Optional[bool] = optional('default false')
    base_fare: Optional[Decimal] = optional('8.2, default 0')
    per_km_price: Optional[Decimal] = optional('8.2, default 0')
    per_minute_price: Optional[Decimal] = optional('8.2, default 0')
    minimum_fare: Optional[Decimal] = optional('8.2, default 0')
    currency: Optional[enums.Currency] = optional('default TRY')
    order: Optional[int] = optional('list order, default 0')
    is_active: Optional[bool] = optional('default true')


@sb.input
class UpdateVehicleCategory:
    id: sb.ID
    name_en: Optional[str] = optional('50 chars max')
    name_tr: Optional[str] = optional('50 chars max')
    description_en: Optional[str] = optional()
    description_tr: Optional[str] = optional()
    image: Optional[Upload] = optional()
    max_passengers: Optional[int] = optional('>= 1')
    max_luggage: Optional[int] = optional('>= 0')
    child_seat_capacity: Optional[int] = optional()
    is_wheelchair_accessible: Optional[bool] = optional()
    base_fare: Optional[Decimal] = optional('8.2')
    per_km_price: Optional[Decimal] = optional('8.2')
    per_minute_price: Optional[Decimal] = optional('8.2')
    minimum_fare: Optional[Decimal] = optional('8.2')
    currency: Optional[enums.Currency] = optional()
    order: Optional[int] = optional()
    is_active: Optional[bool] = optional()


# Vehicle

@sb.input
class CreateVehicle:
    driver_id: sb.ID = required('active user with DRIVER role')
    category_id: sb.ID
    plate: str = required('20 chars max, unique')
    brand: str = required('50 chars max')
    model: str = required('50 chars max')
    year: Optional[int] = optional()
    color: Optional[str] = optional('30 chars max')
    image: Optional[Upload] = optional()
    is_active: Optional[bool] = optional('default true')


@sb.input
class UpdateVehicle:
    id: sb.ID
    driver_id: Optional[sb.ID] = optional('active user with DRIVER role')
    category_id: Optional[sb.ID] = optional()
    plate: Optional[str] = optional('20 chars max, unique')
    brand: Optional[str] = optional('50 chars max')
    model: Optional[str] = optional('50 chars max')
    year: Optional[int] = optional()
    color: Optional[str] = optional('30 chars max')
    image: Optional[Upload] = optional()
    is_active: Optional[bool] = optional()


# Transfer

@sb.input
class CreateTransfer:
    pickup_address: str = required('255 chars max')
    pickup_latitude: Decimal = required('3.6')
    pickup_longitude: Decimal = required('3.6')
    dropoff_address: str = required('255 chars max')
    dropoff_latitude: Decimal = required('3.6')
    dropoff_longitude: Decimal = required('3.6')
    pickup_time: datetime = required('future, timezone aware')
    distance: Decimal = required('route distance in km, > 0, 6.2')
    duration: int = required('route duration in minutes, > 0')
    passenger_count: Optional[int] = optional('default 1')
    luggage_count: Optional[int] = optional('default 0')
    child_seat_count: Optional[int] = optional('default 0')
    needs_wheelchair: Optional[bool] = optional('default false')
    passenger_name: str = required('100 chars max')
    passenger_phone: Optional[str] = optional('20 chars max')
    flight_number: Optional[str] = optional('10 chars max')
    note: Optional[str] = optional()


@sb.input
class UpdateTransfer:
    id: sb.ID
    pickup_address: Optional[str] = optional('255 chars max')
    pickup_latitude: Optional[Decimal] = optional('3.6')
    pickup_longitude: Optional[Decimal] = optional('3.6')
    dropoff_address: Optional[str] = optional('255 chars max')
    dropoff_latitude: Optional[Decimal] = optional('3.6')
    dropoff_longitude: Optional[Decimal] = optional('3.6')
    pickup_time: Optional[datetime] = optional('future, timezone aware')
    distance: Optional[Decimal] = optional('route distance in km, > 0')
    duration: Optional[int] = optional('route duration in minutes, > 0')
    passenger_count: Optional[int] = optional()
    luggage_count: Optional[int] = optional()
    child_seat_count: Optional[int] = optional()
    needs_wheelchair: Optional[bool] = optional()
    passenger_name: Optional[str] = optional('100 chars max')
    passenger_phone: Optional[str] = optional('20 chars max')
    flight_number: Optional[str] = optional('10 chars max')
    note: Optional[str] = optional()


@sb.input
class PayTransfer:
    id: sb.ID
    vehicle_category_id: sb.ID = required('one of vehicleOptions')


@sb.input
class AssignDriver:
    id: sb.ID
    driver_id: sb.ID = required('one of availableDrivers')
    vehicle_id: Optional[sb.ID] = optional(
        "driver's suitable vehicle, default: the first one"
    )


@sb.input
class UpdateTransferStatus:
    id: sb.ID
    status: enums.TransferStatus = required('ASSIGNED -> STARTED -> COMPLETED')


@sb.input
class TipDriver:
    id: sb.ID
    amount: Decimal = required('8.2, > 0')


# Transaction

@sb.input
class RefundTransaction:
    id: sb.ID = required('PAYMENT or TIP transaction')
    amount: Optional[Decimal] = optional('8.2, default: all refundable')
    note: Optional[str] = optional()


# Message

@sb.input
class SendMessage:
    transfer_id: sb.ID
    message: Optional[str] = optional()
    location: Optional[str] = optional()
    file: Optional[Upload] = optional('2MB max')
    parent_id: Optional[sb.ID] = optional()


@sb.input
class UpdateMessage:
    id: sb.ID = required()
    message: str = required()
