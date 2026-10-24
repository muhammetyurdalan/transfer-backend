from datetime import timedelta
from decimal import Decimal
from django.conf import settings
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
Action = enums.Action
ZERO = models.ZERO
PAYMENT_KEYS = (
    'payment_id', 'payment_link', 'payment_link_expire_time', 'order_amount'
)
TIP_KEYS = ('tip_id', 'tip_link', 'tip_link_expire_time', 'tip_amount')


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
        logger.error('PSP %s failed: %r', path, error)
        raise Error(dumps({'code': 'ERR031'}))
    if response.get('success'):
        return response['data']
    logger.error('PSP %s failed: %s', path, response.get('error'))
    raise Error(dumps({'code': 'ERR031'}))


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
        'state': settings.PSP_STATE,
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


async def expire_payment_link(transfer):
    if not transfer.psp_data.get('payment_link'):
        return
    data = {'psp': transfer.psp_data.get('psp'), 'psp_data': transfer.psp_data}
    await async_post('/expirePaymentLink', data)
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
    action_kwargs = {'transfer_id': transfer_id}
    customer_ids = [transfer.customer_id]
    if not data.get('success'):
        if transfer.status == Status.PENDING:
            args = (None, Action.PAYMENT_FAILED, customer_ids)
            await utils.broadcast(*args, **action_kwargs)
        return
    now = timezone.now()
    pending = transfers.filter(status=Status.PENDING)
    # Conditional update: a repeated return is processed only once
    updated = await pending.aupdate(status=Status.PAID, updated_at=now)
    if not updated:
        logger.warning(
            'Payment return for transfer %s in status %s', transfer_id,
            transfer.status
        )
        return
    amount = Decimal(transfer.psp_data.get('order_amount', transfer.price))
    transaction = get_transaction(transfer, Category.PAYMENT, amount)
    transaction.order_id = data.get('order_id') or ''
    await transaction.asave()
    args = (None, Action.PAYMENT_SUCCESS, customer_ids)
    await utils.broadcast(*args, **action_kwargs)
    operator_ids = await utils.get_operator_ids()
    await utils.notify_users(
        operator_ids,
        gettext_noop('New Transfer'),
        gettext_noop('Transfer #%(id)s is paid and waiting for a driver'),
        Action.TRANSFER_PAID,
        transfer
    )
    args = (None, Action.TRANSFER_PAID, operator_ids)
    await utils.broadcast(*args, **action_kwargs)


# Tip

async def get_tip_link(transfer, amount):
    driver = transfer.driver
    transaction = get_transaction(transfer, Category.TIP, amount)
    data = {
        'state': settings.PSP_STATE,
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
    transaction = get_transaction(transfer, Category.TIP, amount)
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

async def get_refundable_amount(transaction):
    refunds = transaction.refunds.aaggregate(total=Sum('order_amount'))
    refunded = (await refunds)['total'] or ZERO
    return transaction.order_amount - refunded


async def refund_transaction(transaction, amount=None, note=''):
    """
    Refunds a PAYMENT or TIP transaction (transfer must be loaded), all of
    the remaining amount when amount is None.
    """
    refundable = await get_refundable_amount(transaction)
    amount = refundable if amount is None else amount.quantize(ZERO)
    assert ZERO < amount <= refundable, dumps({'code': 'ERR032'})
    transfer = transaction.transfer
    refund = get_transaction(
        transfer, Category.REFUND, amount,
        parent=transaction, note=note
    )
    refund.currency = transaction.currency
    psp_key = 'tip_psp' if transaction.category == Category.TIP else 'psp'
    data = {
        'psp': transfer.psp_data.get(psp_key),
        'transactions': [
            utils.get_dict(transaction),
            utils.get_dict(refund)
        ]
    }
    response = await async_post('/refundPayment', data)
    refund.order_id = response.get('order_id') or ''
    await refund.asave()
    await utils.notify_users(
        [transfer.customer_id],
        gettext_noop('Refund'),
        gettext_noop('A refund was made for transfer #%(id)s'),
        Action.REFUND,
        transfer
    )
    return refund


def get_penalty_rate(transfer, cancel_time):
    remaining = transfer.pickup_time - cancel_time
    for rule in settings.CANCEL_PENALTY_RULES:
        if remaining < timedelta(hours=rule['hours']):
            return Decimal(rule['rate'])
    return ZERO


async def refund_cancelled_transfer(transfer, penalty_rate=ZERO):
    """Refunds the transfer payment except the penalty share, if paid."""
    payments = transfer.transactions.select_related('transfer')
    payment = await payments.filter(category=Category.PAYMENT).afirst()
    if not payment:
        return None
    kept = (payment.order_amount * penalty_rate).quantize(ZERO)
    amount = await get_refundable_amount(payment) - kept
    if amount <= ZERO:
        return None
    return await refund_transaction(payment, amount, note='CANCELLED')
