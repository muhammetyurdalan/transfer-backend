from django.core.exceptions import FieldDoesNotExist
from django.core.files.uploadedfile import UploadedFile
from json import load
from pathlib import Path
from strawberry import Schema
from strawberry.extensions import QueryDepthLimiter, SchemaExtension
from strawberry.file_uploads import Upload
from strawberry.tools import merge_types
from strawberry.utils.str_converters import to_snake_case
from strawberry_django.optimizer import DjangoOptimizerExtension
from core.decorators import get_user
from core.schemas.message import Query as message_query
from core.schemas.message import Mutation as message_mutation
from core.schemas.notification import Query as notification_query
from core.schemas.notification import Mutation as notification_mutation
from core.schemas.transaction import Query as transaction_query
from core.schemas.transaction import Mutation as transaction_mutation
from core.schemas.transfer import Query as transfer_query
from core.schemas.transfer import Mutation as transfer_mutation
from core.schemas.user import Query as user_query
from core.schemas.user import Mutation as user_mutation
from core.schemas.user import Subscription as user_subscription
from core.schemas.vehicle import Query as vehicle_query
from core.schemas.vehicle import Mutation as vehicle_mutation
from core.schemas.vehicle_category import Query as vehicle_category_query
from core.schemas.vehicle_category import (
    Mutation as vehicle_category_mutation
)


queries = (
    message_query,
    notification_query,
    transaction_query,
    transfer_query,
    user_query,
    vehicle_query,
    vehicle_category_query,
)

mutations = (
    message_mutation,
    notification_mutation,
    transaction_mutation,
    transfer_mutation,
    user_mutation,
    vehicle_mutation,
    vehicle_category_mutation,
)

subscriptions = (
    user_subscription,
)


Query = merge_types('Query', queries)
Mutation = merge_types('Mutation', mutations)
Subscription = merge_types('Subscription', subscriptions)

with open(Path(__file__).resolve().parent / 'permissions.json') as file:
    PERMISSIONS = load(file)

MAX_QUERY_DEPTH = 10


class PermissionExtension(SchemaExtension):
    def resolve(self, _next, root, info, *args, **kwargs):
        user = get_user(info.context)
        model_name = root.__class__.__name__
        field_name = to_snake_case(info.field_name)
        permissions = PERMISSIONS.get(model_name, {}).get(field_name, [])
        if not permissions:
            pass
        elif not (user and ('ALL' in permissions or user.role in permissions)):
            return None
        try:
            field_type = root._meta.get_field(field_name).get_internal_type()
            if field_type == 'FileField':
                document = getattr(root, field_name)
                try:
                    return document.url
                except ValueError:
                    return None
        except (AttributeError, FieldDoesNotExist):
            pass
        return _next(root, info, *args, **kwargs)


schema = Schema(
    query=Query,
    mutation=Mutation,
    subscription=Subscription,
    scalar_overrides={UploadedFile: Upload},
    extensions=[
        PermissionExtension,
        DjangoOptimizerExtension,
        lambda: QueryDepthLimiter(max_depth=MAX_QUERY_DEPTH)
    ]
)
