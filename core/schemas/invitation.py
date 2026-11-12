from datetime import timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone
from django.utils.translation import activate, gettext
from json import dumps
from urllib.parse import quote
import strawberry as sb
import strawberry_django as sbd
from strawberry import Info
from strawberry.exceptions import StrawberryGraphQLError as Error
from strawberry_django.pagination import OffsetPaginationInput
from typing import List, Optional
from core import enums, inputs, models, outputs, tasks, utils
from core.decorators import permission_required


manager = models.Invitation.objects
Status = enums.InvitationStatus


async def get_pending_invitation(decoded_data):
    """PENDING invitation of a decoded invitation secret, None if not one."""
    invitation_id = decoded_data.get('invitation_id')
    if not invitation_id:
        return None
    kwargs = {
        'pk': invitation_id,
        'receiver_email': decoded_data.get('email'),
        'status': Status.PENDING,
    }
    return await manager.filter(**kwargs).afirst()


def send_invitation(invitation, sender):
    """Emails the invitation link (action 'invitation') via the microservice."""
    payload = {
        'email': invitation.receiver_email,
        'language': invitation.language,
        'invitation_id': invitation.id,
        'is_validated': True,
        'exp': invitation.expires_at,
    }
    secret = utils.encode_secret(payload)
    link = f'{settings.FRONTEND_URL}/register?invitation={quote(secret)}'
    activate(invitation.language)
    subject = gettext('You are invited to {app_name}')
    subject = subject.format(app_name=settings.APP_NAME)
    tasks.send_email.delay({
        'action': 'invitation',
        'message': secret,
        'link': link,
        'subject': subject,
        'to_user': {
            'email': invitation.receiver_email,
            'language': invitation.language,
        },
        'from_user': {
            'first_name': sender.first_name,
            'last_name': sender.last_name,
            'email': sender.email,
        },
    })


@sb.type
class Query:
    @sbd.field(description='ADMIN, STAFF, OPERATOR (OPERATOR: own invitations)')
    @permission_required('ADMIN', 'STAFF', 'OPERATOR')
    async def invitations(
        root, info: Info,
        filters: Optional[outputs.InvitationFilter] = sb.UNSET,
        ordering: Optional[List[outputs.InvitationOrder]] = sb.UNSET,
        pagination: Optional[OffsetPaginationInput] = sb.UNSET
    ) -> outputs.InvitationsPaginated:
        user = info.context['user']
        invitations = manager.all()
        if user.role == enums.Role.OPERATOR:
            invitations = invitations.filter(sender=user)
        if filters:
            invitations = sbd.filters.apply(filters, invitations, info)
        count = await utils.get_count(info, invitations)
        if ordering:
            args = (outputs.InvitationOrder, ordering, info, invitations)
            invitations = sbd.ordering.apply_ordering(*args)
        if pagination:
            invitations = sbd.pagination.apply(pagination, invitations)
        return outputs.InvitationsPaginated(
            invitations=invitations, count=count
        )

    @sb.field
    async def invitation_info(
        root, info: Info,
        secret: str
    ) -> outputs.InvitationInfo:
        """Public: email and language of a PENDING invitation (sign-up form)."""
        decoded_data = utils.decode_secret(secret)
        invitation = await get_pending_invitation(decoded_data)
        assert invitation, dumps({'code': 'ERR044'})
        return outputs.InvitationInfo(
            email=invitation.receiver_email,
            language=enums.Language(invitation.language),
        )


@sb.type
class Mutation:
    @sb.mutation(description='ADMIN, STAFF, OPERATOR')
    @permission_required('ADMIN', 'STAFF', 'OPERATOR')
    async def invite_customer(
        root, info: Info,
        data: inputs.InviteCustomer
    ) -> outputs.Invitation:
        """
        Emails a sign-up link. An existing PENDING invitation to the same
        email is renewed and sent again. Active user with the email: ERR011.
        """
        sender = info.context['user']
        email = utils.normalize_email(data.email)
        try:
            validate_email(email)
        except ValidationError:
            raise Error(dumps({'code': 'ERR030'}))
        users = utils.User.objects.filter(email=email, is_active=True)
        assert not await users.aexists(), dumps({'code': 'ERR011'})
        language = data.language or sender.language or enums.Language.EN
        expire_days = settings.INVITATION_EXPIRE_DAYS
        kwargs = {'receiver_email': email, 'status': Status.PENDING}
        invitation = await manager.filter(**kwargs).afirst()
        invitation = invitation or models.Invitation(**kwargs)
        invitation.sender = sender
        invitation.receiver_role = enums.Role.CUSTOMER
        invitation.language = enums.Language(language).value
        invitation.expires_at = timezone.now() + timedelta(days=expire_days)
        await invitation.asave()
        send_invitation(invitation, sender)
        return invitation
