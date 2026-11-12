from django.db.models import TextChoices
from strawberry import enum


@enum
class Role(TextChoices):
    ADMIN = 'ADMIN'
    STAFF = 'STAFF'
    OPERATOR = 'OPERATOR'
    DRIVER = 'DRIVER'
    CUSTOMER = 'CUSTOMER'


@enum
class VerifyStatus(TextChoices):
    PENDING = 'PENDING'
    ACCEPTED = 'ACCEPTED'
    REJECTED = 'REJECTED'


@enum
class InvitationStatus(TextChoices):
    PENDING = 'PENDING'
    ACCEPTED = 'ACCEPTED'


@enum
class SignUpRole(TextChoices):
    CUSTOMER = 'CUSTOMER'
    DRIVER = 'DRIVER'


@enum
class Action(TextChoices):
    USER_UPDATED = 'USER_UPDATED'
    USER_DEACTIVATED = 'USER_DEACTIVATED'
    NOTIFICATION = 'NOTIFICATION'
    MESSAGE = 'MESSAGE'
    UPDATE_MESSAGE = 'UPDATE_MESSAGE'
    DELETE_MESSAGE = 'DELETE_MESSAGE'
    PAYMENT_SUCCESS = 'PAYMENT_SUCCESS'
    PAYMENT_FAILED = 'PAYMENT_FAILED'
    TIP_PAYMENT_SUCCESS = 'TIP_PAYMENT_SUCCESS'
    TIP_PAYMENT_FAILED = 'TIP_PAYMENT_FAILED'
    REFUND = 'REFUND'
    TRANSFER_ACCEPTED = 'TRANSFER_ACCEPTED'
    TRANSFER_ASSIGNED = 'TRANSFER_ASSIGNED'
    TRANSFER_STATUS_UPDATED = 'TRANSFER_STATUS_UPDATED'
    TRANSFER_CANCELLED = 'TRANSFER_CANCELLED'
    TRANSFER_WITHDRAWN = 'TRANSFER_WITHDRAWN'


@enum
class TransferStatus(TextChoices):
    PENDING = 'PENDING'
    ACCEPTED = 'ACCEPTED'
    STARTED = 'STARTED'
    COMPLETED = 'COMPLETED'
    CANCELLED = 'CANCELLED'
    WITHDRAWN = 'WITHDRAWN'
    EXPIRED = 'EXPIRED'


@enum
class TransactionStatus(TextChoices):
    PENDING = 'PENDING'
    FAILED = 'FAILED'
    COMPLETED = 'COMPLETED'


@enum
class TransactionCategory(TextChoices):
    PAYMENT = 'PAYMENT'
    TIP = 'TIP'
    REFUND = 'REFUND'


@enum
class Language(TextChoices):
    EN = 'en'
    TR = 'tr'
    DE = 'de'
    RU = 'ru'


@enum
class Currency(TextChoices):
    USD = 'USD'
    EUR = 'EUR'
    GBP = 'GBP'
    TRY = 'TRY'


@enum
class DeviceType(TextChoices):
    IOS = 'ios'
    ANDROID = 'android'
    WEB = 'web'
