from django.db.models import ProtectedError
from django.utils.translation import activate
from re import fullmatch
from json import dumps
import strawberry as sb
import strawberry_django as sbd
from strawberry import Info
from strawberry.exceptions import StrawberryGraphQLError as Error
from typing import List, Optional
from core import enums, inputs, models, outputs, utils
from core.decorators import get_public_user, permission_required


manager = models.Reason.objects
DESCRIPTION_FIELDS = [
    'description_en', 'description_tr', 'description_de', 'description_ru'
]


def check_reason(data):
    """
    Descriptions stripped, 200 chars max, en not empty; code snake_case,
    50 chars max (ERR030).
    """
    fields = utils.get_fields(data)
    for key in DESCRIPTION_FIELDS:
        value = fields.get(key)
        if isinstance(value, str):
            fields[key] = value = value.strip()
            setattr(data, key, value)
        valid = value is None or len(value) <= 200
        assert valid, dumps({'code': 'ERR030'})
    code = fields.get('code')
    if code is not None:
        data.code = code = code.strip()
        valid = bool(fullmatch(r'[a-z0-9]+(_[a-z0-9]+)*', code))
        assert valid and len(code) <= 50, dumps({'code': 'ERR030'})
    order = fields.get('order')
    valid = fields.get('description_en', 'description') != ''
    valid = valid and (order is None or 0 <= order <= 32767)
    assert valid, dumps({'code': 'ERR030'})


async def check_unique_code(code, exclude_id=None):
    reasons = manager.filter(code=code).exclude(pk=exclude_id)
    assert not await reasons.aexists(), dumps({'code': 'ERR053'})


@sb.type
class Query:
    @sbd.field(description='Public (inactive ones for ADMIN, STAFF)')
    async def reasons(
        root, info: Info,
        category: Optional[enums.ReasonCategory] = sb.UNSET,
        code: Optional[str] = sb.UNSET,
        is_active: Optional[bool] = sb.UNSET,
        language: Optional[enums.Language] = sb.UNSET
    ) -> List[outputs.Reason]:
        """
        Public (bookings without an account list the cancellation reasons).
        `description` is in `language`, or in the user's language (en without
        a token). Inactive reasons only for ADMIN/STAFF (`isActive` filters
        them).
        """
        user = await get_public_user(info)
        activate(language or (user and user.language) or enums.Language.EN)
        reasons = manager.all()
        if not (user and user.role in utils.STAFF_ROLES):
            reasons = reasons.filter(is_active=True)
        elif is_active is not None and is_active is not sb.UNSET:
            reasons = reasons.filter(is_active=is_active)
        if category:
            reasons = reasons.filter(category=category)
        if code:
            reasons = reasons.filter(code=code)
        return reasons


@sb.type
class Mutation:
    @sb.mutation(description='ADMIN, STAFF')
    @permission_required('ADMIN', 'STAFF')
    async def create_reason(
        root, info: Info,
        data: inputs.CreateReason
    ) -> outputs.Reason:
        check_reason(data)
        await check_unique_code(data.code)
        reason = models.Reason()
        utils.set_attributes(reason, data, set_nulls=False)
        await reason.asave()
        return reason

    @sb.mutation(description='ADMIN, STAFF')
    @permission_required('ADMIN', 'STAFF')
    async def update_reason(
        root, info: Info,
        data: inputs.UpdateReason
    ) -> outputs.Reason:
        check_reason(data)
        reason = await manager.filter(pk=data.id).afirst()
        assert reason, dumps({'code': 'ERR050'})
        if data.code:
            await check_unique_code(data.code, reason.id)
        ignored = ['id']
        utils.set_attributes(reason, data, ignored=ignored, set_nulls=False)
        await reason.asave()
        return reason

    @sb.mutation(description='ADMIN, STAFF')
    @permission_required('ADMIN', 'STAFF')
    async def delete_reason(
        root, info: Info,
        id: sb.ID
    ) -> outputs.SuccessResponse:
        """A reason used by transfers can't be deleted (ERR054), deactivate it."""
        try:
            deleted, _ = await manager.filter(pk=id).adelete()
        except ProtectedError:
            raise Error(dumps({'code': 'ERR054'}))
        assert deleted, dumps({'code': 'ERR050'})
        return outputs.SuccessResponse(success=True)
