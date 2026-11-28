"""
Booking without an account (guest_booking.md). A guest is a passwordless
CUSTOMER (User.is_guest) without a session: its booking link token
(utils.get_access_token) gives a short lived session limited to the transfer
(scope guest, the GUEST role of permission_required).
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone
from django.utils.translation import activate
from json import dumps
from secrets import randbelow
import strawberry as sb
from strawberry import Info
from strawberry.exceptions import StrawberryGraphQLError as Error
from typing import Optional
from core import enums, inputs, models, outputs, utils
from core.decorators import append_user, permission_required
from core.ratelimit import check_rate_limit
from core.schemas.transfer import (
    IGNORED_CREATE_FIELDS, get_transfer, get_vehicle_options,
    payment_enabled, prepare_new_transfer, save_new_transfer
)
from core.schemas.user import get_code_response, send_code


User = utils.User
Status = enums.TransferStatus


def check_contact(contact):
    """Names 1-50 chars, a valid email, a phone and accepted terms (ERR030)."""
    first_name = contact.first_name.strip()
    last_name = contact.last_name.strip()
    valid = 0 < len(first_name) <= 50 and 0 < len(last_name) <= 50
    assert valid and contact.accept_terms, dumps({'code': 'ERR030'})
    email = utils.normalize_email(contact.email.strip())
    try:
        validate_email(email)
    except ValidationError:
        raise Error(dumps({'code': 'ERR030'}))
    phone = utils.check_phone(contact.phone)
    return first_name, last_name, email, phone


async def get_guest_customer(first_name, last_name, email, phone, language):
    """
    The customer of a booking without an account and whether the booking is
    claimed (visible in the customer's panel):
    - new email, or a deleted account's: a guest is opened (claimed)
    - a guest's: the same guest, its name/phone kept (claimed)
    - a registered customer's: theirs, hidden until they claim it
    - a driver's, operator's or staff's: ERR062 (sign in)
    """
    user = await User.objects.filter(email=email).afirst()
    if user and user.is_active and not user.is_guest:
        assert user.role == enums.Role.CUSTOMER, dumps({'code': 'ERR062'})
        return user, False
    if user and user.is_active:
        return user, True
    if user:
        # A deleted account's email: reopened as a guest, old sessions end
        user.token_version += 1
        user.status = enums.VerifyStatus.PENDING
    else:
        user = User(email=email)
    user.role = enums.Role.CUSTOMER
    user.first_name = first_name
    user.last_name = last_name
    user.phone = phone
    user.language = language
    user.is_guest = True
    user.is_active = True
    user.set_unusable_password()
    await user.asave()
    return user, True


async def check_open_bookings(customer):
    """At most GUEST_OPEN_BOOKINGS unpaid guest bookings per email (ERR064)."""
    open_bookings = models.Transfer.objects.filter(
        customer=customer, status=Status.PENDING
    ).exclude(access_nonce='')
    count = await open_bookings.acount()
    assert count < settings.GUEST_OPEN_BOOKINGS, dumps({'code': 'ERR064'})


def start_guest_session(info, user, transfer):
    """The rest of the request resolves as the transfer's guest session."""
    user.guest_transfer_id = str(transfer.pk)
    append_user(info.context, user)
    return utils.create_guest_token(user, transfer)


async def get_booking(transfer_id, token):
    """The guest booking of a valid link (ERR063), with its customer."""
    transfer = None
    if str(transfer_id).isdigit():
        transfers = models.Transfer.objects.select_related('customer')
        transfer = await transfers.filter(pk=transfer_id).afirst()
    utils.check_access_token(transfer, token)
    assert transfer.customer.is_active, dumps({'code': 'ERR063'})
    return transfer


@sb.type
class Mutation:
    @sb.mutation(description='Public, rate limited per client IP (ERR056)')
    async def create_guest_transfer(
        root, info: Info,
        data: inputs.CreateTransfer,
        contact: inputs.GuestContact,
        language: Optional[enums.Language] = sb.UNSET
    ) -> outputs.GuestBookingResponse:
        """
        createTransfer without an account. The contact is the payer and the
        default passenger, never the account's (a registered email's booking
        is hidden from its owner until claimed). Returns the booking link
        token and a guest session. The token is not read.
        """
        limit = settings.GUEST_BOOKING_RATE_LIMIT
        window = settings.GUEST_BOOKING_RATE_WINDOW
        await check_rate_limit(info, 'guest_booking', limit, window)
        first_name, last_name, email, phone = check_contact(contact)
        language = enums.Language(language or enums.Language.EN).value
        payer_name = f'{first_name} {last_name}'[:100]
        transfer = models.Transfer(
            access_nonce=utils.new_access_nonce(),
            payer_name=payer_name,
            payer_email=email,
            payer_phone=phone,
            terms_accepted_at=timezone.now(),
        )
        ignored = [*IGNORED_CREATE_FIELDS, 'customer_id']
        utils.set_attributes(transfer, data, ignored=ignored, set_nulls=False)
        if not (transfer.passenger_name or '').strip():
            transfer.passenger_name = payer_name
        if not (transfer.passenger_phone or '').strip():
            transfer.passenger_phone = phone
        # The transfer is checked before an account is opened for it
        await prepare_new_transfer(transfer, data)
        args = (first_name, last_name, email, phone, language)
        customer, claimed = await get_guest_customer(*args)
        await check_open_bookings(customer)
        transfer.customer = customer
        transfer.is_claimed = claimed
        await save_new_transfer(transfer, data)
        session = start_guest_session(info, customer, transfer)
        activate(language)
        vehicle_options = await get_vehicle_options(transfer)
        return outputs.GuestBookingResponse(
            transfer=transfer,
            vehicle_options=vehicle_options,
            access_token=utils.get_access_token(transfer),
            session=session
        )

    @sb.mutation(description='Public, rate limited per client IP (ERR056)')
    async def guest_session(
        root, info: Info,
        transfer_id: sb.ID,
        token: str
    ) -> outputs.GuestSession:
        """A guest session of the booking link (ERR063 if invalid)."""
        limit = settings.GUEST_SESSION_RATE_LIMIT
        window = settings.GUEST_SESSION_RATE_WINDOW
        await check_rate_limit(info, 'guest_session', limit, window)
        transfer = await get_booking(transfer_id, token)
        session = start_guest_session(info, transfer.customer, transfer)
        return outputs.GuestSession(
            session=session, transfer_id=transfer.pk,
            customer_id=transfer.customer_id
        )

    @sb.mutation(description='GUEST, ADMIN, STAFF')
    @permission_required('GUEST', 'ADMIN', 'STAFF')
    async def renew_booking_link(
        root, info: Info,
        id: sb.ID
    ) -> outputs.GuestSession:
        """
        A new booking link token, the old link and its guest sessions stop
        working (ERR063). A guest gets a new session too.
        """
        user = info.context['user']
        transfer = await get_transfer(user, id, expand=['customer'])
        assert transfer.is_guest_booking, dumps({'code': 'ERR030'})
        transfer.access_nonce = utils.new_access_nonce()
        await transfer.asave(update_fields=['access_nonce', 'updated_at'])
        session = ''
        if utils.get_guest_transfer_id(user) is not None:
            session = start_guest_session(info, user, transfer)
        return outputs.GuestSession(
            session=session,
            transfer_id=transfer.pk,
            customer_id=transfer.customer_id,
            access_token=utils.get_access_token(transfer)
        )

    @sb.mutation(description='CUSTOMER')
    @permission_required('CUSTOMER')
    async def claim_transfer(
        root, info: Info,
        transfer_id: sb.ID,
        token: str
    ) -> outputs.Transfer:
        """
        A signed in customer adds a booking made with their email without
        signing in to their transfers (ERR063: invalid link or not theirs).
        """
        user = info.context['user']
        transfer = await get_booking(transfer_id, token)
        assert transfer.customer_id == user.id, dumps({'code': 'ERR063'})
        if not transfer.is_claimed:
            transfer.is_claimed = True
            await transfer.asave(update_fields=['is_claimed', 'updated_at'])
        activate(user.language)
        return transfer

    @sb.mutation(description='GUEST')
    @permission_required('GUEST')
    async def send_confirm_code(
        root, info: Info,
        transfer_id: sb.ID
    ) -> outputs.CodeResponse:
        """
        Emails a code to the booking's email for confirmTransfer (payment
        off, ERR061 otherwise), the email is not verified otherwise.
        """
        user = info.context['user']
        expand = ['state']
        transfer = await get_transfer(user, transfer_id, [Status.PENDING], expand)
        assert not payment_enabled(transfer), dumps({'code': 'ERR061'})
        limit = settings.GUEST_CODE_ATTEMPTS
        window = settings.CODE_EXPIRE_SECONDS
        scope = f'guest_send_code:{transfer.pk}'
        await check_rate_limit(info, scope, limit, window)
        code = f'{randbelow(1000000):06d}'
        email = transfer.payer_email or user.email
        send_code(code, email, user.language)
        payload = {
            'purpose': 'guest_confirm', 'tid': str(transfer.pk), 'code': code
        }
        return get_code_response(payload)
