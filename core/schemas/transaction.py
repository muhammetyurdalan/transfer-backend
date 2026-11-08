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


get_transactions = utils.get_transactions


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
        if filters and filters.is_payout_sent is not sb.UNSET:
            # Hidden field: others could infer it by filtering
            allowed = user.role in utils.DISPATCH_ROLES
            assert allowed, dumps({'code': 'ERR001'})
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

    @sb.mutation(description='CUSTOMER, OPERATOR, ADMIN, STAFF')
    @permission_required('CUSTOMER', 'OPERATOR', 'ADMIN', 'STAFF')
    async def retry_refund(
        root, info: Info,
        id: sb.ID
    ) -> outputs.Transaction:
        """
        Resends a PENDING refund (the payment service failed before).
        Scoped like the transactions query: CUSTOMER own refunds, OPERATOR
        refunds of the transfers they operate. Still PENDING if it fails again.
        """
        user = info.context['user']
        refunds = get_transactions(user).filter(category=Category.REFUND)
        refund = await refunds.filter(pk=id).afirst()
        assert refund, dumps({'code': 'ERR034'})
        return await psp.retry_refund(refund)

    @sb.mutation(description='OPERATOR, ADMIN, STAFF')
    @permission_required('OPERATOR', 'ADMIN', 'STAFF')
    async def update_transaction(
        root, info: Info,
        data: inputs.UpdateTransaction
    ) -> outputs.Transaction:
        """OPERATOR: only transactions of the transfers they operate."""
        user = info.context['user']
        transaction = await get_transactions(user).filter(pk=data.id).afirst()
        assert transaction, dumps({'code': 'ERR034'})
        fields = {
            key: value for key, value in utils.get_fields(data).items()
            if value is not None
        }
        utils.set_attributes(transaction, fields)
        await transaction.asave(update_fields=list(fields) or None)
        return transaction
