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
    phone: str = required('20 chars max, not empty (ERR043)')
    profile_image: Optional[Upload] = optional()


@sb.input
class InviteCustomer:
    email: str = required('100 chars max')
    language: Optional[enums.Language] = optional(
        "invitation email and account language, default: the inviter's"
    )


@sb.input
class CreateAdminUser:
    email: str = required('100 chars max, unique')
    password: str = required('128 chars max')
    role: enums.Role = required('only ADMIN can create ADMIN')
    first_name: str = required('50 chars max')
    last_name: str = required('50 chars max')
    phone: str = required('20 chars max, not empty (ERR043)')
    language: Optional[enums.Language] = optional('default EN')
    profile_image: Optional[Upload] = optional()
    district_ids: Optional[List[sb.ID]] = optional('required for OPERATOR')


@sb.input
class SocialSignIn:
    provider: enums.SocialProvider
    id_token: str = required('OpenID Connect id_token from the provider')
    nonce: str = required('nonce sent to the provider, must match the token')
    first_name: Optional[str] = optional(
        '50 chars max, Apple sends the name only on first sign-in'
    )
    last_name: Optional[str] = optional('50 chars max')


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
    phone: Optional[str] = optional('20 chars max, cannot be cleared (ERR043)')
    profile_image: Optional[Upload] = optional()


@sb.input
class UpdateAdminUser:
    id: sb.ID
    role: Optional[enums.Role] = optional('ADMIN')
    is_active: Optional[bool] = optional('ADMIN')
    status: Optional[enums.VerifyStatus] = optional('ADMIN, STAFF')


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


# State

@sb.input
class CreateState:
    name: str = required('50 chars max')
    code: str = required('4 chars max, unique')
    psp: str = required('20 chars max, payment provider of the state')
    customer_imposed_hours: Optional[List[int]] = optional(
        'cancel penalty thresholds in hours before start, ascending'
    )
    customer_imposed_rates: Optional[List[Decimal]] = optional(
        'penalty rate (0-1) per hour threshold, same length as hours'
    )
    driver_buffer_minutes: Optional[int] = optional('default 30')
    allow_multiple_vehicles: Optional[bool] = optional('default false')
    allow_unconditional_refund: Optional[bool] = optional(
        'default false, needs cancelPriceRate > 0'
    )
    cancel_price_rate: Optional[Decimal] = optional(
        '0-1, share of the price paid for the cancellation protection'
    )


@sb.input
class UpdateState:
    id: sb.ID
    name: Optional[str] = optional('50 chars max')
    code: Optional[str] = optional('4 chars max, unique')
    psp: Optional[str] = optional('20 chars max')
    customer_imposed_hours: Optional[List[int]] = optional(
        'replaces the list, send with customerImposedRates'
    )
    customer_imposed_rates: Optional[List[Decimal]] = optional(
        'replaces the list, send with customerImposedHours'
    )
    driver_buffer_minutes: Optional[int] = optional()
    allow_multiple_vehicles: Optional[bool] = optional()
    allow_unconditional_refund: Optional[bool] = optional(
        'needs cancelPriceRate > 0'
    )
    cancel_price_rate: Optional[Decimal] = optional('0-1')


@sb.input
class CreateCity:
    state_id: sb.ID
    name: str = required('50 chars max')


@sb.input
class UpdateCity:
    id: sb.ID
    state_id: Optional[sb.ID] = optional()
    name: Optional[str] = optional('50 chars max')


@sb.input
class CreateDistrict:
    city_id: sb.ID
    name: str = required('50 chars max')
    is_active: Optional[bool] = optional('default true')


@sb.input
class UpdateDistrict:
    id: sb.ID
    city_id: Optional[sb.ID] = optional()
    name: Optional[str] = optional('50 chars max')
    is_active: Optional[bool] = optional()


# Vehicle category

@sb.input
class CreateVehicleCategory:
    name_en: str = required('50 chars max')
    name_tr: Optional[str] = optional('50 chars max, falls back to name_en')
    name_de: Optional[str] = optional('50 chars max, falls back to name_en')
    name_ru: Optional[str] = optional('50 chars max, falls back to name_en')
    description_en: Optional[str] = optional()
    description_tr: Optional[str] = optional()
    description_de: Optional[str] = optional()
    description_ru: Optional[str] = optional()
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
    name_de: Optional[str] = optional('50 chars max')
    name_ru: Optional[str] = optional('50 chars max')
    description_en: Optional[str] = optional()
    description_tr: Optional[str] = optional()
    description_de: Optional[str] = optional()
    description_ru: Optional[str] = optional()
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
    driver_id: Optional[sb.ID] = optional(
        'active user with DRIVER role, required except for DRIVER (always self)'
    )
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
class CreateLeg:
    start_latitude: Decimal = required('4.6')
    start_longitude: Decimal = required('4.6')
    start_address: str = required()
    start_time: datetime = required('timezone aware')
    end_latitude: Decimal = required('4.6')
    end_longitude: Decimal = required('4.6')
    end_address: str = required()
    end_time: datetime = required('after start_time')
    order: Optional[int] = optional('default: position in the list')
    route: Optional[str] = optional()


@sb.input
class CreateTransfer:
    pickup_address: str = required('255 chars max')
    pickup_latitude: Decimal = required('3.6')
    pickup_longitude: Decimal = required('3.6')
    dropoff_address: str = required('255 chars max')
    dropoff_latitude: Decimal = required('3.6')
    dropoff_longitude: Decimal = required('3.6')
    pickup_district_name: str = required('at least one must be found')
    dropoff_district_name: str = required('at least one must be found')
    pickup_time: datetime = required('future, timezone aware')
    distance: Decimal = required('route distance in km, > 0, 6.2')
    duration: int = required('route duration in minutes, > 0')
    passenger_count: Optional[int] = optional('default 1')
    luggage_count: Optional[int] = optional('default 0')
    child_seat_count: Optional[int] = optional('default 0')
    needs_wheelchair: Optional[bool] = optional('default false')
    passenger_name: Optional[str] = optional(
        "100 chars max, default the customer's name"
    )
    passenger_phone: Optional[str] = optional(
        "20 chars max, default the customer's phone"
    )
    flight_number: Optional[str] = optional('10 chars max')
    note: Optional[str] = optional()
    legs: Optional[List[CreateLeg]] = optional()
    customer_id: Optional[sb.ID] = optional('for operators')
    vehicle_category_id: Optional[sb.ID] = optional(
        'chosen vehicle (one of vehicleOptions, ERR025), saved with its '
        'price without a payment link; payTransfer can change it'
    )
    vehicle_count: Optional[int] = optional(
        'with vehicleCategoryId, see PayTransfer'
    )
    refund_option: Optional[enums.RefundOption] = optional(
        'with vehicleCategoryId, see PayTransfer'
    )


@sb.input
class QuoteTransfer:
    """Only what decides the vehicle options and prices (see CreateTransfer)."""
    pickup_district_name: str = required('at least one must be found')
    dropoff_district_name: str = required('at least one must be found')
    distance: Decimal = required('route distance in km, > 0, 6.2')
    duration: int = required('route duration in minutes, > 0')
    pickup_time: Optional[datetime] = optional('future if given')
    passenger_count: Optional[int] = optional('default 1')
    luggage_count: Optional[int] = optional('default 0')
    child_seat_count: Optional[int] = optional('default 0')
    needs_wheelchair: Optional[bool] = optional('default false')


@sb.input   
class CheckDistricts:
    pickup_district_name: str = required('at least one must be found')
    dropoff_district_name: str = required('at least one must be found')


@sb.input
class UpdateTransfer:
    id: sb.ID
    pickup_address: Optional[str] = optional('255 chars max')
    pickup_latitude: Optional[Decimal] = optional('3.6')
    pickup_longitude: Optional[Decimal] = optional('3.6')
    dropoff_address: Optional[str] = optional('255 chars max')
    dropoff_latitude: Optional[Decimal] = optional('3.6')
    dropoff_longitude: Optional[Decimal] = optional('3.6')
    pickup_district_name: Optional[str] = optional(
        'with a new pickup address'
    )
    dropoff_district_name: Optional[str] = optional(
        'with a new dropoff address'
    )
    pickup_time: Optional[datetime] = optional('future, timezone aware')
    distance: Optional[Decimal] = optional('route distance in km, > 0')
    duration: Optional[int] = optional('route duration in minutes, > 0')
    passenger_count: Optional[int] = optional()
    luggage_count: Optional[int] = optional()
    child_seat_count: Optional[int] = optional()
    needs_wheelchair: Optional[bool] = optional()
    passenger_name: Optional[str] = optional('100 chars max, not blank (ERR058)')
    passenger_phone: Optional[str] = optional('20 chars max, not blank (ERR057)')
    flight_number: Optional[str] = optional('10 chars max')
    note: Optional[str] = optional()
    legs: Optional[List[CreateLeg]] = optional(
        'replaces all legs (stops), [] removes them; counts as a route change'
    )
    vehicle_category_id: Optional[sb.ID] = optional(
        'new vehicle choice (one of the vehicle options after this update, '
        'ERR025), saved with its price; expires the payment link'
    )
    vehicle_count: Optional[int] = optional(
        'with vehicleCategoryId, see PayTransfer'
    )
    refund_option: Optional[enums.RefundOption] = optional(
        'with vehicleCategoryId, see PayTransfer'
    )


@sb.input
class PayTransfer:
    id: sb.ID
    vehicle_category_id: sb.ID = required('one of vehicleOptions')
    vehicle_count: Optional[int] = optional(
        "At least the option's count, which is the default"
    )
    refund_option: Optional[enums.RefundOption] = optional(
        'default STANDARD. UNCONDITIONAL adds cancelPrice (state '
        'cancelPriceRate * customerPrice), only if the state allows it'
    )


@sb.input
class AssignDriver:
    id: sb.ID = required('transfer id')
    driver_id: Optional[sb.ID] = optional(
        'one of availableDrivers, empty for an external driver'
    )
    vehicle_id: Optional[sb.ID] = optional(
        "internal driver's suitable vehicle, default: the first one"
    )
    driver_name: Optional[str] = optional(
        '100 chars max, required for an external driver, '
        "an internal driver's name is taken from the user"
    )
    driver_phone: Optional[str] = optional('20 chars max, external driver')
    plate: Optional[str] = optional(
        "20 chars max, external driver, an internal driver's is the vehicle's"
    )
    vehicle_brand: Optional[str] = optional(
        "50 chars max, external driver, an internal driver's is the vehicle's"
    )
    vehicle_model: Optional[str] = optional(
        "50 chars max, external driver, an internal driver's is the vehicle's"
    )
    driver_price: Optional[Decimal] = optional('8.2, >= 0')
    driver_price_currency: Optional[enums.Currency] = optional()


@sb.input
class UpdateTransferDriver:
    id: sb.ID = required('Transfer.drivers id')
    driver_name: Optional[str] = optional(
        '100 chars max, external driver (ignored for an internal one)'
    )
    driver_phone: Optional[str] = optional(
        '20 chars max, external driver (ignored for an internal one)'
    )
    plate: Optional[str] = optional(
        '20 chars max, external driver (ignored for an internal one)'
    )
    vehicle_brand: Optional[str] = optional(
        '50 chars max, external driver (ignored for an internal one)'
    )
    vehicle_model: Optional[str] = optional(
        '50 chars max, external driver (ignored for an internal one)'
    )
    driver_price: Optional[Decimal] = optional('8.2, >= 0')
    driver_price_currency: Optional[enums.Currency] = optional()


@sb.input
class UpdateTransferStatus:
    id: sb.ID
    status: enums.TransferStatus = required(
        'ACCEPTED -> STARTED -> COMPLETED, ACCEPTED -> COMPLETED'
    )


@sb.input
class CancelTransfer:
    id: sb.ID
    cancellation_reason_id: Optional[sb.ID] = optional(
        'active Reason of the allowed category; or cancellationReasonText'
    )
    cancellation_reason_text: Optional[str] = optional(
        '"Other": 3-200 chars, only without cancellationReasonId'
    )


@sb.input
class TipDriver:
    id: sb.ID
    amount: Decimal = required('8.2, > 0')
    transfer_driver_id: Optional[sb.ID] = optional(
        'Transfer.drivers id of an internal driver, '
        'required if the transfer has more than one'
    )


# Reason

@sb.input
class CreateReason:
    code: str = required('unique snake_case, 50 chars max')
    category: enums.ReasonCategory
    description_en: str = required('200 chars max')
    description_tr: Optional[str] = optional('200 chars max, falls back to en')
    description_de: Optional[str] = optional('200 chars max, falls back to en')
    description_ru: Optional[str] = optional('200 chars max, falls back to en')
    order: Optional[int] = optional('list order, default 0')
    is_active: Optional[bool] = optional('default true')


@sb.input
class UpdateReason:
    id: sb.ID
    code: Optional[str] = optional('unique snake_case, 50 chars max')
    category: Optional[enums.ReasonCategory] = optional()
    description_en: Optional[str] = optional('200 chars max')
    description_tr: Optional[str] = optional('200 chars max')
    description_de: Optional[str] = optional('200 chars max')
    description_ru: Optional[str] = optional('200 chars max')
    order: Optional[int] = optional()
    is_active: Optional[bool] = optional()


# Transaction

@sb.input
class RefundTransaction:
    id: sb.ID = required('PAYMENT or TIP transaction')
    amount: Optional[Decimal] = optional('8.2, default: all refundable')
    note: Optional[str] = optional()


@sb.input
class UpdateTransaction:
    id: sb.ID
    note: Optional[str] = optional()
    is_payout_sent: Optional[bool] = optional()


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
