from django.db.models import Q
from json import dumps
import strawberry as sb
import strawberry_django as sbd
from strawberry import Info
from strawberry_django.pagination import OffsetPaginationInput
from typing import List, Optional
from core import enums, inputs, models, outputs, psp, utils
from core.decorators import permission_required


Category = enums.TransactionCategory
manager = models.Transaction.objects
REFUNDABLE_CATEGORIES = [Category.PAYMENT, Category.TIP]


def get_transactions(user):
    """
    ADMIN/STAFF: all. DRIVER: tips (and their refunds) of own transfers.
    Others: own payments, tips and refunds.
    """
    transactions = manager.all()
    if user.role in utils.STAFF_ROLES:
        return transactions
    if user.role == enums.Role.DRIVER:
        tips = Q(category=Category.TIP) | Q(parent__category=Category.TIP)
        return transactions.filter(tips, transfer__driver=user)
    return transactions.filter(user=user)


@sb.type
class Query:
    @sbd.field(description='ALL')
    @permission_required('ALL')
    async def transactions(
        root, info: Info,
        filters: Optional[outputs.TransactionFilter] = sb.UNSET,
        ordering: Optional[List[outputs.TransactionOrder]] = sb.UNSET,
        pagination: Optional[OffsetPaginationInput] = sb.UNSET
    ) -> outputs.TransactionsPaginated:
        user = info.context['user']
        transactions = get_transactions(user)
        if filters:
            transactions = sbd.filters.apply(filters, transactions, info)
        count = await utils.get_count(info, transactions)
        if ordering:
            args = (outputs.TransactionOrder, ordering, info, transactions)
            transactions = sbd.ordering.apply_ordering(*args)
        if pagination:
            transactions = sbd.pagination.apply(pagination, transactions)
        return outputs.TransactionsPaginated(
            transactions=transactions,
            count=count
        )


@sb.type
class Mutation:
    @sb.mutation(description='ADMIN, STAFF')
    @permission_required('ADMIN', 'STAFF')
    async def refund_transaction(
        root, info: Info,
        data: inputs.RefundTransaction
    ) -> outputs.Transaction:
        """Full or partial refund of a PAYMENT or TIP transaction."""
        filters = {'pk': data.id, 'category__in': REFUNDABLE_CATEGORIES}
        transactions = manager.select_related('transfer').filter(**filters)
        transaction = await transactions.afirst()
        assert transaction, dumps({'code': 'ERR034'})
        amount = data.amount or None
        note = data.note or ''
        return await psp.refund_transaction(transaction, amount, note)
