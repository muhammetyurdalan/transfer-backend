from asgiref.sync import async_to_sync as a2s, sync_to_async as s2a
from datetime import timedelta
from decimal import Decimal
from django.conf import settings
from django.db import transaction as db_transaction
from django.db.models import Sum
from django.http import (
    HttpResponse, HttpResponseBadRequest,
    HttpResponseForbidden, HttpResponseNotAllowed
)
from django.utils import timezone
from django.utils.translation import gettext_noop
from httpx import AsyncClient, HTTPError
from json import dumps, loads
from logging import getLogger
from secrets import compare_digest
from strawberry.exceptions import StrawberryGraphQLError as Error
from core import enums, models, utils


logger = getLogger(__name__)
Status = enums.TransferStatus
Category = enums.TransactionCategory
TransactionStatus = enums.TransactionStatus
Action = enums.Action
ZERO = models.ZERO
PAYMENT_KEYS = (
    'payment_id', 'payment_link', 'payment_link_expire_time', 'order_amount'
)
TIP_KEYS = ('tip_id', 'tip_link', 'tip_link_expire_time', 'tip_amount')


class PSPError(Error):
    """ERR031 for the client, error_code keeps the payment service's error."""

    def __init__(self, error_code):
        super().__init__(dumps({'code': 'ERR031'}))
        self.error_code = error_code


def get_error_code(error):
    """Payment service error as a short code for Transaction.error_code."""
    if isinstance(error, dict):
        error = error.get('code') or error.get('message') or dumps(error)
    max_length = models.Transaction._meta.get_field('error_code').max_length
    return str(error or 'ERR031')[:max_length]


def get_headers():
    return {'X-Access-Token': settings.MS_ACCESS_TOKEN}


async def async_post(path, data):
    data = loads(dumps(data, default=str))
    url = settings.PSP_MS_URL + path
    try:
        async with AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=get_headers(), json=data)
        response = response.json()
    except (HTTPError, ValueError) as error:
        # Unreachable or invalid response: no payment service error to keep
        logger.error('PSP %s failed: %r', path, error)
        raise PSPError('ERR031')
    if response.get('success'):
        return response['data']
    logger.error('PSP %s failed: %s', path, response.get('error'))
    raise PSPError(get_error_code(response.get('error')))


def get_transaction(transfer, category, order_amount, **kwargs):
    return models.Transaction(
        transfer=transfer,
        user_id=transfer.customer_id,
        category=category,
        order_amount=order_amount,
        currency=transfer.currency,
        order_time=timezone.now(),
        **kwargs
    )


# Payment

async def get_payment_link(transfer):
    """
    Payment link for transfer.price (the chosen vehicle category's price).
    Any previous link is expired first, so only the latest choice is payable.
    """
    await expire_payment_link(transfer)
    transaction = get_transaction(transfer, Category.PAYMENT, transfer.price)
    data = {
        'psp': transfer.state.psp.lower(),
        'transfer': utils.get_dict(transfer),
        'customer': utils.get_dict(transfer.customer),
        'transaction': utils.get_dict(transaction),
    }
    response = await async_post('/createPaymentLink', data)
    transfer.psp_data['psp'] = response['psp']
    transfer.psp_data['payment_id'] = response['payment_id']
    transfer.psp_data['payment_link'] = response['payment_link']
    expire_time = response['link_expire_time']
    transfer.psp_data['payment_link_expire_time'] = expire_time
    transfer.psp_data['order_amount'] = str(transfer.price)


async def expire_payment_link(transfer, link_type='payment'):
    if not transfer.psp_data.get(f'{link_type}_link'):
        return
    data = {
        'psp': transfer.psp_data.get('psp'), 
        'psp_data': transfer.psp_data,
        'link_type': link_type
    }
    response = await async_post('/expirePaymentLink', data)
    if not response.get('success'):
        logger.warning('PSP expirePaymentLink failed: %s', response.get('error'))
        return
    for key in PAYMENT_KEYS:
        transfer.psp_data.pop(key, None)


async def process_payment_return(data):
    transfer_id = str(data.get('id', ''))
    if not transfer_id.isdigit():
        return
    transfers = models.Transfer.objects.filter(pk=transfer_id)
    transfer = await transfers.afirst()
    if not transfer:
        return
    if transfer.status != Status.PENDING:
        return
    action_kwargs = {'transfer_id': transfer_id}
    customer_ids = [transfer.customer_id]
    if not data.get('success'):
        args = (None, Action.PAYMENT_FAILED, customer_ids)
        await utils.broadcast(*args, **action_kwargs)
        return
    now = timezone.now()
    pending = transfers.filter(status=Status.PENDING)
    updated = await pending.aupdate(status=Status.ACCEPTED, updated_at=now)
    if not updated:
        logger.warning(
            'Payment return for transfer %s in status %s', transfer_id,
            transfer.status
        )
        return
    amount = Decimal(transfer.psp_data.get('order_amount', transfer.price))
    transaction = get_transaction(
        transfer, Category.PAYMENT, amount,
        status=TransactionStatus.COMPLETED
    )
    transaction.order_id = data.get('order_id') or ''
    await transaction.asave()
    args = (None, Action.PAYMENT_SUCCESS, customer_ids)
    await utils.broadcast(*args, **action_kwargs)
    await utils.notify_users(
        [transfer.operator_id],
        gettext_noop('New Transfer'),
        gettext_noop('Transfer #%(id)s is paid and waiting for a driver'),
        Action.TRANSFER_ACCEPTED,
        transfer
    )
    args = (None, Action.TRANSFER_ACCEPTED, [transfer.operator_id])
    await utils.broadcast(*args, **action_kwargs)


# Tip

async def get_tip_link(transfer, amount):
    driver = transfer.driver
    await expire_payment_link(transfer, 'tip')
    transaction = get_transaction(transfer, Category.TIP, amount)
    data = {
        'psp': transfer.state.psp.lower(),
        'transfer': utils.get_dict(transfer),
        'customer': utils.get_dict(transfer.customer),
        'provider': driver.psp_data,
        'transaction': utils.get_dict(transaction),
    }
    response = await async_post('/createTipLink', data)
    transfer.psp_data['tip_psp'] = response['psp']
    transfer.psp_data['tip_id'] = response['payment_id']
    transfer.psp_data['tip_link'] = response['payment_link']
    transfer.psp_data['tip_link_expire_time'] = response['link_expire_time']
    transfer.psp_data['tip_amount'] = str(amount)


async def process_tip_return(data):
    transfer_id = str(data.get('id', ''))
    if not transfer_id.isdigit():
        return
    transfers = models.Transfer.objects.filter(pk=transfer_id)
    transfer = await transfers.afirst()
    if not (transfer and transfer.psp_data.get('tip_amount')):
        return
    action_kwargs = {'transfer_id': transfer_id}
    customer_id, driver_id = transfer.customer_id, transfer.driver_id
    if not data.get('success'):
        args = (None, Action.TIP_PAYMENT_FAILED, [customer_id])
        await utils.broadcast(*args, **action_kwargs)
        return
    amount = Decimal(transfer.psp_data['tip_amount'])
    for key in TIP_KEYS:
        transfer.psp_data.pop(key, None)
    # Conditional update: a repeated return finds tip_amount removed
    tip_pending = transfers.filter(psp_data__has_key='tip_amount')
    updated = await tip_pending.aupdate(psp_data=transfer.psp_data)
    if not updated:
        return
    transaction = get_transaction(
        transfer, Category.TIP, amount,
        status=TransactionStatus.COMPLETED
    )
    transaction.order_id = data.get('order_id') or ''
    await transaction.asave()
    args = (None, Action.TIP_PAYMENT_SUCCESS, [customer_id])
    await utils.broadcast(*args, **action_kwargs)
    if driver_id:
        await utils.notify_users(
            [driver_id],
            gettext_noop('New Tip'),
            gettext_noop('You received a tip for transfer #%(id)s'),
            Action.TIP_PAYMENT_SUCCESS,
            transfer,
            sender_id=customer_id
        )
        args = (customer_id, Action.TIP_PAYMENT_SUCCESS, [driver_id])
        await utils.broadcast(*args, **action_kwargs)


FUNCTIONS = {
    'PAYMENT_RETURN': process_payment_return,
    'TIP_RETURN': process_tip_return,
}


async def process_psp_return(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    access_token = request.headers.get('X-Access-Token', '')
    expected = settings.MS_ACCESS_TOKEN
    if not (expected and compare_digest(access_token, expected)):
        return HttpResponseForbidden()
    try:
        data = loads(request.body)
    except ValueError:
        return HttpResponseBadRequest()
    if not isinstance(data, dict):
        return HttpResponseBadRequest()
    func = FUNCTIONS.get(data.pop('action', None))
    if func:
        await func(data)
    return HttpResponse('OK')


# Refund
#
# Every refund runs in a DB transaction holding a row lock
# (select_for_update of='self', only the transaction row: the nullable
# parent join cannot be locked in PostgreSQL): on the parent PAYMENT/TIP for
# a new refund, on the refund itself for a retry. Concurrent requests wait
# for the lock and then see the committed refunds, so the same amount is
# never refunded twice.
# The refund row is created PENDING before /refundPayment and committed even
# if the payment service fails, it becomes COMPLETED on success.

def get_refunded_amount(transaction):
    """PENDING refunds count too, they reserve their amount for the retry."""
    refunds = transaction.refunds.aggregate(total=Sum('order_amount'))
    return refunds['total'] or ZERO


def send_refund(refund):
    """
    Sync, called holding the lock. Returns the payment service error
    (refund stays PENDING) or None (refund COMPLETED).
    """
    parent, transfer = refund.parent, refund.transfer
    psp_key = 'tip_psp' if parent.category == Category.TIP else 'psp'
    data = {
        'psp': transfer.psp_data.get(psp_key),
        'transactions': [utils.get_dict(parent), utils.get_dict(refund)]
    }
    try:
        response = a2s(async_post)('/refundPayment', data)
    except Error as error:
        logger.warning('Refund %s stays PENDING', refund.id)
        refund.error_code = getattr(error, 'error_code', 'ERR031')
        refund.save(update_fields=['error_code'])
        return error
    refund.order_id = response.get('order_id') or ''
    refund.status = TransactionStatus.COMPLETED
    refund.error_code = ''
    refund.save(update_fields=['order_id', 'status', 'error_code'])
    return None


@s2a
def create_refund(transaction_id, amount, note, kept=ZERO):
    """
    amount None: all refundable except kept. Returns (refund, error), refund
    is None when nothing is left to refund and amount was None.
    """
    with db_transaction.atomic():
        transactions = models.Transaction.objects.select_for_update(
            of=('self',)
        )
        parent = transactions.select_related('transfer').get(
            pk=transaction_id
        )
        refundable = parent.order_amount - get_refunded_amount(parent)
        if amount is None:
            amount = refundable - kept
            if amount <= ZERO:
                return None, None
        amount = amount.quantize(ZERO)
        assert ZERO < amount <= refundable, dumps({'code': 'ERR032'})
        refund = get_transaction(
            parent.transfer, Category.REFUND, amount,
            parent=parent, note=note
        )
        refund.currency = parent.currency
        refund.save()
        return refund, send_refund(refund)


@s2a
def resend_refund(refund_id):
    """Retries a PENDING or FAILED refund. Returns (refund, error)."""
    with db_transaction.atomic():
        refunds = models.Transaction.objects.select_for_update(
            of=('self',)
        )
        refund = refunds.select_related('parent', 'transfer').get(
            pk=refund_id
        )
        # A retry waiting for the lock finds it already completed
        retryable = refund.status != TransactionStatus.COMPLETED
        assert retryable, dumps({'code': 'ERR042'})
        return refund, send_refund(refund)


async def notify_refund(refund):
    if refund.status != TransactionStatus.COMPLETED:
        return
    await utils.notify_users(
        [refund.transfer.customer_id],
        gettext_noop('Refund'),
        gettext_noop('A refund was made for transfer #%(id)s'),
        Action.REFUND,
        refund.transfer
    )


async def refund_transaction(transaction, amount=None, note=''):
    """
    Refunds a PAYMENT or TIP transaction, all of the remaining amount when
    amount is None. Returns the refund, PENDING if the payment service
    failed (retry with retry_refund).
    """
    refund, _ = await create_refund(transaction.id, amount, note)
    # amount None and nothing left to refund
    assert refund, dumps({'code': 'ERR032'})
    await notify_refund(refund)
    return refund


async def retry_refund(refund):
    refund, _ = await resend_refund(refund.id)
    await notify_refund(refund)
    return refund


def get_penalty_rate(transfer, cancel_time, waive_penalty=False):
    if waive_penalty:
        return ZERO
    state = transfer.state
    iterator = (
        Decimal(rate)
        for hour, rate in zip(
            state.customer_imposed_hours,
            state.customer_imposed_rates,
        )
        if transfer.pickup_time - cancel_time < timedelta(hours=hour)
    )
    penalty_rate = next(iterator, ZERO)
    return penalty_rate


async def refund_cancelled_transfer(transfer, cancel_time):
    """
    Refunds the transfer payment except the penalty share, if paid. A failed
    refund stays PENDING, the cancellation still goes through.
    """
    payments = transfer.transactions.filter(category=Category.PAYMENT)
    payment = await payments.afirst()
    if not payment:
        return None
    penalty_rate = get_penalty_rate(transfer, cancel_time)
    kept = (payment.order_amount * penalty_rate).quantize(ZERO)
    args = (payment.id, None, 'CANCELLED', kept)
    refund, _ = await create_refund(*args)
    if refund:
        await notify_refund(refund)
    return refund
