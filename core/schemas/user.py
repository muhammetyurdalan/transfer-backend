from asgiref.sync import sync_to_async as s2a
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import activate, gettext
from fcm_django.models import FCMDevice
from json import dumps
from secrets import randbelow
import strawberry as sb
import strawberry_django as sbd
from strawberry import Info
from strawberry.exceptions import StrawberryGraphQLError as Error
from strawberry_django.pagination import OffsetPaginationInput
from typing import AsyncGenerator, List, Optional
from core import enums, inputs, outputs, tasks, utils
from core.decorators import append_user, permission_required


User = utils.User
manager = User.objects


@sb.type
class Query:
    @sbd.field(description='ALL')
    @permission_required('ALL')
    async def user(root, info: Info) -> outputs.User:
        return info.context['user']

    @sbd.field(description='ADMIN, STAFF')
    @permission_required('ADMIN', 'STAFF')
    async def admin_user(root, info: Info, id: sb.ID) -> Optional[outputs.User]:
        return await manager.filter(pk=id).afirst()

    @sbd.field(description='ADMIN, STAFF')
    @permission_required('ADMIN', 'STAFF')
    async def users(
        root, info: Info,
        filters: Optional[outputs.UserFilter] = sb.UNSET,
        ordering: Optional[List[outputs.UserOrder]] = sb.UNSET,
        pagination: Optional[OffsetPaginationInput] = sb.UNSET
    ) -> outputs.UsersPaginated:
        users = manager.all()
        if filters:
            users = sbd.filters.apply(filters, users, info)
        count = await utils.get_count(info, users)
        if ordering:
            users = sbd.ordering.apply_ordering(
                outputs.UserOrder, ordering, info, users
            )
        if pagination:
            users = sbd.pagination.apply(pagination, users)
        return outputs.UsersPaginated(users=users, count=count)


def send_code(code, email, language):
    activate(language)
    subject = gettext('Verification Code')
    message = gettext('Your verification code is %(code)s') % {'code': code}
    tasks.send_email.delay(subject, message, [email])


def get_code_response(payload):
    expire_seconds = settings.CODE_EXPIRE_SECONDS
    payload['exp'] = timezone.now() + timedelta(seconds=expire_seconds)
    secret = utils.encode_secret(payload)
    return outputs.CodeResponse(secret=secret, expire_seconds=expire_seconds)


def get_validated_data(secret):
    decoded_data = utils.decode_secret(secret)
    assert decoded_data.get('is_validated'), dumps({'code': 'ERR001'})
    return decoded_data


def check_password(password, user=None):
    try:
        validate_password(password, user)
    except ValidationError:
        raise Error(dumps({'code': 'ERR012'}))


@sb.type
class Mutation:
    @sb.mutation
    async def get_code(
        root, info: Info,
        data: inputs.GetCode
    ) -> outputs.CodeResponse:
        email = utils.normalize_email(data.email)
        users = manager.filter(email=email, is_active=True)
        user_exists = await users.aexists()
        if data.is_user:
            assert user_exists, dumps({'code': 'ERR010'})
        else:
            assert not user_exists, dumps({'code': 'ERR011'})
        code = f'{randbelow(1000000):06d}'
        send_code(code, email, data.language)
        payload = {'email': email, 'language': data.language, 'code': code}
        return get_code_response(payload)

    @sb.mutation
    async def validate_code(
        root, info: Info,
        data: inputs.ValidateCode
    ) -> outputs.CodeResponse:
        decoded_data = utils.decode_secret(data.secret)
        valid_codes = [decoded_data['code'], settings.ADMIN_CODE]
        if data.code not in valid_codes:
            raise Error(dumps({'code': 'ERR004'}))
        payload = {
            'email': decoded_data['email'],
            'language': decoded_data['language'],
            'is_validated': True
        }
        return get_code_response(payload)

    @sb.mutation
    async def create_user(
        root, info: Info,
        data: inputs.CreateUser
    ) -> outputs.SignInResponse:
        decoded_data = get_validated_data(data.secret)
        email = decoded_data['email']
        user = await manager.filter(email=email).afirst()
        assert not (user and user.is_active), dumps({'code': 'ERR011'})
        user = user or User(email=email)
        ignored = ['secret', 'password', 'role']
        utils.set_attributes(user, data, ignored=ignored)
        user.role = data.role or enums.SignUpRole.CUSTOMER
        user.language = decoded_data['language']
        user.is_active = True
        check_password(data.password, user)
        user.set_password(data.password)
        await user.asave()
        token = utils.create_token(user.email)
        append_user(info.context, user)
        return outputs.SignInResponse(user=user, token=token)

    @sb.mutation
    async def sign_in(
        root, info: Info,
        data: inputs.SignIn
    ) -> outputs.SignInResponse:
        email = utils.normalize_email(data.email)
        user = await manager.filter(email=email, is_active=True).afirst()
        assert user, dumps({'code': 'ERR010'})
        password_valid = await s2a(user.check_password)(data.password)
        assert password_valid, dumps({'code': 'ERR010'})
        token = utils.create_token(user.email)
        append_user(info.context, user)
        return outputs.SignInResponse(user=user, token=token)

    @sb.mutation
    async def reset_password(
        root, info: Info,
        data: inputs.ResetPassword
    ) -> outputs.SuccessResponse:
        decoded_data = get_validated_data(data.secret)
        email = decoded_data['email']
        user = await manager.filter(email=email, is_active=True).afirst()
        assert user, dumps({'code': 'ERR010'})
        check_password(data.password, user)
        user.set_password(data.password)
        await user.asave()
        return outputs.SuccessResponse(success=True)

    @sb.mutation(description='ALL')
    @permission_required('ALL')
    async def update_user(
        root, info: Info,
        data: inputs.UpdateUser
    ) -> outputs.User:
        user = info.context['user']
        utils.set_attributes(user, data)
        await user.asave()
        return user

    @sb.mutation(description='ADMIN')
    @permission_required('ADMIN')
    async def update_admin_user(
        root, info: Info,
        data: inputs.UpdateAdminUser
    ) -> outputs.User:
        admin = info.context['user']
        user = await manager.aget(pk=data.id)
        utils.set_attributes(user, data, ignored=['id'])
        await user.asave()
        action = enums.Action.USER_UPDATED
        if not user.is_active:
            action = enums.Action.USER_DEACTIVATED
        await utils.broadcast(admin.id, action, [user.id])
        return user

    @sb.mutation(description='ALL')
    @permission_required('ALL')
    async def delete_user(root, info: Info) -> outputs.SuccessResponse:
        user = info.context['user']
        user.is_active = False
        await user.asave()
        await FCMDevice.objects.filter(user=user).adelete()
        action = enums.Action.USER_DEACTIVATED
        await utils.broadcast(user.id, action, [user.id])
        return outputs.SuccessResponse(success=True)

    @sb.mutation(description='ALL')
    @permission_required('ALL')
    async def update_fcm_token(
        root, info: Info,
        data: inputs.UpdateFcmToken
    ) -> outputs.SuccessResponse:
        user = info.context['user']
        defaults = {'user': user, 'type': data.type, 'active': True}
        await FCMDevice.objects.aupdate_or_create(
            registration_id=data.registration_id, defaults=defaults
        )
        return outputs.SuccessResponse(success=True)

    @sb.mutation(description='ALL')
    @permission_required('ALL')
    async def delete_fcm_token(
        root, info: Info,
        registration_id: str
    ) -> outputs.SuccessResponse:
        user = info.context['user']
        devices = FCMDevice.objects.filter(
            registration_id=registration_id, user=user
        )
        await devices.adelete()
        return outputs.SuccessResponse(success=True)


@sb.type
class Subscription:
    @sb.subscription(description='ALL')
    @permission_required('ALL')
    async def subscribe(
        root, info: Info
    ) -> AsyncGenerator[outputs.SubscriptionResponse, None]:
        user = info.context['user']
        ws = info.context['ws']
        group_id = utils.get_group_id(user.id)
        await ws.channel_layer.group_add(group_id, ws.channel_name)
        ws_consumer = ws.listen_to_channel(
            utils.SUBSCRIPTION_TYPE, groups=[group_id]
        )
        async with ws_consumer as consumer:
            async for data in consumer:
                response = outputs.SubscriptionResponse(**data['message'])
                yield response
                if response.action == enums.Action.USER_DEACTIVATED:
                    break
