import asyncio
from asgiref.sync import sync_to_async as s2a
from datetime import timedelta
from decimal import Decimal
from django.apps import apps
from django.core.cache import cache
from django.db.backends.utils import CursorWrapper
from django.test import TestCase, override_settings
from django.utils import timezone
from fcm_django.models import FCMDevice
from importlib import import_module
from json import dumps, loads
from strawberry.channels.testing import GraphQLWebsocketCommunicator
from strawberry.exceptions import StrawberryGraphQLError as Error
from unittest.mock import AsyncMock, Mock, patch
from core import models, outputs, tasks, utils
from core.psp import PSPError, async_post as real_async_post
from transfer.asgi import application


User = utils.User
URL = '/graphql/'
PSP_RETURN_URL = '/psp-return/'
PASSWORD = 'S3cure-Passw0rd!'

SIGN_IN = '''
mutation($data: SignIn!) { signIn(data: $data) { token user { email } } }
'''
SOCIAL_SIGN_IN = '''
mutation($data: SocialSignIn!) {
  socialSignIn(data: $data) { token user { email role firstName lastName } }
}
'''
GET_CODE = '''
mutation($data: GetCode!) { getCode(data: $data) { secret } }
'''
VALIDATE_CODE = '''
mutation($data: ValidateCode!) { validateCode(data: $data) { secret } }
'''
CREATE_USER = '''
mutation($data: CreateUser!) { createCustomer(data: $data) { token user { role } } }
'''
VEHICLE_CATEGORIES = '''
query($language: Language) {
  vehicleCategories(language: $language) { count vehicleCategories { id name } }
}
'''
CREATE_VEHICLE_CATEGORY = '''
mutation($data: CreateVehicleCategory!) {
  createVehicleCategory(data: $data) { id name nameTr maxPassengers }
}
'''
CREATE_VEHICLE = '''
mutation($data: CreateVehicle!) {
  createVehicle(data: $data) { id plate driver { id } category { id } }
}
'''
TRANSFER_FIELDS = '''
  id status price currency vehicleCategoryId operatorId
  stateId pickupDistrictId dropoffDistrictId
  drivers { id driverId vehicleId isExternal }
'''
OPTION_FIELDS = '''
  vehicleCategory { id name } count distance duration price currency
'''
CREATE_TRANSFER = f'''
mutation($data: CreateTransfer!) {{
  createTransfer(data: $data) {{
    transfer {{ {TRANSFER_FIELDS} }} vehicleOptions {{ {OPTION_FIELDS} }}
  }}
}}
'''
UPDATE_TRANSFER = f'''
mutation($data: UpdateTransfer!) {{
  updateTransfer(data: $data) {{
    transfer {{ {TRANSFER_FIELDS} }} vehicleOptions {{ {OPTION_FIELDS} }}
  }}
}}
'''
VEHICLE_OPTIONS = f'''
query($transferId: ID!) {{ vehicleOptions(transferId: $transferId) {{ {OPTION_FIELDS} }} }}
'''
TRANSFER = f'query($id: ID!) {{ transfer(id: $id) {{ {TRANSFER_FIELDS} }} }}'
TRANSFERS = '{ transfers { count transfers { id } } }'
TRANSFERS_FILTERED = '''
query($filters: TransferFilter) {
  transfers(filters: $filters) { count transfers { id } }
}
'''
PAY_TRANSFER = '''
mutation($data: PayTransfer!) {
  payTransfer(data: $data) { paymentLink linkExpireTime }
}
'''
AVAILABLE_DRIVERS = '''
query($transferId: ID!) { availableDrivers(transferId: $transferId) { count users { id } } }
'''
AVAILABLE_VEHICLES = '''
query($transferId: ID!) { availableVehicles(transferId: $transferId) { id driverId category { id } } }
'''
ASSIGN_DRIVER = f'''
mutation($data: AssignDriver!) {{ assignDriver(data: $data) {{ {TRANSFER_FIELDS} }} }}
'''
DRIVER_FIELDS = '''
  id driverId vehicleId vehicleCategoryId isExternal driverName driverPhone
  plate vehicleBrand vehicleModel driverPrice driverPriceCurrency
'''
ASSIGN_DRIVER_WITH_PRICE = f'''
mutation($data: AssignDriver!) {{
  assignDriver(data: $data) {{ id drivers {{ {DRIVER_FIELDS} }} }}
}}
'''
UPDATE_TRANSFER_DRIVER = f'''
mutation($data: UpdateTransferDriver!) {{
  updateTransferDriver(data: $data) {{ {DRIVER_FIELDS} }}
}}
'''
REMOVE_TRANSFER_DRIVER = f'''
mutation($id: ID!) {{ removeTransferDriver(id: $id) {{ {TRANSFER_FIELDS} }} }}
'''
TRANSFER_DRIVER = f'''
query($id: ID!) {{ transfer(id: $id) {{ id drivers {{ {DRIVER_FIELDS} }} }} }}
'''
UPDATE_TRANSFER_STATUS = f'''
mutation($data: UpdateTransferStatus!) {{
  updateTransferStatus(data: $data) {{ {TRANSFER_FIELDS} }}
}}
'''
CANCEL_TRANSFER = f'''
mutation($id: ID!) {{ cancelTransfer(data: {{id: $id, cancellationReasonText: "Plans changed"}}) {{ {TRANSFER_FIELDS} }} }}
'''
CANCEL_WITH_REASON = '''
mutation($data: CancelTransfer!) {
  cancelTransfer(data: $data) {
    status cancellationReasonText cancellationReason { code description }
  }
}
'''
REASON_FIELDS = (
    'id code category description descriptionEn descriptionTr order isActive'
)
REASONS = f'''
query($category: ReasonCategory, $code: String, $isActive: Boolean, $language: Language) {{
  reasons(category: $category, code: $code, isActive: $isActive, language: $language) {{
    {REASON_FIELDS}
  }}
}}
'''
CREATE_REASON = f'''
mutation($data: CreateReason!) {{ createReason(data: $data) {{ {REASON_FIELDS} }} }}
'''
UPDATE_REASON = f'''
mutation($data: UpdateReason!) {{ updateReason(data: $data) {{ {REASON_FIELDS} }} }}
'''
DELETE_REASON = '''
mutation($id: ID!) { deleteReason(id: $id) { success } }
'''
DELETE_TRANSFER = '''
mutation($id: ID!) { deleteTransfer(id: $id) { success } }
'''
TIP_DRIVER = '''
mutation($data: TipDriver!) { tipDriver(data: $data) { paymentLink } }
'''
CREATE_DRIVER = '''
mutation($data: CreateUser!) { createDriver(data: $data) { token user { id role } } }
'''
INVITE_CUSTOMER = '''
mutation($data: InviteCustomer!) {
  inviteCustomer(data: $data) { id receiverEmail language status expiresAt }
}
'''
INVITATIONS = '''
query { invitations { count invitations { receiverEmail status sender { id } } } }
'''
INVITATION_INFO = '''
query($secret: String!) { invitationInfo(secret: $secret) { email language } }
'''
CREATE_ADMIN_USER = '''
mutation($data: CreateAdminUser!) {
  createAdminUser(data: $data) { id role status }
}
'''
UPDATE_ADMIN_USER = '''
mutation($data: UpdateAdminUser!) {
  updateAdminUser(data: $data) { id role status }
}
'''
ADMIN_USER = '''
query($id: ID!) { adminUser(id: $id) { id status } }
'''
UPDATE_TRANSACTION = '''
mutation($data: UpdateTransaction!) {
  updateTransaction(data: $data) { id note isPayoutSent }
}
'''
PAYOUT_TRANSACTIONS = '''
{ transactions { count transactions { id isPayoutSent } } }
'''
TRANSACTIONS = '''
{ transactions { count transactions { id category orderAmount parentId } } }
'''
REFUND_TRANSACTION = '''
mutation($data: RefundTransaction!) {
  refundTransaction(data: $data) { id category orderAmount parentId status }
}
'''
RETRY_REFUND = '''
mutation($id: ID!) { retryRefund(id: $id) { id status orderAmount } }
'''
SEND_MESSAGE = '''
mutation($data: SendMessage!) {
  sendMessage(data: $data) { id message receivers { id } }
}
'''
MESSAGES = '''
query($transferId: ID!) { messages(transferId: $transferId) { id message } }
'''
SEE_MESSAGES = '''
mutation($transferId: ID!) { seeMessages(transferId: $transferId) { success } }
'''
SEE_MESSAGES_UP_TO = '''
mutation($transferId: ID!, $upToMessageId: ID) {
  seeMessages(transferId: $transferId, upToMessageId: $upToMessageId) { success }
}
'''
DELIVER_MESSAGES = '''
mutation($transferId: ID) { deliverMessages(transferId: $transferId) { success } }
'''
MESSAGE_STATUSES = '''
query($transferId: ID!) { messages(transferId: $transferId) { id status } }
'''
REACT_MESSAGE = '''
mutation($id: ID!, $emoji: ReactionEmoji) {
  reactMessage(id: $id, emoji: $emoji) { id reactions { emoji userId } }
}
'''
MESSAGE_REACTIONS = '''
query($transferId: ID!) {
  messages(transferId: $transferId) {
    id reactions { id emoji userId user { id name } }
  }
}
'''
DELETE_MESSAGE = '''
mutation($id: ID!) { deleteMessage(id: $id) { id status isDeleted } }
'''
UPDATE_MESSAGE = '''
mutation($data: UpdateMessage!) {
  updateMessage(data: $data) { id message isEdited }
}
'''


CITIES = '''
query($filters: CityFilter) {
  cities(filters: $filters, ordering: [{name: ASC}]) {
    count cities { name stateId state { name psp } }
  }
}
'''
DISTRICTS = '''
query($filters: DistrictFilter) {
  districts(filters: $filters, ordering: [{name: ASC}]) {
    count districts { name cityId }
  }
}
'''
CREATE_CITY = '''
mutation($data: CreateCity!) { createCity(data: $data) { id name stateId } }
'''
UPDATE_CITY = '''
mutation($data: UpdateCity!) { updateCity(data: $data) { id name stateId } }
'''
DELETE_CITY = '''
mutation($id: ID!) { deleteCity(id: $id) { success } }
'''
CREATE_DISTRICT = '''
mutation($data: CreateDistrict!) {
  createDistrict(data: $data) { id name cityId isActive }
}
'''
UPDATE_DISTRICT = '''
mutation($data: UpdateDistrict!) {
  updateDistrict(data: $data) { id name isActive }
}
'''
DELETE_DISTRICT = '''
mutation($id: ID!) { deleteDistrict(id: $id) { success } }
'''
STATE_FIELDS = '''
id name code psp customerImposedHours customerImposedRates
driverBufferMinutes allowMultipleVehicles allowUnconditionalRefund
cancelPriceRate
'''
STATES = '''
query { states(ordering: [{name: ASC}]) { count states { name code } } }
'''
CREATE_STATE = f'''
mutation($data: CreateState!) {{ createState(data: $data) {{ {STATE_FIELDS} }} }}
'''
UPDATE_STATE = f'''
mutation($data: UpdateState!) {{ updateState(data: $data) {{ {STATE_FIELDS} }} }}
'''
DELETE_STATE = '''
mutation($id: ID!) { deleteState(id: $id) { success } }
'''


def psp_response(path, data):
    if path in ('/createPaymentLink', '/createTipLink'):
        return {
            'psp': 'stripe',
            'payment_id': 'pay_1',
            'payment_link': f'https://pay.test{path}',
            'link_expire_time': '2030-01-01T00:00:00+00:00',
        }
    if path == '/refundPayment':
        return {'order_id': 'refund_1'}
    return {'is_expired': True}


class GraphQLTestCase(TestCase):
    def setUp(self):
        def create(email, role='CUSTOMER', status='ACCEPTED'):
            return User.objects.create(
                email=email, role=role, first_name=email.split('@')[0],
                last_name='Test', status=status, phone='+905550000000'
            )
        self.customer = create('customer@example.com')
        self.other_customer = create('other@example.com')
        self.operator = create('operator@example.com', 'OPERATOR')
        self.other_operator = create('operator2@example.com', 'OPERATOR')
        self.driver = create('driver@example.com', 'DRIVER')
        self.other_driver = create('driver2@example.com', 'DRIVER')
        self.admin = create('admin@example.com', 'ADMIN')
        self.staff = create('staff@example.com', 'STAFF')
        self.turkey = models.State.objects.create(name='Turkey', code='TR')
        self.greece = models.State.objects.create(name='Greece', code='GR')
        istanbul = models.City.objects.create(state=self.turkey, name='Istanbul')
        athens = models.City.objects.create(state=self.greece, name='Athens')
        self.arnavutkoy = models.District.objects.create(
            city=istanbul, name='Arnavutkoy'
        )
        self.beyoglu = models.District.objects.create(
            city=istanbul, name='Beyoglu'
        )
        self.glyfada = models.District.objects.create(
            city=athens, name='Glyfada'
        )
        models.District.objects.create(
            city=athens, name='Closed', is_active=False
        )
        # Transfers in these districts go to self.operator
        self.operator.districts.set(
            [self.arnavutkoy, self.beyoglu, self.glyfada]
        )
        self.sedan = models.VehicleCategory.objects.create(
            name_en='Sedan', name_tr='Binek', max_passengers=3,
            max_luggage=3, base_fare='100.00', per_km_price='10.00',
            per_minute_price='2.00', minimum_fare='300.00', order=1
        )
        self.van = models.VehicleCategory.objects.create(
            name_en='Van', name_tr='Minibüs', max_passengers=7,
            max_luggage=8, child_seat_capacity=2, base_fare='200.00',
            per_km_price='15.00', per_minute_price='3.00', order=2
        )
        self.accessible = models.VehicleCategory.objects.create(
            name_en='Accessible Van', max_passengers=4, max_luggage=4,
            is_wheelchair_accessible=True, base_fare='250.00', order=3
        )
        self.inactive = models.VehicleCategory.objects.create(
            name_en='Limousine', max_passengers=8, max_luggage=8,
            base_fare='900.00', is_active=False, order=4
        )
        self.driver_vehicle = models.Vehicle.objects.create(
            driver=self.driver, category=self.sedan, plate='34ABC01',
            brand='Toyota', model='Corolla'
        )
        self.other_driver_vehicle = models.Vehicle.objects.create(
            driver=self.other_driver, category=self.sedan, plate='34ABC02',
            brand='Fiat', model='Egea'
        )
        self.psp = patch('core.psp.async_post', new_callable=AsyncMock)
        self.async_post = self.psp.start()
        self.async_post.side_effect = psp_response
        self.addCleanup(self.psp.stop)
        # Mail microservice: tasks.send_email posts here, sent_code() reads it
        self.mail = patch('core.tasks.post')
        self.mail_post = self.mail.start()
        self.mail_post.return_value.json.return_value = {'success': True}
        self.addCleanup(self.mail.stop)

    def sent_code(self):
        """Verification code of the last email sent to the microservice."""
        data = self.mail_post.call_args.kwargs['json']
        self.assertEqual(data['action'], 'verification')
        return data['message']

    async def execute(self, query, variables=None, user=None):
        headers = {}
        if user:
            headers['authorization'] = utils.create_token(user.email)
        body = dumps({'query': query, 'variables': variables or {}})
        response = await self.async_client.post(
            URL, body, content_type='application/json', headers=headers
        )
        return loads(response.content)

    async def psp_return(self, data, token='test-ms-access-token'):
        return await self.async_client.post(
            PSP_RETURN_URL, dumps(data), content_type='application/json',
            headers={'X-Access-Token': token}
        )

    def error_code(self, result):
        return loads(result['errors'][0]['message'])['code']

    def ok(self, result, key):
        self.assertNotIn('errors', result, result)
        return result['data'][key]

    def psp_paths(self):
        return [call.args[0] for call in self.async_post.call_args_list]

    def transfer_data(self, **kwargs):
        pickup = timezone.now() + timedelta(days=2)
        data = {
            'pickupAddress': 'Istanbul Airport',
            'pickupLatitude': '41.275300', 'pickupLongitude': '28.751900',
            'dropoffAddress': 'Taksim Square',
            'dropoffLatitude': '41.036900', 'dropoffLongitude': '28.985000',
            'pickupDistrictName': 'Arnavutkoy',
            'dropoffDistrictName': 'Beyoglu',
            'pickupTime': pickup.isoformat(),
            'distance': '40.5', 'duration': 50,
            'passengerName': 'Ada Lovelace', 'passengerPhone': '+905551112233',
            'passengerCount': 2, 'luggageCount': 2,
        }
        data.update(kwargs)
        return data

    async def create_transfer(self, customer=None, **kwargs):
        result = await self.execute(
            CREATE_TRANSFER, {'data': self.transfer_data(**kwargs)},
            user=customer or self.customer
        )
        return self.ok(result, 'createTransfer')

    async def paid_transfer(self, category=None, refund_option=None, **kwargs):
        created = await self.create_transfer(**kwargs)
        transfer_id = created['transfer']['id']
        data = {
            'id': transfer_id,
            'vehicleCategoryId': str((category or self.sedan).id)
        }
        if refund_option:
            data['refundOption'] = refund_option
        result = await self.execute(PAY_TRANSFER, {'data': data}, self.customer)
        self.ok(result, 'payTransfer')
        response = await self.psp_return({
            'action': 'PAYMENT_RETURN', 'id': transfer_id, 'success': True,
            'order_id': 'order_1'
        })
        self.assertEqual(response.status_code, 200)
        return await models.Transfer.objects.aget(pk=transfer_id)

    async def assigned_transfer(self, driver=None, **kwargs):
        transfer = await self.paid_transfer(**kwargs)
        data = {'id': str(transfer.id), 'driverId': str((driver or self.driver).id)}
        result = await self.execute(ASSIGN_DRIVER, {'data': data}, self.operator)
        self.ok(result, 'assignDriver')
        return await models.Transfer.objects.aget(pk=transfer.id)


class UserTests(GraphQLTestCase):
    async def sign_up(self, email, mutation=CREATE_USER, **kwargs):
        data = {'email': email, 'language': 'EN', 'isUser': False}
        result = await self.execute(GET_CODE, {'data': data})
        secret = self.ok(result, 'getCode')['secret']
        code = self.sent_code()
        data = {'code': code, 'secret': secret}
        result = await self.execute(VALIDATE_CODE, {'data': data})
        secret = self.ok(result, 'validateCode')['secret']
        data = {
            'secret': secret, 'password': PASSWORD,
            'firstName': 'Ada', 'lastName': 'Lovelace',
            'phone': '+905551112233', **kwargs
        }
        return await self.execute(mutation, {'data': data})

    async def test_sign_up_role(self):
        result = await self.sign_up('new@example.com')
        user = self.ok(result, 'createCustomer')['user']
        self.assertEqual(user['role'], 'CUSTOMER')
        result = await self.sign_up('newdriver@example.com', CREATE_DRIVER)
        self.assertEqual(self.ok(result, 'createDriver')['user']['role'], 'DRIVER')

    async def test_phone_required(self):
        result = await self.sign_up('nophone@example.com', phone='  ')
        self.assertEqual(self.error_code(result), 'ERR043')
        result = await self.sign_up('phone@example.com', phone=' +905551 ')
        user = await User.objects.aget(email='phone@example.com')
        self.assertEqual(user.phone, '+905551')
        update = 'mutation($data: UpdateUser!) { updateUser(data: $data) { id } }'
        for phone in ('', None):
            data = {'phone': phone}
            result = await self.execute(update, {'data': data}, self.customer)
            self.assertEqual(self.error_code(result), 'ERR043')
        data = {'firstName': 'New'}
        result = await self.execute(update, {'data': data}, self.customer)
        self.ok(result, 'updateUser')

    async def test_create_driver_is_pending(self):
        data = {'email': 'd@example.com', 'language': 'TR', 'isUser': False}
        result = await self.execute(GET_CODE, {'data': data})
        secret = self.ok(result, 'getCode')['secret']
        code = self.sent_code()
        result = await self.execute(
            VALIDATE_CODE, {'data': {'code': code, 'secret': secret}}
        )
        secret = self.ok(result, 'validateCode')['secret']
        data = {'secret': secret, 'password': PASSWORD, 'firstName': 'D',
                'lastName': 'R', 'phone': '+905551112233'}
        result = await self.execute(CREATE_DRIVER, {'data': data})
        user = self.ok(result, 'createDriver')['user']
        self.assertEqual(user['role'], 'DRIVER')
        driver = await User.objects.aget(pk=user['id'])
        self.assertEqual((driver.status, driver.language), ('PENDING', 'tr'))

    async def test_create_admin_user(self):
        data = {'email': 'Op@Example.com', 'password': PASSWORD,
                'role': 'OPERATOR', 'firstName': 'O', 'lastName': 'P',
                'phone': '+905551112233'}
        result = await self.execute(
            CREATE_ADMIN_USER, {'data': data}, self.operator
        )
        self.assertEqual(self.error_code(result), 'ERR001')
        # Districts are required for OPERATOR
        result = await self.execute(CREATE_ADMIN_USER, {'data': data}, self.staff)
        self.assertEqual(self.error_code(result), 'ERR030')
        data['districtIds'] = ['0']
        result = await self.execute(CREATE_ADMIN_USER, {'data': data}, self.staff)
        self.assertEqual(self.error_code(result), 'ERR035')
        data['districtIds'] = [str(self.beyoglu.id)]
        result = await self.execute(CREATE_ADMIN_USER, {'data': data}, self.staff)
        created = self.ok(result, 'createAdminUser')
        self.assertEqual(created['status'], 'ACCEPTED')
        operator = await User.objects.aget(email='op@example.com')
        district_ids = [d.id async for d in operator.districts.all()]
        self.assertEqual(district_ids, [self.beyoglu.id])
        result = await self.execute(CREATE_ADMIN_USER, {'data': data}, self.staff)
        self.assertEqual(self.error_code(result), 'ERR011')
        data = {'email': 'a2@example.com', 'password': PASSWORD,
                'role': 'ADMIN', 'firstName': 'A', 'lastName': 'B',
                'phone': '+905551112233'}
        result = await self.execute(CREATE_ADMIN_USER, {'data': data}, self.staff)
        self.assertEqual(self.error_code(result), 'ERR001')
        result = await self.execute(CREATE_ADMIN_USER, {'data': data}, self.admin)
        self.assertEqual(self.ok(result, 'createAdminUser')['role'], 'ADMIN')

    async def test_status_visibility_and_update(self):
        await User.objects.filter(pk=self.driver.id).aupdate(status='PENDING')
        variables = {'id': str(self.driver.id)}
        result = await self.execute(ADMIN_USER, variables, self.operator)
        self.assertIsNone(self.ok(result, 'adminUser')['status'])
        result = await self.execute(ADMIN_USER, variables, self.staff)
        self.assertEqual(self.ok(result, 'adminUser')['status'], 'PENDING')
        data = {'id': str(self.driver.id), 'status': 'ACCEPTED'}
        result = await self.execute(UPDATE_ADMIN_USER, {'data': data}, self.staff)
        self.assertEqual(self.ok(result, 'updateAdminUser')['status'], 'ACCEPTED')
        # STAFF can only change the status
        data = {'id': str(self.driver.id), 'role': 'ADMIN'}
        result = await self.execute(UPDATE_ADMIN_USER, {'data': data}, self.staff)
        self.assertEqual(self.error_code(result), 'ERR001')
        data = {'id': str(self.driver.id), 'status': 'REJECTED'}
        result = await self.execute(
            UPDATE_ADMIN_USER, {'data': data}, self.operator
        )
        self.assertEqual(self.error_code(result), 'ERR001')
        result = await self.execute(UPDATE_ADMIN_USER, {'data': data}, self.admin)
        self.assertEqual(self.ok(result, 'updateAdminUser')['status'], 'REJECTED')

    async def test_sign_in(self):
        self.customer.set_password(PASSWORD)
        await self.customer.asave()
        data = {'email': 'Customer@example.com', 'password': PASSWORD}
        response = self.ok(await self.execute(SIGN_IN, {'data': data}), 'signIn')
        self.assertEqual(response['user']['email'], 'customer@example.com')

        wrong = {**data, 'password': 'wrong'}
        result = await self.execute(SIGN_IN, {'data': wrong})
        self.assertEqual(self.error_code(result), 'ERR059')
        # No password set (social sign in)
        social = {**data, 'email': 'other@example.com'}
        result = await self.execute(SIGN_IN, {'data': social})
        self.assertEqual(self.error_code(result), 'ERR059')

        unknown = {**data, 'email': 'unknown@example.com'}
        result = await self.execute(SIGN_IN, {'data': unknown})
        self.assertEqual(self.error_code(result), 'ERR010')
        # Inactive users get the same error whatever the password is
        self.customer.is_active = False
        await self.customer.asave()
        for inactive in (data, wrong):
            result = await self.execute(SIGN_IN, {'data': inactive})
            self.assertEqual(self.error_code(result), 'ERR010')

    async def test_sign_up_cannot_choose_other_roles(self):
        result = await self.sign_up('x@example.com', role='OPERATOR')
        self.assertIn('errors', result)
        self.assertFalse(await User.objects.filter(email='x@example.com').aexists())


class SocialSignInTests(GraphQLTestCase):
    """Tokens are signed with a local RSA key, the JWKS fetch is patched."""

    def setUp(self):
        super().setUp()
        from cryptography.hazmat.primitives.asymmetric import rsa
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwks = patch(
            'core.social.signing_key', return_value=self.key.public_key()
        )
        jwks.start()
        self.addCleanup(jwks.stop)

    def id_token(self, key=None, **claims):
        from jwt import encode
        now = timezone.now()
        payload = {
            'iss': 'https://accounts.google.com', 'aud': 'test-google-client-id',
            'sub': '1234567890', 'iat': now, 'exp': now + timedelta(hours=1),
            'nonce': 'n-1', 'email': 'Social@Example.com', 'email_verified': True,
            'given_name': 'Grace', 'family_name': 'Hopper', **claims
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return encode(payload, key or self.key, algorithm='RS256')

    async def sign_in(self, token, nonce='n-1', **data):
        data = {'provider': 'GOOGLE', 'idToken': token, 'nonce': nonce, **data}
        return await self.execute(SOCIAL_SIGN_IN, {'data': data})

    async def test_new_user_is_pending_customer(self):
        result = await self.sign_in(self.id_token())
        response = self.ok(result, 'socialSignIn')
        self.assertEqual(response['user'], {
            'email': 'social@example.com', 'role': 'CUSTOMER',
            'firstName': 'Grace', 'lastName': 'Hopper'
        })
        user = await User.objects.aget(email='social@example.com')
        self.assertEqual((user.status, user.phone), ('PENDING', ''))
        self.assertFalse(user.has_usable_password())
        # The returned token works like signIn's token
        result = await self.execute('query { user { email } }', user=user)
        self.assertTrue(response['token'])
        self.assertEqual(self.ok(result, 'user')['email'], 'social@example.com')
        # Second sign-in finds the same user
        result = await self.sign_in(self.id_token())
        self.ok(result, 'socialSignIn')
        self.assertEqual(
            await User.objects.filter(email='social@example.com').acount(), 1
        )

    async def test_existing_user_keeps_role_and_name(self):
        token = self.id_token(email='driver@example.com')
        result = await self.sign_in(token, firstName='Other')
        user = self.ok(result, 'socialSignIn')['user']
        self.assertEqual((user['role'], user['firstName']), ('DRIVER', 'driver'))

    async def test_name_from_input_overrides_token(self):
        token = self.id_token(given_name=None, family_name=None)
        result = await self.sign_in(token, firstName='Ada', lastName='Lovelace')
        user = self.ok(result, 'socialSignIn')['user']
        self.assertEqual((user['firstName'], user['lastName']), ('Ada', 'Lovelace'))

    async def test_inactive_user(self):
        self.customer.is_active = False
        await self.customer.asave()
        result = await self.sign_in(self.id_token(email='customer@example.com'))
        self.assertEqual(self.error_code(result), 'ERR010')

    async def test_accepts_pending_invitation(self):
        self.ok(await self.execute(
            INVITE_CUSTOMER, {'data': {'email': 'social@example.com'}}, self.operator
        ), 'inviteCustomer')
        self.ok(await self.sign_in(self.id_token()), 'socialSignIn')
        invitation = await models.Invitation.objects.aget()
        self.assertEqual(invitation.status, 'ACCEPTED')

    async def test_invalid_tokens(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        past = timezone.now() - timedelta(hours=2)
        invalid = [
            self.id_token(key=other_key),
            self.id_token(aud='someone-else'),
            self.id_token(iss='https://evil.example.com'),
            self.id_token(iat=past - timedelta(hours=1), exp=past),
            self.id_token(email_verified=False),
            self.id_token(email=None),
            'not-a-jwt',
        ]
        for token in invalid:
            with self.subTest(token=token[:20]):
                result = await self.sign_in(token)
                self.assertEqual(self.error_code(result), 'ERR045')
        result = await self.sign_in(self.id_token(), nonce='other')
        self.assertEqual(self.error_code(result), 'ERR045')
        self.assertFalse(
            await User.objects.filter(email='social@example.com').aexists()
        )

    def microsoft_token(self, tid=None, **claims):
        from core.social import MSA_TENANT
        tid = tid or MSA_TENANT
        defaults = {
            'iss': f'https://login.microsoftonline.com/{tid}/v2.0',
            'aud': 'test-microsoft-client-id', 'tid': tid, 'email_verified': None,
            'given_name': None, 'family_name': None, 'name': 'Alan Mathison Turing',
        }
        return self.id_token(**{**defaults, **claims})

    async def test_microsoft_personal_account(self):
        result = await self.sign_in(self.microsoft_token(), provider='MICROSOFT')
        user = self.ok(result, 'socialSignIn')['user']
        self.assertEqual(
            (user['email'], user['firstName'], user['lastName']),
            ('social@example.com', 'Alan', 'Mathison Turing')
        )

    async def test_microsoft_work_account_needs_verified_domain(self):
        tenant = '11111111-2222-3333-4444-555555555555'
        result = await self.sign_in(
            self.microsoft_token(tenant), provider='MICROSOFT'
        )
        self.assertEqual(self.error_code(result), 'ERR045')
        result = await self.sign_in(
            self.microsoft_token(tenant, xms_edov=True), provider='MICROSOFT'
        )
        self.ok(result, 'socialSignIn')

    async def test_microsoft_invalid_tokens(self):
        other = '11111111-2222-3333-4444-555555555555'
        invalid = [
            # Issuer of another tenant than the token's tid
            self.microsoft_token(
                iss=f'https://login.microsoftonline.com/{other}/v2.0'
            ),
            self.microsoft_token(tid=None, iss='https://accounts.google.com'),
            # Google audience is not accepted for Microsoft
            self.microsoft_token(aud='test-google-client-id'),
        ]
        for token in invalid:
            with self.subTest():
                result = await self.sign_in(token, provider='MICROSOFT')
                self.assertEqual(self.error_code(result), 'ERR045')
        # Google token with Microsoft as provider
        result = await self.sign_in(self.id_token(), provider='MICROSOFT')
        self.assertEqual(self.error_code(result), 'ERR045')

    async def test_unconfigured_provider(self):
        result = await self.sign_in(self.id_token(), provider='APPLE')
        self.assertEqual(self.error_code(result), 'ERR030')
        with override_settings(GOOGLE_CLIENT_IDS=[]):
            result = await self.sign_in(self.id_token())
        self.assertEqual(self.error_code(result), 'ERR030')


class InvitationTests(GraphQLTestCase):
    async def invite(self, user, **data):
        data = {'email': 'Guest@Example.com', **data}
        return await self.execute(INVITE_CUSTOMER, {'data': data}, user)

    def sent_invitation(self):
        data = self.mail_post.call_args.kwargs['json']
        self.assertEqual(data['action'], 'invitation')
        return data

    async def sign_up(self, secret, mutation=CREATE_USER):
        data = {
            'secret': secret, 'password': PASSWORD, 'firstName': 'Gu',
            'lastName': 'Est', 'phone': '+905551112233'
        }
        return await self.execute(mutation, {'data': data})

    async def test_permissions_and_validation(self):
        for user in (self.customer, self.driver):
            result = await self.invite(user)
            self.assertEqual(self.error_code(result), 'ERR001')
        result = await self.invite(self.admin, email='not-an-email')
        self.assertEqual(self.error_code(result), 'ERR030')
        result = await self.invite(self.admin, email=self.customer.email)
        self.assertEqual(self.error_code(result), 'ERR011')

    async def test_language_defaults_to_inviter(self):
        await User.objects.filter(pk=self.staff.id).aupdate(language='de')
        result = await self.invite(self.staff)
        invitation = self.ok(result, 'inviteCustomer')
        self.assertEqual(invitation['receiverEmail'], 'guest@example.com')
        self.assertEqual(invitation['language'], 'DE')
        self.assertEqual(invitation['status'], 'PENDING')
        mail = self.sent_invitation()
        self.assertEqual(mail['to_user']['language'], 'de')
        self.assertIn('/register?invitation=', mail['link'])
        result = await self.invite(self.staff, language='RU')
        self.assertEqual(self.ok(result, 'inviteCustomer')['language'], 'RU')
        # Inviting again renews the same PENDING invitation
        count = await models.Invitation.objects.acount()
        self.assertEqual(count, 1)

    async def test_sign_up_with_invitation(self):
        self.ok(await self.invite(self.operator, language='TR'), 'inviteCustomer')
        secret = self.sent_invitation()['message']
        result = await self.execute(INVITATION_INFO, {'secret': secret})
        info = self.ok(result, 'invitationInfo')
        self.assertEqual(info, {'email': 'guest@example.com', 'language': 'TR'})
        # Customer invitations cannot sign up drivers
        result = await self.sign_up(secret, CREATE_DRIVER)
        self.assertEqual(self.error_code(result), 'ERR001')
        result = await self.sign_up(secret)
        self.assertEqual(self.ok(result, 'createCustomer')['user']['role'], 'CUSTOMER')
        user = await User.objects.aget(email='guest@example.com')
        self.assertEqual((user.status, user.language), ('ACCEPTED', 'tr'))
        invitation = await models.Invitation.objects.aget()
        self.assertEqual(invitation.status, 'ACCEPTED')
        self.assertEqual(invitation.receiver_id, user.id)
        # Used once
        result = await self.execute(INVITATION_INFO, {'secret': secret})
        self.assertEqual(self.error_code(result), 'ERR044')
        result = await self.sign_up(secret)
        self.assertEqual(self.error_code(result), 'ERR044')

    async def test_operator_sees_own_invitations(self):
        self.ok(await self.invite(self.operator), 'inviteCustomer')
        self.ok(await self.invite(self.admin, email='b@example.com'), 'inviteCustomer')
        result = await self.execute(INVITATIONS, user=self.operator)
        self.assertEqual(self.ok(result, 'invitations')['count'], 1)
        result = await self.execute(INVITATIONS, user=self.staff)
        self.assertEqual(self.ok(result, 'invitations')['count'], 2)
        result = await self.execute(INVITATIONS, user=self.customer)
        self.assertEqual(self.error_code(result), 'ERR001')


class VehicleCategoryTests(GraphQLTestCase):
    async def test_public_list_is_translated_and_hides_inactive(self):
        result = await self.execute(VEHICLE_CATEGORIES, {'language': 'TR'})
        data = self.ok(result, 'vehicleCategories')
        names = [category['name'] for category in data['vehicleCategories']]
        # Accessible Van has no Turkish name: falls back to English
        self.assertEqual(names, ['Binek', 'Minibüs', 'Accessible Van'])
        result = await self.execute(VEHICLE_CATEGORIES, user=self.admin)
        self.assertEqual(self.ok(result, 'vehicleCategories')['count'], 4)

    async def test_only_admin_or_staff_can_write(self):
        data = {'nameEn': 'VIP', 'nameTr': 'VIP Araç', 'maxPassengers': 4,
                'maxLuggage': 2, 'perKmPrice': '20.00'}
        result = await self.execute(
            CREATE_VEHICLE_CATEGORY, {'data': data}, self.operator
        )
        self.assertEqual(self.error_code(result), 'ERR001')
        result = await self.execute(
            CREATE_VEHICLE_CATEGORY, {'data': data}, self.staff
        )
        created = self.ok(result, 'createVehicleCategory')
        self.assertEqual(created['nameTr'], 'VIP Araç')
        data['maxPassengers'] = 0
        result = await self.execute(
            CREATE_VEHICLE_CATEGORY, {'data': data}, self.staff
        )
        self.assertEqual(self.error_code(result), 'ERR030')


class ReasonTests(GraphQLTestCase):
    async def create_reason(self, user=None, **data):
        data = {
            'code': 'cancel_plans_changed',
            'category': 'CUSTOMER_CANCELLATION',
            'descriptionEn': 'Plans changed',
            **data
        }
        return await self.execute(
            CREATE_REASON, {'data': data}, user or self.staff
        )

    async def test_crud(self):
        result = await self.create_reason(descriptionTr='Planlar değişti')
        reason = self.ok(result, 'createReason')
        self.assertEqual(reason['description'], 'Plans changed')
        self.assertEqual(reason['code'], 'cancel_plans_changed')
        self.assertTrue(reason['isActive'])
        data = {
            'id': reason['id'], 'order': 2, 'descriptionEn': 'Changed',
            'code': 'cancel_changed', 'isActive': False
        }
        result = await self.execute(UPDATE_REASON, {'data': data}, self.admin)
        updated = self.ok(result, 'updateReason')
        self.assertEqual(
            (updated['order'], updated['code'], updated['isActive']),
            (2, 'cancel_changed', False)
        )
        result = await self.execute(DELETE_REASON, {'id': reason['id']}, self.admin)
        self.assertTrue(self.ok(result, 'deleteReason')['success'])
        result = await self.execute(UPDATE_REASON, {'data': data}, self.admin)
        self.assertEqual(self.error_code(result), 'ERR050')

    async def test_only_admin_and_staff_manage(self):
        result = await self.create_reason(self.operator)
        self.assertEqual(self.error_code(result), 'ERR001')

    async def test_invalid_description(self):
        result = await self.create_reason(descriptionEn=' ')
        self.assertEqual(self.error_code(result), 'ERR030')
        result = await self.create_reason(descriptionTr='x' * 201)
        self.assertEqual(self.error_code(result), 'ERR030')

    async def test_code_format_and_unique(self):
        for code in ['Bad Code', 'trailing_', 'x' * 51, '']:
            result = await self.create_reason(code=code)
            self.assertEqual(self.error_code(result), 'ERR030', code)
        self.ok(await self.create_reason(), 'createReason')
        result = await self.create_reason()
        self.assertEqual(self.error_code(result), 'ERR053')
        other = self.ok(
            await self.create_reason(code='cancel_other'), 'createReason'
        )
        data = {'id': other['id'], 'code': 'cancel_plans_changed'}
        result = await self.execute(UPDATE_REASON, {'data': data}, self.admin)
        self.assertEqual(self.error_code(result), 'ERR053')

    async def test_list_by_category_and_language(self):
        await self.create_reason(descriptionTr='Planlar değişti')
        await self.create_reason(
            code='operator_no_driver', category='OPERATOR_CANCELLATION',
            descriptionEn='No driver'
        )
        variables = {'category': 'CUSTOMER_CANCELLATION', 'language': 'TR'}
        result = await self.execute(REASONS, variables, self.customer)
        reasons = self.ok(result, 'reasons')
        self.assertEqual(
            [reason['description'] for reason in reasons], ['Planlar değişti']
        )
        # Missing language falls back to en
        variables = {'category': 'OPERATOR_CANCELLATION', 'language': 'DE'}
        result = await self.execute(REASONS, variables, self.customer)
        self.assertEqual(self.ok(result, 'reasons')[0]['description'], 'No driver')
        result = await self.execute(REASONS, {'code': 'operator_no_driver'}, self.customer)
        self.assertEqual(len(self.ok(result, 'reasons')), 1)
        result = await self.execute(REASONS)
        self.assertEqual(self.error_code(result), 'ERR001')

    async def test_inactive_only_for_staff(self):
        reason = self.ok(await self.create_reason(isActive=False), 'createReason')
        result = await self.execute(REASONS, {}, self.customer)
        self.assertEqual(self.ok(result, 'reasons'), [])
        result = await self.execute(REASONS, {}, self.staff)
        self.assertEqual(self.ok(result, 'reasons')[0]['id'], reason['id'])
        result = await self.execute(REASONS, {'isActive': True}, self.staff)
        self.assertEqual(self.ok(result, 'reasons'), [])


class RefundOptionTests(GraphQLTestCase):
    async def enable_protection(self, rate='0.10'):
        await models.State.objects.filter(pk=self.turkey.id).aupdate(
            allow_unconditional_refund=True, cancel_price_rate=Decimal(rate),
            # Standard refund would keep 20% within 72 hours
            customer_imposed_hours=[72], customer_imposed_rates=['0.20']
        )

    async def pay(self, refund_option):
        created = await self.create_transfer()
        data = {
            'id': created['transfer']['id'],
            'vehicleCategoryId': str(self.sedan.id),
            'refundOption': refund_option
        }
        result = await self.execute(PAY_TRANSFER, {'data': data}, self.customer)
        return created['transfer']['id'], result

    async def test_state_validation(self):
        # The fixture state has no psp, which update_state requires
        await models.State.objects.filter(pk=self.turkey.id).aupdate(psp='stripe')
        cases = [
            {'allowUnconditionalRefund': True},
            {'allowUnconditionalRefund': True, 'cancelPriceRate': '0'},
            {'cancelPriceRate': '1.5'},
            {'cancelPriceRate': '-0.1'},
        ]
        for case in cases:
            data = {'id': str(self.turkey.id), **case}
            result = await self.execute(UPDATE_STATE, {'data': data}, self.staff)
            self.assertEqual(self.error_code(result), 'ERR030', case)
        data = {
            'id': str(self.turkey.id), 'allowUnconditionalRefund': True,
            'cancelPriceRate': '0.15'
        }
        result = await self.execute(UPDATE_STATE, {'data': data}, self.admin)
        state = self.ok(result, 'updateState')
        self.assertTrue(state['allowUnconditionalRefund'])
        self.assertEqual(Decimal(state['cancelPriceRate']), Decimal('0.15'))
        result = await self.execute(UPDATE_STATE, {'data': data}, self.operator)
        self.assertEqual(self.error_code(result), 'ERR001')

    async def test_not_allowed(self):
        _, result = await self.pay('UNCONDITIONAL')
        self.assertEqual(self.error_code(result), 'ERR055')

    async def test_pay_with_protection(self):
        await self.enable_protection()
        transfer_id, result = await self.pay('UNCONDITIONAL')
        self.ok(result, 'payTransfer')
        transfer = await models.Transfer.objects.aget(pk=transfer_id)
        self.assertEqual(transfer.customer_price, Decimal('605.00'))
        self.assertEqual(transfer.cancel_price, Decimal('60.50'))
        self.assertEqual(transfer.price, Decimal('665.50'))
        _, payload = self.async_post.call_args.args
        self.assertEqual(payload['transaction']['order_amount'], Decimal('665.50'))
        self.assertEqual(payload['transfer']['price'], Decimal('665.50'))
        # Choosing again without it clears the fee
        data = {
            'id': transfer_id, 'vehicleCategoryId': str(self.sedan.id),
            'refundOption': 'STANDARD'
        }
        result = await self.execute(PAY_TRANSFER, {'data': data}, self.customer)
        self.ok(result, 'payTransfer')
        transfer = await models.Transfer.objects.aget(pk=transfer_id)
        self.assertEqual(transfer.cancel_price, Decimal('0.00'))
        self.assertEqual(transfer.price, Decimal('605.00'))

    async def refund_of(self, user, refund_option):
        await self.enable_protection()
        transfer = await self.paid_transfer(refund_option=refund_option)
        result = await self.execute(CANCEL_TRANSFER, {'id': str(transfer.id)}, user)
        self.assertEqual(self.ok(result, 'cancelTransfer')['status'], 'CANCELLED')
        refund = await models.Transaction.objects.aget(category='REFUND')
        return refund.order_amount

    async def test_customer_cancel_keeps_only_the_fee(self):
        refund = await self.refund_of(self.customer, 'UNCONDITIONAL')
        self.assertEqual(refund, Decimal('605.00'))

    async def test_customer_cancel_standard_keeps_penalty(self):
        refund = await self.refund_of(self.customer, 'STANDARD')
        self.assertEqual(refund, Decimal('484.00'))

    async def test_operator_cancel_refunds_the_fee_too(self):
        refund = await self.refund_of(self.operator, 'UNCONDITIONAL')
        self.assertEqual(refund, Decimal('665.50'))


QUOTE_TRANSFER = f'''
query($data: QuoteTransfer!, $language: Language) {{
  quoteTransfer(data: $data, language: $language) {{
    vehicleOptions {{ {OPTION_FIELDS} }}
    state {{
      id allowMultipleVehicles allowUnconditionalRefund cancelPriceRate
      customerImposedHours customerImposedRates psp driverBufferMinutes
    }}
  }}
}}
'''


class PassengerTests(GraphQLTestCase):
    """Passenger name and phone: the customer's unless given, required by payTransfer."""

    def pay_data(self, transfer_id):
        return {'id': transfer_id, 'vehicleCategoryId': str(self.sedan.id)}

    async def customer_passenger(self, user):
        user = await User.objects.aget(pk=user.id)
        return f'{user.first_name} {user.last_name}'.strip(), user.phone

    async def test_create_defaults_to_customer(self):
        for blank in [None, ' ']:
            data = self.transfer_data()
            if blank is None:
                del data['passengerName'], data['passengerPhone']
            else:
                data.update(passengerName=blank, passengerPhone=blank)
            result = await self.execute(CREATE_TRANSFER, {'data': data}, self.customer)
            id = self.ok(result, 'createTransfer')['transfer']['id']
            transfer = await models.Transfer.objects.aget(pk=id)
            self.assertEqual(
                (transfer.passenger_name, transfer.passenger_phone),
                await self.customer_passenger(self.customer)
            )

    async def test_create_trims_and_limits(self):
        created = await self.create_transfer(
            passengerName='  Ada  ', passengerPhone='  +905551112233 '
        )
        transfer = await models.Transfer.objects.aget(pk=created['transfer']['id'])
        self.assertEqual(transfer.passenger_name, 'Ada')
        self.assertEqual(transfer.passenger_phone, '+905551112233')
        for field, value in [
            ('passengerPhone', '+90555111223344556677'),
            ('passengerName', 'a' * 101),
        ]:
            data = self.transfer_data(**{field: value})
            result = await self.execute(CREATE_TRANSFER, {'data': data}, self.customer)
            self.assertEqual(self.error_code(result), 'ERR030', field)

    async def test_pay_fills_blank_passenger(self):
        # Saved without a passenger (before the default): the customer's
        created = await self.create_transfer()
        id = created['transfer']['id']
        await models.Transfer.objects.filter(pk=id).aupdate(
            passenger_name='', passenger_phone=''
        )
        self.ok(await self.execute(PAY_TRANSFER, {'data': self.pay_data(id)}, self.customer), 'payTransfer')
        transfer = await models.Transfer.objects.aget(pk=id)
        self.assertEqual(
            (transfer.passenger_name, transfer.passenger_phone),
            await self.customer_passenger(self.customer)
        )

    async def test_pay_requires_passenger(self):
        # Neither the transfer nor the customer has a phone
        created = await self.create_transfer()
        id = created['transfer']['id']
        await models.Transfer.objects.filter(pk=id).aupdate(passenger_phone='')
        await User.objects.filter(pk=self.customer.id).aupdate(phone='')
        result = await self.execute(PAY_TRANSFER, {'data': self.pay_data(id)}, self.customer)
        self.assertEqual(self.error_code(result), 'ERR057')

    async def test_update_cannot_clear_passenger(self):
        created = await self.create_transfer()
        id = created['transfer']['id']
        for field, code in [('passengerPhone', 'ERR057'), ('passengerName', 'ERR058')]:
            data = {'id': id, field: ' '}
            result = await self.execute(UPDATE_TRANSFER, {'data': data}, self.customer)
            self.assertEqual(self.error_code(result), code, field)

    async def test_update_other_fields_without_passenger(self):
        created = await self.create_transfer()
        id = created['transfer']['id']
        await models.Transfer.objects.filter(pk=id).aupdate(passenger_name='', passenger_phone='')
        data = {'id': id, 'note': 'Window seat'}
        self.ok(await self.execute(UPDATE_TRANSFER, {'data': data}, self.customer), 'updateTransfer')


class CreateSelectionTests(GraphQLTestCase):
    """createTransfer saves an optional vehicle choice without a payment link."""

    async def test_saves_choice_and_price(self):
        await models.State.objects.filter(pk=self.turkey.id).aupdate(
            allow_unconditional_refund=True, cancel_price_rate=Decimal('0.10')
        )
        with patch('core.psp.async_post') as post:
            created = await self.create_transfer(
                vehicleCategoryId=str(self.sedan.id), refundOption='UNCONDITIONAL'
            )
        post.assert_not_called()
        transfer = await models.Transfer.objects.aget(pk=created['transfer']['id'])
        sedan = await models.VehicleCategory.objects.aget(pk=self.sedan.id)
        price = sedan.get_price(transfer.distance, transfer.duration)
        self.assertEqual(transfer.status, 'PENDING')
        self.assertEqual(transfer.vehicle_category_id, self.sedan.id)
        self.assertEqual(transfer.vehicle_count, 1)
        self.assertEqual(transfer.customer_price, price)
        self.assertEqual(transfer.cancel_price, (price * Decimal('0.10')).quantize(Decimal('0.01')))
        self.assertFalse(transfer.psp_data)

    async def test_without_choice(self):
        created = await self.create_transfer()
        transfer = await models.Transfer.objects.aget(pk=created['transfer']['id'])
        self.assertIsNone(transfer.vehicle_category_id)
        self.assertIsNone(transfer.customer_price)

    async def test_invalid_choice_creates_nothing(self):
        cases = [
            ({'vehicleCategoryId': '999999'}, 'ERR025'),
            ({'vehicleCategoryId': str(self.sedan.id), 'vehicleCount': 2}, 'ERR047'),
            ({'vehicleCategoryId': str(self.sedan.id), 'refundOption': 'UNCONDITIONAL'}, 'ERR055'),
        ]
        for extra, code in cases:
            data = self.transfer_data(**extra)
            result = await self.execute(CREATE_TRANSFER, {'data': data}, self.customer)
            self.assertEqual(self.error_code(result), code, extra)
        self.assertEqual(await models.Transfer.objects.acount(), 0)

    async def test_update_saves_choice(self):
        created = await self.create_transfer(vehicleCategoryId=str(self.sedan.id))
        id = created['transfer']['id']
        await models.Transfer.objects.filter(pk=id).aupdate(
            psp_data={'psp': 'stripe', 'payment_link': 'https://pay/old'}
        )
        # Route change and a new choice in one update: priced for the new route
        data = {
            'id': id, 'distance': '60', 'vehicleCategoryId': str(self.sedan.id),
            'vehicleCount': 1, 'refundOption': 'STANDARD'
        }
        self.ok(await self.execute(UPDATE_TRANSFER, {'data': data}, self.customer), 'updateTransfer')
        transfer = await models.Transfer.objects.aget(pk=id)
        sedan = await models.VehicleCategory.objects.aget(pk=self.sedan.id)
        self.assertEqual(transfer.vehicle_category_id, self.sedan.id)
        self.assertEqual(transfer.customer_price, sedan.get_price(Decimal('60'), transfer.duration))
        paths = [call.args[0] for call in self.async_post.call_args_list]
        self.assertIn('/expirePaymentLink', paths)
        # Invalid choice changes nothing
        data = {'id': id, 'note': 'x', 'vehicleCategoryId': '999999'}
        result = await self.execute(UPDATE_TRANSFER, {'data': data}, self.customer)
        self.assertEqual(self.error_code(result), 'ERR025')
        transfer = await models.Transfer.objects.aget(pk=id)
        self.assertNotEqual(transfer.note, 'x')

    async def test_route_change_clears_choice(self):
        created = await self.create_transfer(vehicleCategoryId=str(self.sedan.id))
        id = created['transfer']['id']
        data = {'id': id, 'passengerCount': 3}
        self.ok(await self.execute(UPDATE_TRANSFER, {'data': data}, self.customer), 'updateTransfer')
        transfer = await models.Transfer.objects.aget(pk=id)
        self.assertIsNone(transfer.vehicle_category_id)


class QuoteTests(GraphQLTestCase):
    def setUp(self):
        super().setUp()
        # Rate limit counters live in the (locmem) cache across tests
        cache.clear()
        self.addCleanup(cache.clear)

    def quote_data(self, **kwargs):
        data = {
            'pickupDistrictName': 'Arnavutkoy', 'dropoffDistrictName': 'Beyoglu',
            'distance': '40.5', 'duration': 50,
            'passengerCount': 2, 'luggageCount': 2,
        }
        data.update(kwargs)
        return data

    async def quote(self, language=None, headers=None, **kwargs):
        variables = {'data': self.quote_data(**kwargs), 'language': language}
        body = dumps({'query': QUOTE_TRANSFER, 'variables': variables})
        response = await self.async_client.post(
            URL, body, content_type='application/json', headers=headers or {}
        )
        return loads(response.content)

    async def test_guest_quote_matches_create_transfer(self):
        quoted = self.ok(await self.quote(), 'quoteTransfer')
        self.assertEqual(await models.Transfer.objects.acount(), 0)
        created = await self.create_transfer()
        self.assertEqual(quoted['vehicleOptions'], created['vehicleOptions'])
        names = [o['vehicleCategory']['name'] for o in quoted['vehicleOptions']]
        self.assertEqual(names, ['Sedan', 'Van', 'Accessible Van'])
        self.assertEqual(quoted['vehicleOptions'][0]['price'], '605.00')

    async def test_language_and_state(self):
        await models.State.objects.filter(pk=self.turkey.id).aupdate(
            psp='stripe', allow_multiple_vehicles=True,
            allow_unconditional_refund=True, cancel_price_rate=Decimal('0.10'),
            customer_imposed_hours=[24], customer_imposed_rates=['0.50']
        )
        result = self.ok(
            await self.quote(language='TR', passengerCount=5), 'quoteTransfer'
        )
        sedan = result['vehicleOptions'][0]
        self.assertEqual(sedan['vehicleCategory']['name'], 'Binek')
        self.assertEqual(sedan['count'], 2)
        state = result['state']
        self.assertEqual(state['id'], str(self.turkey.id))
        self.assertTrue(state['allowMultipleVehicles'])
        self.assertTrue(state['allowUnconditionalRefund'])
        self.assertEqual(Decimal(state['cancelPriceRate']), Decimal('0.10'))
        self.assertEqual(state['customerImposedHours'], [24])
        # Staff-only state fields stay hidden from guests
        self.assertIsNone(state['psp'])
        self.assertIsNone(state['driverBufferMinutes'])

    async def test_stale_token_is_ignored(self):
        result = await self.quote(headers={'authorization': 'expired-or-bad'})
        self.ok(result, 'quoteTransfer')

    async def test_validation(self):
        past = (timezone.now() - timedelta(hours=1)).isoformat()
        cases = [
            ({'pickupDistrictName': 'Nowhere', 'dropoffDistrictName': 'None'}, 'ERR035'),
            ({'distance': '0'}, 'ERR030'),
            ({'distance': '2000.01'}, 'ERR030'),
            ({'duration': 2 * 24 * 60 + 1}, 'ERR030'),
            ({'passengerCount': 51}, 'ERR030'),
            ({'luggageCount': 101}, 'ERR030'),
            ({'childSeatCount': 21}, 'ERR030'),
            ({'pickupTime': past}, 'ERR016'),
        ]
        for case, code in cases:
            self.assertEqual(self.error_code(await self.quote(**case)), code, case)

    async def test_create_transfer_upper_bounds(self):
        data = self.transfer_data(distance='2500')
        result = await self.execute(CREATE_TRANSFER, {'data': data}, self.customer)
        self.assertEqual(self.error_code(result), 'ERR030')

    @override_settings(QUOTE_RATE_LIMIT=2, QUOTE_RATE_WINDOW=60)
    async def test_rate_limit(self):
        self.ok(await self.quote(), 'quoteTransfer')
        self.ok(await self.quote(), 'quoteTransfer')
        self.assertEqual(self.error_code(await self.quote()), 'ERR056')
        # Signing in doesn't lift it: the limit is per client IP
        body = dumps({
            'query': QUOTE_TRANSFER, 'variables': {'data': self.quote_data()}
        })
        headers = {'authorization': utils.create_token(self.customer.email)}
        response = await self.async_client.post(
            URL, body, content_type='application/json', headers=headers
        )
        self.assertEqual(self.error_code(loads(response.content)), 'ERR056')
        # Another client has its own counter
        cache.clear()
        self.ok(await self.quote(), 'quoteTransfer')

    @override_settings(QUOTE_RATE_LIMIT=2, QUOTE_RATE_WINDOW=60)
    async def test_rate_limit_counts_aliases(self):
        query = '''
        query($data: QuoteTransfer!) {
          a: quoteTransfer(data: $data) { state { id } }
          b: quoteTransfer(data: $data) { state { id } }
          c: quoteTransfer(data: $data) { state { id } }
        }
        '''
        body = dumps({'query': query, 'variables': {'data': self.quote_data()}})
        response = await self.async_client.post(
            URL, body, content_type='application/json'
        )
        result = loads(response.content)
        codes = [loads(e['message'])['code'] for e in result['errors']]
        self.assertEqual(codes, ['ERR056'])

    @override_settings(QUOTE_RATE_LIMIT=1)
    async def test_cache_outage_allows_quote(self):
        with patch('core.ratelimit.cache.aadd', side_effect=ConnectionError):
            self.ok(await self.quote(), 'quoteTransfer')
            self.ok(await self.quote(), 'quoteTransfer')


class DriverPriceTests(GraphQLTestCase):
    async def orm_transfer(self, status='ACCEPTED', **kwargs):
        return await models.Transfer.objects.acreate(
            customer=self.customer, operator=self.operator, status=status,
            vehicle_category=self.sedan, state=self.turkey,
            pickup_time=timezone.now() + timedelta(days=1),
            duration=30, distance='10', passenger_name='X',
            pickup_address='A', pickup_latitude=0, pickup_longitude=0,
            dropoff_address='B', dropoff_latitude=0, dropoff_longitude=0,
            **kwargs
        )

    async def test_assign_driver_sets_driver_fields(self):
        transfer = await self.orm_transfer()
        data = {'id': str(transfer.id), 'driverId': str(self.driver.id),
                'driverName': 'Ignored', 'driverPhone': '555',
                'vehicleBrand': 'Ignored', 'driverPrice': '150.5',
                'driverPriceCurrency': 'EUR'}
        result = await self.execute(
            ASSIGN_DRIVER_WITH_PRICE, {'data': data}, self.operator
        )
        [assigned] = self.ok(result, 'assignDriver')['drivers']
        # An internal driver's name, plate and vehicle come from the user and
        # the vehicle, the category is the transfer's
        self.assertEqual(assigned, {
            'id': assigned['id'], 'driverId': str(self.driver.id),
            'vehicleId': str(self.driver_vehicle.id),
            'vehicleCategoryId': str(transfer.vehicle_category_id),
            'isExternal': False, 'driverName': 'driver Test',
            'driverPhone': '', 'plate': '34ABC01', 'vehicleBrand': 'Toyota',
            'vehicleModel': 'Corolla', 'driverPrice': '150.50',
            'driverPriceCurrency': 'EUR'
        })
        # A negative price is saved as zero
        data = {'id': assigned['id'], 'driverPrice': '-1'}
        result = await self.execute(
            UPDATE_TRANSFER_DRIVER, {'data': data}, self.operator
        )
        updated = self.ok(result, 'updateTransferDriver')
        self.assertEqual(updated['driverPrice'], '0.00')

    async def test_assign_external_driver(self):
        transfer = await self.orm_transfer()
        data = {'id': str(transfer.id), 'driverPhone': '555'}
        result = await self.execute(
            ASSIGN_DRIVER_WITH_PRICE, {'data': data}, self.operator
        )
        # An external driver needs a name, and has no vehicle of ours
        self.assertEqual(self.error_code(result), 'ERR030')
        data.update(driverName=' Ali ', vehicleId=str(self.driver_vehicle.id))
        result = await self.execute(
            ASSIGN_DRIVER_WITH_PRICE, {'data': data}, self.operator
        )
        self.assertEqual(self.error_code(result), 'ERR030')
        del data['vehicleId']
        data.update(plate='34XYZ99', driverPrice='100', vehicleModel='x' * 51)
        result = await self.execute(
            ASSIGN_DRIVER_WITH_PRICE, {'data': data}, self.operator
        )
        self.assertEqual(self.error_code(result), 'ERR030')
        data.update(vehicleBrand=' Mercedes ', vehicleModel='Vito')
        result = await self.execute(
            ASSIGN_DRIVER_WITH_PRICE, {'data': data}, self.operator
        )
        [assigned] = self.ok(result, 'assignDriver')['drivers']
        self.assertEqual(assigned, {
            'id': assigned['id'], 'driverId': None, 'vehicleId': None,
            'vehicleCategoryId': str(transfer.vehicle_category_id),
            'isExternal': True, 'driverName': 'Ali', 'driverPhone': '555',
            'plate': '34XYZ99', 'vehicleBrand': 'Mercedes',
            'vehicleModel': 'Vito', 'driverPrice': '100.00',
            'driverPriceCurrency': None
        })
        # The status does not change with the assignment
        transfer = await models.Transfer.objects.aget(pk=transfer.pk)
        self.assertEqual(transfer.status, 'ACCEPTED')
        # vehicleCount is 1: no more drivers
        data = {'id': str(transfer.id), 'driverId': str(self.driver.id)}
        result = await self.execute(ASSIGN_DRIVER, {'data': data}, self.operator)
        self.assertEqual(self.error_code(result), 'ERR048')

    async def test_update_transfer_driver(self):
        transfer = await self.orm_transfer()
        external = await models.TransferDriver.objects.acreate(
            transfer=transfer, driver_name='Ali'
        )
        internal = await models.TransferDriver.objects.acreate(
            transfer=transfer, driver=self.driver, driver_name='driver Test'
        )
        data = {'id': str(external.id), 'driverName': 'Veli',
                'driverPhone': '555', 'plate': '06AB12', 'driverPrice': '100',
                'driverPriceCurrency': 'TRY'}
        result = await self.execute(
            UPDATE_TRANSFER_DRIVER, {'data': data}, self.customer
        )
        self.assertEqual(self.error_code(result), 'ERR001')
        result = await self.execute(
            UPDATE_TRANSFER_DRIVER, {'data': data}, self.other_operator
        )
        self.assertEqual(self.error_code(result), 'ERR049')
        result = await self.execute(
            UPDATE_TRANSFER_DRIVER, {'data': data}, self.operator
        )
        updated = self.ok(result, 'updateTransferDriver')
        self.assertEqual(
            (updated['driverName'], updated['driverPhone'], updated['plate'],
             updated['driverPrice'], updated['driverPriceCurrency']),
            ('Veli', '555', '06AB12', '100.00', 'TRY')
        )
        # An internal driver's name is kept, the price changes
        data['id'] = str(internal.id)
        data['driverPrice'] = '120'
        result = await self.execute(
            UPDATE_TRANSFER_DRIVER, {'data': data}, self.admin
        )
        updated = self.ok(result, 'updateTransferDriver')
        self.assertEqual(
            (updated['driverName'], updated['driverPhone'],
             updated['driverPrice']),
            ('driver Test', '', '120.00')
        )
        # Still editable while STARTED, not after
        for status, code in (('STARTED', None), ('COMPLETED', 'ERR014')):
            await models.Transfer.objects.filter(pk=transfer.pk).aupdate(
                status=status
            )
            result = await self.execute(
                UPDATE_TRANSFER_DRIVER, {'data': data}, self.admin
            )
            if code:
                self.assertEqual(self.error_code(result), code)
            else:
                self.ok(result, 'updateTransferDriver')

    async def test_driver_price_hidden_from_customer(self):
        transfer = await self.orm_transfer()
        await models.TransferDriver.objects.acreate(
            transfer=transfer, driver_name='Ali', driver_phone='555',
            plate='34XYZ99', driver_price='100', driver_price_currency='TRY'
        )
        variables = {'id': str(transfer.id)}
        result = await self.execute(TRANSFER_DRIVER, variables, self.customer)
        # The customer sees the name and plate, never the price and phone
        [driver] = self.ok(result, 'transfer')['drivers']
        self.assertEqual(
            (driver['driverName'], driver['plate'], driver['driverPhone'],
             driver['driverPrice'], driver['driverPriceCurrency']),
            ('Ali', '34XYZ99', None, None, None)
        )
        result = await self.execute(TRANSFER_DRIVER, variables, self.operator)
        [driver] = self.ok(result, 'transfer')['drivers']
        self.assertEqual(
            (driver['driverPhone'], driver['driverPrice']), ('555', '100.00')
        )


class PayoutTests(GraphQLTestCase):
    async def create_payment(self, operator):
        transfer = await models.Transfer.objects.acreate(
            customer=self.customer, operator=operator, status='ACCEPTED',
            vehicle_category=self.sedan, state=self.turkey,
            pickup_time=timezone.now() + timedelta(days=1),
            duration=30, distance='10', passenger_name='X',
            pickup_address='A', pickup_latitude=0, pickup_longitude=0,
            dropoff_address='B', dropoff_latitude=0, dropoff_longitude=0
        )
        return await models.Transaction.objects.acreate(
            transfer=transfer, user=self.customer, category='PAYMENT',
            order_amount='100', currency='TRY', order_time=timezone.now()
        )

    async def test_update_transaction(self):
        payment = await self.create_payment(self.operator)
        data = {'id': str(payment.id), 'note': 'Paid', 'isPayoutSent': True}
        for user in (self.customer, self.driver):
            result = await self.execute(UPDATE_TRANSACTION, {'data': data}, user)
            self.assertEqual(self.error_code(result), 'ERR001')
        result = await self.execute(
            UPDATE_TRANSACTION, {'data': data}, self.other_operator
        )
        self.assertEqual(self.error_code(result), 'ERR034')
        result = await self.execute(
            UPDATE_TRANSACTION, {'data': data}, self.operator
        )
        self.assertEqual(
            self.ok(result, 'updateTransaction'),
            {'id': str(payment.id), 'note': 'Paid', 'isPayoutSent': True}
        )
        data = {'id': str(payment.id), 'isPayoutSent': False}
        result = await self.execute(UPDATE_TRANSACTION, {'data': data}, self.staff)
        updated = self.ok(result, 'updateTransaction')
        self.assertEqual((updated['note'], updated['isPayoutSent']),
                         ('Paid', False))

    async def test_is_payout_sent_visibility(self):
        await self.create_payment(self.operator)
        result = await self.execute(PAYOUT_TRANSACTIONS, user=self.customer)
        rows = self.ok(result, 'transactions')['transactions']
        self.assertEqual([row['isPayoutSent'] for row in rows], [None])
        for user in (self.operator, self.admin):
            result = await self.execute(PAYOUT_TRANSACTIONS, user=user)
            rows = self.ok(result, 'transactions')['transactions']
            self.assertEqual([row['isPayoutSent'] for row in rows], [False])
        result = await self.execute(PAYOUT_TRANSACTIONS, user=self.other_operator)
        self.assertEqual(self.ok(result, 'transactions')['count'], 0)

    async def test_transfer_transactions_scoped_by_role(self):
        payment = await self.create_payment(self.operator)
        transfer = await models.Transfer.objects.aget(pk=payment.transfer_id)
        await models.TransferDriver.objects.acreate(
            transfer=transfer, driver=self.driver
        )
        tip = await models.Transaction.objects.acreate(
            transfer=transfer, user=self.customer, category='TIP',
            driver=self.driver, order_amount='10', currency='TRY',
            order_time=timezone.now()
        )
        query = '''
        query($id: ID!) { transfer(id: $id) { transactions { id } } }
        '''
        variables = {'id': str(transfer.id)}
        expected = {
            self.customer: {payment.id, tip.id},
            self.driver: {tip.id},
            self.operator: {payment.id, tip.id},
            self.admin: {payment.id, tip.id},
        }
        for user, ids in expected.items():
            result = await self.execute(query, variables, user)
            rows = self.ok(result, 'transfer')['transactions']
            self.assertEqual({int(row['id']) for row in rows}, ids, user.role)

    async def test_filter_by_is_payout_sent(self):
        payment = await self.create_payment(self.operator)
        query = '''
        query($sent: Boolean) {
          transactions(filters: {isPayoutSent: $sent}) { count }
        }
        '''
        result = await self.execute(query, {'sent': False}, self.customer)
        self.assertEqual(self.error_code(result), 'ERR001')
        result = await self.execute(query, {'sent': True}, self.operator)
        self.assertEqual(self.ok(result, 'transactions')['count'], 0)
        payment.is_payout_sent = True
        await payment.asave()
        result = await self.execute(query, {'sent': True}, self.admin)
        self.assertEqual(self.ok(result, 'transactions')['count'], 1)


class LocationTests(GraphQLTestCase):
    async def test_public_queries(self):
        result = await self.execute(CITIES)
        data = self.ok(result, 'cities')
        self.assertEqual(
            [city['name'] for city in data['cities']], ['Athens', 'Istanbul']
        )
        # State's payment provider is not public
        self.assertIsNone(data['cities'][0]['state']['psp'])
        await models.State.objects.filter(pk=self.greece.id).aupdate(psp='x')
        result = await self.execute(CITIES, user=self.admin)
        cities = self.ok(result, 'cities')['cities']
        self.assertEqual(cities[0]['state']['psp'], 'x')
        filters = {'stateId': {'exact': str(self.turkey.id)}}
        result = await self.execute(CITIES, {'filters': filters})
        self.assertEqual(self.ok(result, 'cities')['count'], 1)
        result = await self.execute(DISTRICTS)
        names = [d['name'] for d in self.ok(result, 'districts')['districts']]
        self.assertEqual(names, ['Arnavutkoy', 'Beyoglu', 'Glyfada'])
        result = await self.execute(DISTRICTS, user=self.staff)
        self.assertEqual(self.ok(result, 'districts')['count'], 4)
        filters = {'stateId': str(self.greece.id)}
        result = await self.execute(DISTRICTS, {'filters': filters})
        names = [d['name'] for d in self.ok(result, 'districts')['districts']]
        self.assertEqual(names, ['Glyfada'])

    async def test_city_crud(self):
        data = {'stateId': str(self.turkey.id), 'name': 'Ankara'}
        result = await self.execute(CREATE_CITY, {'data': data})
        self.assertEqual(self.error_code(result), 'ERR001')
        result = await self.execute(CREATE_CITY, {'data': data}, self.operator)
        self.assertEqual(self.error_code(result), 'ERR001')
        result = await self.execute(CREATE_CITY, {'data': data}, self.staff)
        city = self.ok(result, 'createCity')
        data = {'id': city['id'], 'stateId': '0'}
        result = await self.execute(UPDATE_CITY, {'data': data}, self.admin)
        self.assertEqual(self.error_code(result), 'ERR036')
        data = {'id': city['id'], 'name': 'Izmir'}
        result = await self.execute(UPDATE_CITY, {'data': data}, self.admin)
        self.assertEqual(self.ok(result, 'updateCity')['name'], 'Izmir')
        result = await self.execute(DELETE_CITY, {'id': city['id']}, self.admin)
        self.assertTrue(self.ok(result, 'deleteCity')['success'])
        result = await self.execute(DELETE_CITY, {'id': city['id']}, self.admin)
        self.assertEqual(self.error_code(result), 'ERR039')

    async def test_district_crud(self):
        data = {'cityId': '0', 'name': 'Kadikoy'}
        result = await self.execute(CREATE_DISTRICT, {'data': data}, self.staff)
        self.assertEqual(self.error_code(result), 'ERR039')
        data['cityId'] = str(self.beyoglu.city_id)
        result = await self.execute(
            CREATE_DISTRICT, {'data': data}, self.driver
        )
        self.assertEqual(self.error_code(result), 'ERR001')
        result = await self.execute(CREATE_DISTRICT, {'data': data}, self.staff)
        district = self.ok(result, 'createDistrict')
        self.assertTrue(district['isActive'])
        data = {'id': district['id'], 'isActive': False, 'name': ' '}
        result = await self.execute(UPDATE_DISTRICT, {'data': data}, self.admin)
        self.assertEqual(self.error_code(result), 'ERR030')
        data['name'] = 'Kadıköy'
        result = await self.execute(UPDATE_DISTRICT, {'data': data}, self.admin)
        self.assertFalse(self.ok(result, 'updateDistrict')['isActive'])
        await models.Transfer.objects.acreate(
            customer=self.customer, state=self.turkey,
            pickup_district=self.beyoglu, vehicle_category=self.sedan,
            pickup_time=timezone.now() + timedelta(days=1),
            duration=30, distance='10', passenger_name='X',
            pickup_address='A', pickup_latitude=0, pickup_longitude=0,
            dropoff_address='B', dropoff_latitude=0, dropoff_longitude=0
        )
        result = await self.execute(
            DELETE_DISTRICT, {'id': self.beyoglu.id}, self.admin
        )
        self.assertEqual(self.error_code(result), 'ERR041')
        result = await self.execute(
            DELETE_CITY, {'id': self.beyoglu.city_id}, self.admin
        )
        self.assertEqual(self.error_code(result), 'ERR040')
        result = await self.execute(
            DELETE_DISTRICT, {'id': district['id']}, self.admin
        )
        self.assertTrue(self.ok(result, 'deleteDistrict')['success'])


class StateTests(GraphQLTestCase):
    async def test_states_query_permissions(self):
        result = await self.execute(STATES, user=self.customer)
        self.assertEqual(self.error_code(result), 'ERR001')
        result = await self.execute(STATES, user=self.operator)
        data = self.ok(result, 'states')
        self.assertEqual(data['count'], 2)
        self.assertEqual(
            [state['code'] for state in data['states']], ['GR', 'TR']
        )

    async def test_create_update_state(self):
        data = {'name': 'Cyprus', 'code': 'CY', 'psp': 'iyzico',
                'customerImposedHours': [2, 24],
                'customerImposedRates': ['1', '0.5']}
        result = await self.execute(CREATE_STATE, {'data': data}, self.operator)
        self.assertEqual(self.error_code(result), 'ERR001')
        result = await self.execute(CREATE_STATE, {'data': data}, self.staff)
        created = self.ok(result, 'createState')
        self.assertEqual(created['customerImposedRates'], ['1', '0.5'])
        self.assertEqual(created['driverBufferMinutes'], 30)
        self.assertFalse(created['allowMultipleVehicles'])
        result = await self.execute(CREATE_STATE, {'data': data}, self.staff)
        self.assertEqual(self.error_code(result), 'ERR038')
        # Hours not ascending, rate out of range, length mismatch
        for hours, rates in (([24, 2], ['1', '0.5']), ([2], ['1.5']),
                             ([2, 24], ['1'])):
            update = {'id': created['id'], 'customerImposedHours': hours,
                      'customerImposedRates': rates}
            result = await self.execute(
                UPDATE_STATE, {'data': update}, self.admin
            )
            self.assertEqual(self.error_code(result), 'ERR030')
        update = {'id': created['id'], 'driverBufferMinutes': 45,
                  'allowMultipleVehicles': True}
        result = await self.execute(UPDATE_STATE, {'data': update}, self.admin)
        updated = self.ok(result, 'updateState')
        self.assertEqual(updated['driverBufferMinutes'], 45)
        self.assertTrue(updated['allowMultipleVehicles'])
        self.assertEqual(updated['customerImposedHours'], [2, 24])
        update = {'id': '0', 'name': 'X'}
        result = await self.execute(UPDATE_STATE, {'data': update}, self.admin)
        self.assertEqual(self.error_code(result), 'ERR036')

    async def test_delete_state(self):
        await models.Transfer.objects.acreate(
            customer=self.customer, state=self.turkey,
            vehicle_category=self.sedan,
            pickup_time=timezone.now() + timedelta(days=1),
            duration=30, distance='10', passenger_name='X',
            pickup_address='A', pickup_latitude=0, pickup_longitude=0,
            dropoff_address='B', dropoff_latitude=0, dropoff_longitude=0
        )
        result = await self.execute(
            DELETE_STATE, {'id': self.turkey.id}, self.admin
        )
        self.assertEqual(self.error_code(result), 'ERR037')
        result = await self.execute(
            DELETE_STATE, {'id': self.greece.id}, self.admin
        )
        self.assertTrue(self.ok(result, 'deleteState')['success'])
        self.assertFalse(
            await models.District.objects.filter(name='Glyfada').aexists()
        )


class VehicleTests(GraphQLTestCase):
    async def test_create_vehicle_rules(self):
        data = {'driverId': str(self.customer.id), 'categoryId': str(self.van.id),
                'plate': '34 xyz 99', 'brand': 'Mercedes', 'model': 'Vito'}
        result = await self.execute(CREATE_VEHICLE, {'data': data}, self.operator)
        self.assertEqual(self.error_code(result), 'ERR015')
        data['driverId'] = str(self.driver.id)
        result = await self.execute(CREATE_VEHICLE, {'data': data}, self.operator)
        self.assertEqual(self.ok(result, 'createVehicle')['plate'], '34 XYZ 99')
        result = await self.execute(CREATE_VEHICLE, {'data': data}, self.operator)
        self.assertEqual(self.error_code(result), 'ERR028')
        # Driver adds own vehicle, a given driverId is ignored
        data = {'driverId': str(self.other_driver.id), 'categoryId': str(self.van.id),
                'plate': '34 DRV 01', 'brand': 'Ford', 'model': 'Tourneo'}
        result = await self.execute(CREATE_VEHICLE, {'data': data}, self.driver)
        created = self.ok(result, 'createVehicle')
        self.assertEqual(created['driver']['id'], str(self.driver.id))
        # Others must give driverId
        del data['driverId']
        data['plate'] = '34 DRV 02'
        result = await self.execute(CREATE_VEHICLE, {'data': data}, self.operator)
        self.assertEqual(self.error_code(result), 'ERR030')
        result = await self.execute(CREATE_VEHICLE, {'data': data}, self.customer)
        self.assertEqual(self.error_code(result), 'ERR001')


class TransferTests(GraphQLTestCase):
    async def allow_multiple_vehicles(self):
        states = models.State.objects.filter(pk=self.turkey.pk)
        await states.aupdate(allow_multiple_vehicles=True)

    async def test_create_transfer_requires_customer_phone(self):
        await User.objects.filter(pk=self.customer.pk).aupdate(phone='')
        self.customer.phone = ''
        variables = {'data': self.transfer_data()}
        result = await self.execute(CREATE_TRANSFER, variables, self.customer)
        self.assertEqual(self.error_code(result), 'ERR043')
        # Created for the customer by an operator: the customer's phone counts
        data = self.transfer_data(customerId=str(self.customer.id))
        result = await self.execute(CREATE_TRANSFER, {'data': data}, self.operator)
        self.assertEqual(self.error_code(result), 'ERR043')
        self.assertFalse(await models.Transfer.objects.aexists())

    async def test_create_transfer_returns_priced_vehicle_options(self):
        created = await self.create_transfer()
        self.assertEqual(created['transfer']['status'], 'PENDING')
        self.assertIsNone(created['transfer']['vehicleCategoryId'])
        options = {
            option['vehicleCategory']['name']: option
            for option in created['vehicleOptions']
        }
        # Inactive Limousine is never offered
        self.assertEqual(set(options), {'Sedan', 'Van', 'Accessible Van'})
        sedan = options['Sedan']
        # 100 + 40.5 * 10 + 50 * 2 = 605
        self.assertEqual(Decimal(sedan['price']), Decimal('605.00'))
        self.assertEqual(Decimal(sedan['distance']), Decimal('40.50'))
        self.assertEqual(sedan['duration'], 50)
        self.assertEqual(sedan['currency'], 'TRY')
        # 200 + 40.5 * 15 + 50 * 3 = 957.5
        self.assertEqual(Decimal(options['Van']['price']), Decimal('957.50'))

    async def test_vehicle_options_follow_transfer_needs(self):
        # Single vehicle (default): only categories that fit in one vehicle
        created = await self.create_transfer(passengerCount=5)
        options = [
            (o['vehicleCategory']['name'], o['count'])
            for o in created['vehicleOptions']
        ]
        self.assertEqual(options, [('Van', 1)])
        await self.allow_multiple_vehicles()
        created = await self.create_transfer(passengerCount=5)
        options = [
            (o['vehicleCategory']['name'], o['count'])
            for o in created['vehicleOptions']
        ]
        self.assertEqual(options, [('Sedan', 2), ('Van', 1), ('Accessible Van', 2)])
        created = await self.create_transfer(passengerCount=2, luggageCount=10)
        options = [
            (o['vehicleCategory']['name'], o['count'])
            for o in created['vehicleOptions']
        ]
        self.assertEqual(options, [('Sedan', 4), ('Van', 2), ('Accessible Van', 3)])
        created = await self.create_transfer(needsWheelchair=True)
        names = [o['vehicleCategory']['name'] for o in created['vehicleOptions']]
        self.assertEqual(names, ['Accessible Van'])
        created = await self.create_transfer(childSeatCount=1)
        names = [o['vehicleCategory']['name'] for o in created['vehicleOptions']]
        self.assertEqual(names, ['Van'])

    async def test_minimum_fare(self):
        created = await self.create_transfer(distance='5', duration=10)
        sedan = created['vehicleOptions'][0]
        self.assertEqual(Decimal(sedan['price']), Decimal('300.00'))

    async def test_vehicle_options_query_and_scope(self):
        created = await self.create_transfer()
        variables = {'transferId': created['transfer']['id']}
        result = await self.execute(VEHICLE_OPTIONS, variables, self.customer)
        self.assertEqual(len(self.ok(result, 'vehicleOptions')), 3)
        result = await self.execute(VEHICLE_OPTIONS, variables, self.other_customer)
        self.assertEqual(self.error_code(result), 'ERR013')

    async def test_create_transfer_rules(self):
        past = (timezone.now() - timedelta(minutes=1)).isoformat()
        cases = [
            ({'pickupTime': past}, 'ERR016'),
            ({'distance': '0'}, 'ERR030'),
            ({'duration': 0}, 'ERR030'),
            ({'passengerCount': 0}, 'ERR030'),
        ]
        for kwargs, code in cases:
            result = await self.execute(
                CREATE_TRANSFER, {'data': self.transfer_data(**kwargs)},
                user=self.customer
            )
            self.assertEqual(self.error_code(result), code, kwargs)
        result = await self.execute(
            CREATE_TRANSFER, {'data': self.transfer_data()}, user=self.driver
        )
        self.assertEqual(self.error_code(result), 'ERR001')

    async def test_create_transfer_saves_legs(self):
        start = timezone.now() + timedelta(days=2)

        def leg(address, hours, **kwargs):
            return {
                'startLatitude': '41.275300', 'startLongitude': '28.751900',
                'startAddress': address,
                'startTime': (start + timedelta(hours=hours)).isoformat(),
                'endLatitude': '41.036900', 'endLongitude': '28.985000',
                'endAddress': f'{address} end',
                'endTime': (start + timedelta(hours=hours + 1)).isoformat(),
                **kwargs
            }

        legs = [leg('First', 0, route='encoded'), leg('Second', 2)]
        query = CREATE_TRANSFER.replace(
            'transfer {', 'transfer { legs { order startAddress route }', 1
        )
        result = await self.execute(
            query, {'data': self.transfer_data(legs=legs)}, self.customer
        )
        transfer = self.ok(result, 'createTransfer')['transfer']
        self.assertEqual(transfer['legs'], [
            {'order': 0, 'startAddress': 'First', 'route': 'encoded'},
            {'order': 1, 'startAddress': 'Second', 'route': ''},
        ])
        created = await self.create_transfer()
        self.assertFalse(
            await models.Leg.objects.filter(
                transfer_id=created['transfer']['id']
            ).aexists()
        )
        invalid = [leg('Bad', 0, endTime=start.isoformat())]
        result = await self.execute(
            CREATE_TRANSFER, {'data': self.transfer_data(legs=invalid)},
            self.customer
        )
        self.assertEqual(self.error_code(result), 'ERR030')
        self.assertEqual(await models.Transfer.objects.acount(), 2)

    async def test_update_transfer_replaces_legs(self):
        start = timezone.now() + timedelta(days=2)

        def leg(address, hours):
            return {
                'startLatitude': '41.275300', 'startLongitude': '28.751900',
                'startAddress': f'{address} start',
                'startTime': (start + timedelta(hours=hours)).isoformat(),
                'endLatitude': '41.036900', 'endLongitude': '28.985000',
                'endAddress': address,
                'endTime': (start + timedelta(hours=hours + 1)).isoformat(),
            }

        created = await self.create_transfer(legs=[leg('Old', 0), leg('Taksim', 1)])
        id = created['transfer']['id']
        # A chosen vehicle is cleared by a route change
        await models.Transfer.objects.filter(pk=id).aupdate(
            vehicle_category=self.sedan, customer_price='500.00', currency='TRY'
        )
        query = UPDATE_TRANSFER.replace(
            'transfer {', 'transfer { legs { order endAddress }', 1
        )
        legs = [leg('A', 0), leg('B', 1), leg('Taksim', 2)]
        data = {'id': id, 'legs': legs, 'distance': '45.0', 'duration': 60}
        result = await self.execute(query, {'data': data}, self.customer)
        transfer = self.ok(result, 'updateTransfer')['transfer']
        self.assertEqual(transfer['legs'], [
            {'order': 0, 'endAddress': 'A'},
            {'order': 1, 'endAddress': 'B'},
            {'order': 2, 'endAddress': 'Taksim'},
        ])
        saved = await models.Transfer.objects.aget(pk=id)
        self.assertIsNone(saved.vehicle_category_id)
        self.assertIsNone(saved.price)
        # Not given: legs are kept
        data = {'id': id, 'note': 'Call on arrival'}
        result = await self.execute(query, {'data': data}, self.customer)
        self.assertEqual(len(self.ok(result, 'updateTransfer')['transfer']['legs']), 3)
        # Invalid leg: nothing changes
        bad = {**leg('Bad', 0), 'endTime': start.isoformat()}
        result = await self.execute(
            query, {'data': {'id': id, 'legs': [bad]}}, self.customer
        )
        self.assertEqual(self.error_code(result), 'ERR030')
        self.assertEqual(
            await models.Leg.objects.filter(transfer_id=id).acount(), 3
        )
        # [] removes all stops
        result = await self.execute(
            query, {'data': {'id': id, 'legs': []}}, self.customer
        )
        self.assertEqual(self.ok(result, 'updateTransfer')['transfer']['legs'], [])
        # Only while PENDING
        await models.Transfer.objects.filter(pk=id).aupdate(status='ACCEPTED')
        result = await self.execute(
            query, {'data': {'id': id, 'legs': [leg('A', 0)]}}, self.customer
        )
        self.assertEqual(self.error_code(result), 'ERR014')

    async def test_only_accepted_operators_get_transfers(self):
        await User.objects.filter(pk=self.operator.id).aupdate(status='PENDING')
        await self.other_operator.districts.aset([self.beyoglu])
        created = await self.create_transfer(
            pickupDistrictName='Beyoglu', dropoffDistrictName='Glyfada'
        )
        self.assertEqual(
            created['transfer']['operatorId'], str(self.other_operator.id)
        )
        # Glyfada only has the PENDING operator: not serviceable
        result = await self.execute(
            CREATE_TRANSFER,
            {'data': self.transfer_data(
                pickupDistrictName='Glyfada', dropoffDistrictName='Glyfada'
            )},
            user=self.customer
        )
        self.assertEqual(self.error_code(result), 'ERR035')

    async def test_create_transfer_sets_districts_and_state(self):
        def ids(transfer):
            keys = ['pickupDistrictId', 'dropoffDistrictId', 'stateId']
            return [transfer[key] for key in keys]

        def expected(pickup, dropoff, state):
            return [str(i.id) if i else None for i in (pickup, dropoff, state)]

        cases = [
            (
                {'pickupDistrictName': ' arnavutkoy ',
                 'dropoffDistrictName': 'BEYOGLU'},
                (self.arnavutkoy, self.beyoglu, self.turkey)
            ),
            (
                {'dropoffDistrictName': 'Glyfada'},
                (self.arnavutkoy, self.glyfada, self.turkey)
            ),
            (
                {'pickupDistrictName': 'Unknown',
                 'dropoffDistrictName': 'Glyfada'},
                (None, self.glyfada, self.greece)
            ),
            (
                {'dropoffDistrictName': 'Closed'},
                (self.arnavutkoy, None, self.turkey)
            ),
        ]
        for kwargs, districts in cases:
            created = await self.create_transfer(**kwargs)
            self.assertEqual(
                ids(created['transfer']), expected(*districts), kwargs
            )
        data = self.transfer_data(
            pickupDistrictName='Unknown', dropoffDistrictName='Closed'
        )
        result = await self.execute(
            CREATE_TRANSFER, {'data': data}, user=self.customer
        )
        self.assertEqual(self.error_code(result), 'ERR035')
        self.assertEqual(await models.Transfer.objects.acount(), len(cases))

    async def test_update_transfer_districts(self):
        created = await self.create_transfer()
        transfer_id = created['transfer']['id']
        data = {'id': transfer_id, 'pickupDistrictName': 'Glyfada'}
        result = await self.execute(UPDATE_TRANSFER, {'data': data}, self.customer)
        transfer = self.ok(result, 'updateTransfer')['transfer']
        self.assertEqual(transfer['pickupDistrictId'], str(self.glyfada.id))
        self.assertEqual(transfer['dropoffDistrictId'], str(self.beyoglu.id))
        self.assertEqual(transfer['stateId'], str(self.greece.id))
        data = {'id': transfer_id, 'pickupDistrictName': 'Unknown'}
        result = await self.execute(UPDATE_TRANSFER, {'data': data}, self.customer)
        transfer = self.ok(result, 'updateTransfer')['transfer']
        self.assertIsNone(transfer['pickupDistrictId'])
        self.assertEqual(transfer['stateId'], str(self.turkey.id))
        data = {'id': transfer_id, 'dropoffDistrictName': 'Unknown'}
        result = await self.execute(UPDATE_TRANSFER, {'data': data}, self.customer)
        self.assertEqual(self.error_code(result), 'ERR035')

    async def test_pay_transfer(self):
        created = await self.create_transfer(passengerCount=5)
        transfer_id = created['transfer']['id']
        data = {'id': transfer_id, 'vehicleCategoryId': str(self.inactive.id)}
        result = await self.execute(PAY_TRANSFER, {'data': data}, self.customer)
        self.assertEqual(self.error_code(result), 'ERR025')
        data['vehicleCategoryId'] = str(self.van.id)
        result = await self.execute(PAY_TRANSFER, {'data': data}, self.customer)
        payment = self.ok(result, 'payTransfer')
        self.assertEqual(payment['paymentLink'], 'https://pay.test/createPaymentLink')
        transfer = await models.Transfer.objects.aget(pk=transfer_id)
        self.assertEqual(transfer.status, 'PENDING')
        self.assertEqual(transfer.vehicle_category_id, self.van.id)
        self.assertEqual(transfer.vehicle_count, 1)
        self.assertEqual(transfer.price, Decimal('960.00'))
        self.assertEqual(transfer.psp_data['order_amount'], '960.00')
        path, payload = self.async_post.call_args.args
        self.assertEqual(path, '/createPaymentLink')
        self.assertEqual(payload['transaction']['order_amount'], Decimal('960.00'))
        # Choosing again expires the previous link first
        result = await self.execute(PAY_TRANSFER, {'data': data}, self.customer)
        self.ok(result, 'payTransfer')
        self.assertEqual(
            self.psp_paths()[-2:], ['/expirePaymentLink', '/createPaymentLink']
        )

    async def test_vehicle_options_follow_updated_state(self):
        # Only Greece allows multiple vehicles, the transfer starts in Turkey
        states = models.State.objects.filter(pk=self.greece.pk)
        await states.aupdate(allow_multiple_vehicles=True)
        created = await self.create_transfer(passengerCount=5)
        names = [o['vehicleCategory']['name'] for o in created['vehicleOptions']]
        self.assertEqual(names, ['Van'])
        data = {
            'id': created['transfer']['id'],
            'pickupDistrictName': 'Glyfada',
            'dropoffDistrictName': 'Glyfada'
        }
        result = await self.execute(UPDATE_TRANSFER, {'data': data}, self.customer)
        updated = self.ok(result, 'updateTransfer')
        options = [
            (o['vehicleCategory']['name'], o['count'])
            for o in updated['vehicleOptions']
        ]
        self.assertEqual(options, [('Sedan', 2), ('Van', 1), ('Accessible Van', 2)])

    async def test_pay_transfer_single_vehicle(self):
        # The state does not allow multiple vehicles (default)
        created = await self.create_transfer(passengerCount=5)
        transfer_id = created['transfer']['id']
        data = {'id': transfer_id, 'vehicleCategoryId': str(self.sedan.id)}
        result = await self.execute(PAY_TRANSFER, {'data': data}, self.customer)
        self.assertEqual(self.error_code(result), 'ERR025')
        data = {
            'id': transfer_id,
            'vehicleCategoryId': str(self.van.id),
            'vehicleCount': 2
        }
        result = await self.execute(PAY_TRANSFER, {'data': data}, self.customer)
        self.assertEqual(self.error_code(result), 'ERR047')
        data['vehicleCount'] = 1
        result = await self.execute(PAY_TRANSFER, {'data': data}, self.customer)
        self.ok(result, 'payTransfer')
        transfer = await models.Transfer.objects.aget(pk=transfer_id)
        self.assertEqual(transfer.vehicle_count, 1)

    async def test_pay_transfer_vehicle_count(self):
        await self.allow_multiple_vehicles()
        created = await self.create_transfer(passengerCount=5)
        transfer_id = created['transfer']['id']
        options = {
            o['vehicleCategory']['id']: o for o in created['vehicleOptions']
        }
        unit_price = Decimal(options[str(self.sedan.id)]['price'])
        data = {
            'id': transfer_id,
            'vehicleCategoryId': str(self.sedan.id),
            'vehicleCount': 1
        }
        result = await self.execute(PAY_TRANSFER, {'data': data}, self.customer)
        self.assertEqual(self.error_code(result), 'ERR046')
        # Defaults to the option's count
        del data['vehicleCount']
        result = await self.execute(PAY_TRANSFER, {'data': data}, self.customer)
        self.ok(result, 'payTransfer')
        transfer = await models.Transfer.objects.aget(pk=transfer_id)
        self.assertEqual(transfer.vehicle_count, 2)
        self.assertEqual(transfer.price, unit_price * 2)
        # More than the minimum is allowed
        data['vehicleCount'] = 3
        result = await self.execute(PAY_TRANSFER, {'data': data}, self.customer)
        self.ok(result, 'payTransfer')
        transfer = await models.Transfer.objects.aget(pk=transfer_id)
        self.assertEqual(transfer.vehicle_count, 3)
        self.assertEqual(transfer.price, unit_price * 3)
        path, payload = self.async_post.call_args.args
        self.assertEqual(payload['transaction']['order_amount'], unit_price * 3)
        # Changing the needs clears the count with the selection
        data = {'id': transfer_id, 'passengerCount': 6}
        result = await self.execute(UPDATE_TRANSFER, {'data': data}, self.customer)
        self.ok(result, 'updateTransfer')
        transfer = await models.Transfer.objects.aget(pk=transfer_id)
        self.assertEqual(transfer.vehicle_count, 1)

    async def test_update_transfer_clears_vehicle_selection(self):
        created = await self.create_transfer()
        transfer_id = created['transfer']['id']
        data = {'id': transfer_id, 'vehicleCategoryId': str(self.sedan.id)}
        await self.execute(PAY_TRANSFER, {'data': data}, self.customer)
        data = {'id': transfer_id, 'note': 'Child on board'}
        result = await self.execute(UPDATE_TRANSFER, {'data': data}, self.customer)
        self.assertEqual(
            self.ok(result, 'updateTransfer')['transfer']['vehicleCategoryId'],
            str(self.sedan.id)
        )
        data = {'id': transfer_id, 'passengerCount': 6}
        result = await self.execute(UPDATE_TRANSFER, {'data': data}, self.customer)
        updated = self.ok(result, 'updateTransfer')
        self.assertIsNone(updated['transfer']['vehicleCategoryId'])
        self.assertIsNone(updated['transfer']['price'])
        options = [
            (o['vehicleCategory']['name'], o['count'])
            for o in updated['vehicleOptions']
        ]
        self.assertEqual(options, [('Van', 1)])
        self.assertEqual(self.psp_paths()[-1], '/expirePaymentLink')

    async def test_payment_return_marks_paid_and_notifies_operators(self):
        transfer = await self.paid_transfer()
        self.assertEqual(transfer.status, 'ACCEPTED')
        self.assertEqual(transfer.vehicle_category_id, self.sedan.id)
        payments = models.Transaction.objects.filter(transfer=transfer)
        payment = await payments.aget()
        self.assertEqual(payment.category, 'PAYMENT')
        self.assertEqual(payment.order_amount, Decimal('605.00'))
        self.assertEqual(payment.order_id, 'order_1')
        notifications = models.Notification.objects.filter(action='TRANSFER_ACCEPTED')
        user_ids = {n.user_id async for n in notifications}
        # Transfer's operator and all ADMIN/STAFF, each also gets an email
        self.assertEqual(
            user_ids, {transfer.operator_id, self.admin.id, self.staff.id}
        )
        operator = await User.objects.aget(pk=transfer.operator_id)
        emails = [
            call.kwargs['json'] for call in self.mail_post.call_args_list
            if call.kwargs['json']['action'] == 'transfer_accepted'
        ]
        self.assertEqual(
            sorted(email['to_user']['email'] for email in emails),
            sorted([
                'admin@example.com', 'staff@example.com',
                self.customer.email, operator.email
            ])
        )
        self.assertNotIn('psp_data', emails[0]['transfer'])
        mails_before = self.mail_post.call_count - len(emails)
        # A repeated return is processed once
        await self.psp_return({
            'action': 'PAYMENT_RETURN', 'id': str(transfer.id), 'success': True
        })
        self.assertEqual(await payments.acount(), 1)
        self.assertEqual(await notifications.acount(), 3)
        self.assertEqual(self.mail_post.call_count, len(emails) + mails_before)

    async def test_payment_return_rules(self):
        created = await self.create_transfer()
        transfer_id = created['transfer']['id']
        data = {'action': 'PAYMENT_RETURN', 'id': transfer_id, 'success': True}
        response = await self.psp_return(data, token='wrong')
        self.assertEqual(response.status_code, 403)
        data['success'] = False
        await self.psp_return(data)
        transfer = await models.Transfer.objects.aget(pk=transfer_id)
        self.assertEqual(transfer.status, 'PENDING')

    async def test_transfers_are_scoped(self):
        transfer = await self.assigned_transfer()
        await self.create_transfer(customer=self.other_customer)
        expected = {
            self.customer: 1, self.other_customer: 1, self.driver: 1,
            self.other_driver: 0, self.operator: 2, self.admin: 2
        }
        for user, count in expected.items():
            result = await self.execute(TRANSFERS, user=user)
            self.assertEqual(self.ok(result, 'transfers')['count'], count, user)
        result = await self.execute(
            TRANSFER, {'id': str(transfer.id)}, self.other_driver
        )
        self.assertIsNone(self.ok(result, 'transfer'))


class AvailableDriverTests(GraphQLTestCase):
    def setUp(self):
        super().setUp()

        def driver(email, category, status='ACCEPTED', **kwargs):
            user = User.objects.create(
                email=email, role='DRIVER', first_name='D', last_name='T',
                status=status
            )
            models.Vehicle.objects.create(
                driver=user, category=category, plate=email[:10],
                brand='B', model='M', **kwargs
            )
            return user
        self.van_driver = driver('van@example.com', self.van)
        self.inactive_vehicle_driver = driver(
            'inactive@example.com', self.sedan, is_active=False
        )
        self.inactive_driver = driver('passive@example.com', self.sedan)
        User.objects.filter(pk=self.inactive_driver.pk).update(is_active=False)
        # Not verified yet: never available
        self.pending_driver = driver(
            'pending@example.com', self.sedan, status='PENDING'
        )

    async def available_ids(self, transfer):
        result = await self.execute(
            AVAILABLE_DRIVERS, {'transferId': str(transfer.id)}, self.operator
        )
        drivers = self.ok(result, 'availableDrivers')
        self.assertEqual(drivers['count'], len(drivers['users']))
        return {int(user['id']) for user in drivers['users']}

    async def busy_transfer(self, driver, pickup_time, duration=60):
        transfer = await models.Transfer.objects.acreate(
            customer=self.other_customer, status='ACCEPTED',
            vehicle_category=self.sedan, pickup_time=pickup_time,
            duration=duration, distance='10', passenger_name='X',
            pickup_address='A', pickup_latitude=0, pickup_longitude=0,
            dropoff_address='B', dropoff_latitude=0, dropoff_longitude=0
        )
        await models.TransferDriver.objects.acreate(
            transfer=transfer, driver=driver
        )
        return transfer

    async def test_available_drivers(self):
        transfer = await self.paid_transfer()
        # Only active drivers with an active vehicle of the category
        expected = {self.driver.id, self.other_driver.id}
        self.assertEqual(await self.available_ids(transfer), expected)

    async def test_busy_drivers_are_excluded(self):
        transfer = await self.paid_transfer()
        # pickup + 50 min, buffer 30 min on both sides
        busy = await self.busy_transfer(
            self.other_driver, transfer.end_time + timedelta(minutes=20)
        )
        self.assertEqual(await self.available_ids(transfer), {self.driver.id})
        # Far enough after the transfer: available again
        busy.pickup_time = transfer.end_time + timedelta(minutes=31)
        await busy.asave()
        expected = {self.driver.id, self.other_driver.id}
        self.assertEqual(await self.available_ids(transfer), expected)
        # Ends shortly before pickup: busy
        busy.pickup_time = transfer.pickup_time - timedelta(minutes=80)
        await busy.asave()
        self.assertEqual(await self.available_ids(transfer), {self.driver.id})
        # Cancelled or completed transfers do not keep the driver busy
        busy.status = 'COMPLETED'
        await busy.asave()
        self.assertEqual(await self.available_ids(transfer), expected)

    async def test_other_category_and_scope(self):
        transfer = await self.paid_transfer(category=self.van, passengerCount=5)
        self.assertEqual(await self.available_ids(transfer), {self.van_driver.id})
        result = await self.execute(
            AVAILABLE_VEHICLES, {'transferId': str(transfer.id)}, self.operator
        )
        vehicles = self.ok(result, 'availableVehicles')
        self.assertEqual([v['driverId'] for v in vehicles], [str(self.van_driver.id)])
        result = await self.execute(
            AVAILABLE_DRIVERS, {'transferId': str(transfer.id)}, self.customer
        )
        self.assertEqual(self.error_code(result), 'ERR001')
        pending = await self.create_transfer()
        result = await self.execute(
            AVAILABLE_DRIVERS, {'transferId': pending['transfer']['id']}, self.operator
        )
        self.assertEqual(self.error_code(result), 'ERR014')


class AssignmentTests(GraphQLTestCase):
    async def test_assign_driver(self):
        transfer = await self.paid_transfer()
        data = {'id': str(transfer.id), 'driverId': str(self.customer.id)}
        result = await self.execute(ASSIGN_DRIVER, {'data': data}, self.operator)
        self.assertEqual(self.error_code(result), 'ERR015')
        data['driverId'] = str(self.driver.id)
        data['vehicleId'] = str(self.other_driver_vehicle.id)
        result = await self.execute(ASSIGN_DRIVER, {'data': data}, self.operator)
        self.assertEqual(self.error_code(result), 'ERR027')
        del data['vehicleId']
        result = await self.execute(ASSIGN_DRIVER, {'data': data}, self.customer)
        self.assertEqual(self.error_code(result), 'ERR001')
        result = await self.execute(ASSIGN_DRIVER, {'data': data}, self.operator)
        assigned = self.ok(result, 'assignDriver')
        [driver] = assigned['drivers']
        self.assertEqual(driver['driverId'], str(self.driver.id))
        self.assertEqual(driver['vehicleId'], str(self.driver_vehicle.id))
        self.assertEqual(assigned['operatorId'], str(self.operator.id))
        self.assertEqual(assigned['status'], 'ACCEPTED')
        notifications = models.Notification.objects.filter(
            action='TRANSFER_ASSIGNED'
        )
        user_ids = {n.user_id async for n in notifications}
        self.assertEqual(user_ids, {self.driver.id, self.customer.id})

    async def test_assigned_driver_is_busy_for_overlapping_transfer(self):
        await self.assigned_transfer()
        transfer = await self.paid_transfer()
        data = {'id': str(transfer.id), 'driverId': str(self.driver.id)}
        result = await self.execute(ASSIGN_DRIVER, {'data': data}, self.operator)
        self.assertEqual(self.error_code(result), 'ERR015')

    async def test_remove_driver_notifies_and_frees_the_slot(self):
        transfer = await self.assigned_transfer()
        assignment = await transfer.drivers.aget()
        variables = {'id': str(assignment.id)}
        result = await self.execute(
            REMOVE_TRANSFER_DRIVER, variables, self.other_operator
        )
        self.assertEqual(self.error_code(result), 'ERR049')
        result = await self.execute(REMOVE_TRANSFER_DRIVER, variables, self.admin)
        self.assertEqual(self.ok(result, 'removeTransferDriver')['drivers'], [])
        notifications = models.Notification.objects.filter(
            user=self.driver, action='TRANSFER_CANCELLED'
        )
        self.assertTrue(await notifications.aexists())
        # The removed driver no longer sees the transfer
        result = await self.execute(TRANSFER, {'id': str(transfer.id)}, self.driver)
        self.assertIsNone(self.ok(result, 'transfer'))
        data = {'id': str(transfer.id), 'driverId': str(self.other_driver.id)}
        result = await self.execute(ASSIGN_DRIVER, {'data': data}, self.admin)
        [driver] = self.ok(result, 'assignDriver')['drivers']
        self.assertEqual(driver['driverId'], str(self.other_driver.id))

    async def test_multiple_drivers(self):
        await models.State.objects.filter(pk=self.turkey.pk).aupdate(
            allow_multiple_vehicles=True
        )
        transfer = await self.paid_transfer(passengerCount=5)
        self.assertEqual(transfer.vehicle_count, 2)
        data = {'id': str(transfer.id), 'driverId': str(self.driver.id)}
        result = await self.execute(ASSIGN_DRIVER, {'data': data}, self.operator)
        self.ok(result, 'assignDriver')
        # Already assigned: not available again, nor is their vehicle
        result = await self.execute(ASSIGN_DRIVER, {'data': data}, self.operator)
        self.assertEqual(self.error_code(result), 'ERR015')
        result = await self.execute(
            AVAILABLE_DRIVERS, {'transferId': str(transfer.id)}, self.operator
        )
        users = self.ok(result, 'availableDrivers')['users']
        self.assertEqual([u['id'] for u in users], [str(self.other_driver.id)])
        data = {'id': str(transfer.id), 'driverName': 'External'}
        result = await self.execute(ASSIGN_DRIVER, {'data': data}, self.operator)
        drivers = self.ok(result, 'assignDriver')['drivers']
        self.assertEqual(
            [(d['driverId'], d['isExternal']) for d in drivers],
            [(str(self.driver.id), False), (None, True)]
        )
        data = {'id': str(transfer.id), 'driverId': str(self.other_driver.id)}
        result = await self.execute(ASSIGN_DRIVER, {'data': data}, self.operator)
        self.assertEqual(self.error_code(result), 'ERR048')
        # Filters by any of its drivers
        query = '''
        query($filters: TransferFilter) { transfers(filters: $filters) { count } }
        '''
        for filters, count in (
            ({'driverId': str(self.driver.id)}, 1),
            ({'driverId': str(self.other_driver.id)}, 0),
            ({'vehicleId': str(self.driver_vehicle.id)}, 1),
            ({'hasDriver': True}, 1),
            ({'hasDriver': False}, 0),
        ):
            result = await self.execute(query, {'filters': filters}, self.admin)
            self.assertEqual(self.ok(result, 'transfers')['count'], count, filters)

    async def test_status_transitions(self):
        transfer = await self.assigned_transfer()
        data = {'id': str(transfer.id), 'status': 'ACCEPTED'}
        result = await self.execute(UPDATE_TRANSFER_STATUS, {'data': data}, self.driver)
        self.assertEqual(self.error_code(result), 'ERR014')
        data['status'] = 'STARTED'
        result = await self.execute(
            UPDATE_TRANSFER_STATUS, {'data': data}, self.other_driver
        )
        self.assertEqual(self.error_code(result), 'ERR013')
        result = await self.execute(UPDATE_TRANSFER_STATUS, {'data': data}, self.driver)
        self.assertEqual(self.ok(result, 'updateTransferStatus')['status'], 'STARTED')
        data['status'] = 'COMPLETED'
        result = await self.execute(UPDATE_TRANSFER_STATUS, {'data': data}, self.driver)
        self.assertEqual(
            self.ok(result, 'updateTransferStatus')['status'], 'COMPLETED'
        )
        # A completed transfer can't change again
        result = await self.execute(UPDATE_TRANSFER_STATUS, {'data': data}, self.driver)
        self.assertEqual(self.error_code(result), 'ERR014')

    async def test_start_and_end_at_once(self):
        transfer = await self.assigned_transfer()
        data = {'id': str(transfer.id), 'status': 'COMPLETED'}
        result = await self.execute(UPDATE_TRANSFER_STATUS, {'data': data}, self.driver)
        self.assertEqual(
            self.ok(result, 'updateTransferStatus')['status'], 'COMPLETED'
        )


class PaymentTests(GraphQLTestCase):
    async def test_cancel_unpaid_transfer_expires_link(self):
        created = await self.create_transfer()
        transfer_id = created['transfer']['id']
        data = {'id': transfer_id, 'vehicleCategoryId': str(self.sedan.id)}
        await self.execute(PAY_TRANSFER, {'data': data}, self.customer)
        result = await self.execute(CANCEL_TRANSFER, {'id': transfer_id}, self.customer)
        self.assertEqual(self.ok(result, 'cancelTransfer')['status'], 'CANCELLED')
        self.assertEqual(self.psp_paths()[-1], '/expirePaymentLink')
        self.assertFalse(await models.Transaction.objects.aexists())

    async def test_cancel_paid_transfer_refunds(self):
        transfer = await self.assigned_transfer()
        result = await self.execute(
            CANCEL_TRANSFER, {'id': str(transfer.id)}, self.customer
        )
        self.assertEqual(self.ok(result, 'cancelTransfer')['status'], 'CANCELLED')
        refund = await models.Transaction.objects.aget(category='REFUND')
        self.assertEqual(refund.order_amount, Decimal('605.00'))
        self.assertEqual(refund.order_id, 'refund_1')
        path, payload = self.async_post.call_args.args
        self.assertEqual(path, '/refundPayment')
        self.assertEqual(payload['psp'], 'stripe')
        notifications = models.Notification.objects.filter(
            action='TRANSFER_CANCELLED'
        )
        user_ids = {n.user_id async for n in notifications}
        self.assertEqual(user_ids, expected)

    @override_settings(CANCEL_PENALTY_RULES=[{'hours': 72, 'rate': '0.20'}])
    async def test_customer_cancel_penalty(self):
        transfer = await self.paid_transfer()
        await self.execute(CANCEL_TRANSFER, {'id': str(transfer.id)}, self.customer)
        refund = await models.Transaction.objects.aget(category='REFUND')
        self.assertEqual(refund.order_amount, Decimal('484.00'))

    @override_settings(CANCEL_PENALTY_RULES=[{'hours': 72, 'rate': '0.20'}])
    async def test_operator_cancel_refunds_all(self):
        transfer = await self.paid_transfer()
        await self.execute(CANCEL_TRANSFER, {'id': str(transfer.id)}, self.operator)
        refund = await models.Transaction.objects.aget(category='REFUND')
        self.assertEqual(refund.order_amount, Decimal('605.00'))

    async def reason(self, category, code=None, **kwargs):
        return await models.Reason.objects.acreate(
            code=code or category.lower(), category=category,
            description_en=f'{category} en', description_tr=f'{category} tr',
            **kwargs
        )

    async def cancel(self, transfer, user, **data):
        data = {'id': str(transfer.id), **data}
        return await self.execute(CANCEL_WITH_REASON, {'data': data}, user)

    async def test_cancel_with_reason(self):
        reason = await self.reason('OPERATOR_CANCELLATION')
        transfer = await self.paid_transfer()
        await User.objects.filter(pk=self.operator.id).aupdate(language='tr')
        self.operator.language = 'tr'
        result = await self.cancel(
            transfer, self.operator, cancellationReasonId=str(reason.id)
        )
        cancelled = self.ok(result, 'cancelTransfer')
        self.assertEqual(cancelled['status'], 'CANCELLED')
        self.assertEqual(cancelled['cancellationReason'], {
            'code': 'operator_cancellation',
            'description': 'OPERATOR_CANCELLATION tr'
        })
        self.assertEqual(cancelled['cancellationReasonText'], '')
        # A used reason can't be deleted, only deactivated
        result = await self.execute(DELETE_REASON, {'id': str(reason.id)}, self.admin)
        self.assertEqual(self.error_code(result), 'ERR054')
        filters = {'cancellationReasonCode': ['operator_cancellation']}
        result = await self.execute(
            TRANSFERS_FILTERED, {'filters': filters}, self.admin
        )
        self.assertEqual(self.ok(result, 'transfers')['count'], 1)

    async def test_customer_reason_category_by_status(self):
        termination = await self.reason('CUSTOMER_TERMINATION')
        cancellation = await self.reason('CUSTOMER_CANCELLATION')
        created = await self.create_transfer()
        pending = await models.Transfer.objects.aget(pk=created['transfer']['id'])
        result = await self.cancel(
            pending, self.customer, cancellationReasonId=str(cancellation.id)
        )
        self.assertEqual(self.error_code(result), 'ERR052')
        result = await self.cancel(
            pending, self.customer, cancellationReasonId=str(termination.id)
        )
        self.assertEqual(self.ok(result, 'cancelTransfer')['status'], 'WITHDRAWN')
        paid = await self.paid_transfer()
        result = await self.cancel(
            paid, self.customer, cancellationReasonId=str(termination.id)
        )
        self.assertEqual(self.error_code(result), 'ERR052')
        result = await self.cancel(
            paid, self.customer, cancellationReasonId=str(cancellation.id)
        )
        self.assertEqual(self.ok(result, 'cancelTransfer')['status'], 'CANCELLED')

    async def test_cancel_with_text(self):
        transfer = await self.paid_transfer()
        result = await self.cancel(
            transfer, self.operator, cancellationReasonText='  Road closed '
        )
        cancelled = self.ok(result, 'cancelTransfer')
        self.assertEqual(cancelled['cancellationReasonText'], 'Road closed')
        self.assertIsNone(cancelled['cancellationReason'])

    async def test_invalid_cancel_reason(self):
        inactive = await self.reason('OPERATOR_CANCELLATION', is_active=False)
        transfer = await self.paid_transfer()
        cases = [
            ({}, 'ERR051'),
            ({'cancellationReasonText': '  '}, 'ERR051'),
            ({'cancellationReasonText': 'ab'}, 'ERR030'),
            ({'cancellationReasonText': 'x' * 201}, 'ERR030'),
            ({'cancellationReasonId': str(inactive.id)}, 'ERR050'),
            ({'cancellationReasonId': '999'}, 'ERR050'),
            ({
                'cancellationReasonId': str(inactive.id),
                'cancellationReasonText': 'Road closed'
            }, 'ERR030'),
        ]
        for data, code in cases:
            result = await self.cancel(transfer, self.operator, **data)
            self.assertEqual(self.error_code(result), code, data)
        await transfer.arefresh_from_db()
        self.assertEqual(transfer.status, 'ACCEPTED')

    async def test_refund_transaction(self):
        transfer = await self.paid_transfer()
        payment = await models.Transaction.objects.aget(transfer=transfer)
        data = {'id': str(payment.id), 'amount': '100.00'}
        result = await self.execute(REFUND_TRANSACTION, {'data': data}, self.operator)
        self.assertEqual(self.error_code(result), 'ERR001')
        result = await self.execute(REFUND_TRANSACTION, {'data': data}, self.admin)
        refund = self.ok(result, 'refundTransaction')
        self.assertEqual(refund['category'], 'REFUND')
        self.assertEqual(refund['parentId'], str(payment.id))
        data['amount'] = '505.01'
        result = await self.execute(REFUND_TRANSACTION, {'data': data}, self.admin)
        self.assertEqual(self.error_code(result), 'ERR032')
        del data['amount']
        result = await self.execute(REFUND_TRANSACTION, {'data': data}, self.admin)
        self.assertEqual(
            Decimal(self.ok(result, 'refundTransaction')['orderAmount']),
            Decimal('505.00')
        )
        data = {'id': refund['id']}
        result = await self.execute(REFUND_TRANSACTION, {'data': data}, self.admin)
        self.assertEqual(self.error_code(result), 'ERR034')

    async def completed_transfer(self):
        transfer = await self.assigned_transfer()
        await models.Transfer.objects.filter(pk=transfer.pk).aupdate(
            status='COMPLETED'
        )
        return transfer

    async def test_tip_with_several_drivers(self):
        transfer = await self.completed_transfer()
        first = await transfer.drivers.aget()
        await models.TransferDriver.objects.acreate(
            transfer=transfer, driver_name='External'
        )
        external = await transfer.drivers.aget(driver__isnull=True)
        second = await models.TransferDriver.objects.acreate(
            transfer=transfer, driver=self.other_driver
        )
        data = {'id': str(transfer.id), 'amount': '20'}
        # Two internal drivers: which one must be given
        result = await self.execute(TIP_DRIVER, {'data': data}, self.customer)
        self.assertEqual(self.error_code(result), 'ERR030')
        # External drivers can't be tipped
        data['transferDriverId'] = str(external.id)
        result = await self.execute(TIP_DRIVER, {'data': data}, self.customer)
        self.assertEqual(self.error_code(result), 'ERR033')
        data['transferDriverId'] = str(second.id)
        result = await self.execute(TIP_DRIVER, {'data': data}, self.customer)
        self.ok(result, 'tipDriver')
        await self.psp_return({
            'action': 'TIP_RETURN', 'id': str(transfer.id), 'success': True
        })
        tip = await models.Transaction.objects.aget(category='TIP')
        self.assertEqual(tip.driver_id, self.other_driver.id)
        notifications = models.Notification.objects.filter(
            action='TIP_PAYMENT_SUCCESS'
        )
        user_ids = {n.user_id async for n in notifications}
        self.assertEqual(user_ids, {self.other_driver.id})
        # Each driver sees only their own tips
        for user, count in ((self.other_driver, 1), (self.driver, 0)):
            result = await self.execute(TRANSACTIONS, user=user)
            self.assertEqual(self.ok(result, 'transactions')['count'], count)
        self.assertEqual(first.driver_id, self.driver.id)

    async def test_tip_driver(self):
        transfer = await self.assigned_transfer()
        data = {'id': str(transfer.id), 'amount': '50'}
        result = await self.execute(TIP_DRIVER, {'data': data}, self.customer)
        self.assertEqual(self.error_code(result), 'ERR014')
        await models.Transfer.objects.filter(pk=transfer.pk).aupdate(
            status='COMPLETED'
        )
        data['amount'] = '0'
        result = await self.execute(TIP_DRIVER, {'data': data}, self.customer)
        self.assertEqual(self.error_code(result), 'ERR033')
        data['amount'] = '50'
        result = await self.execute(TIP_DRIVER, {'data': data}, self.customer)
        self.assertEqual(
            self.ok(result, 'tipDriver')['paymentLink'],
            'https://pay.test/createTipLink'
        )
        tip_return = {
            'action': 'TIP_RETURN', 'id': str(transfer.id), 'success': True,
            'order_id': 'tip_order'
        }
        await self.psp_return(tip_return)
        await self.psp_return(tip_return)
        tip = await models.Transaction.objects.aget(category='TIP')
        self.assertEqual(tip.order_amount, Decimal('50.00'))
        self.assertEqual(tip.user_id, self.customer.id)
        notifications = models.Notification.objects.filter(
            user=self.driver, action='TIP_PAYMENT_SUCCESS'
        )
        self.assertEqual(await notifications.acount(), 1)
        # The driver sees the tip, not the transfer payment
        result = await self.execute(TRANSACTIONS, user=self.driver)
        transactions = self.ok(result, 'transactions')['transactions']
        self.assertEqual([t['category'] for t in transactions], ['TIP'])
        result = await self.execute(TRANSACTIONS, user=self.customer)
        self.assertEqual(self.ok(result, 'transactions')['count'], 2)
        # Tips can be refunded too
        data = {'id': str(tip.id)}
        result = await self.execute(REFUND_TRANSACTION, {'data': data}, self.staff)
        self.assertEqual(
            Decimal(self.ok(result, 'refundTransaction')['orderAmount']),
            Decimal('50.00')
        )
        _, payload = self.async_post.call_args.args
        self.assertEqual(payload['transactions'][0]['category'], 'TIP')

    async def test_payment_service_error(self):
        response = Mock()
        response.json.return_value = {'success': False, 'error': 'down'}
        with patch('core.psp.AsyncClient') as client:
            post = AsyncMock(return_value=response)
            client.return_value.__aenter__.return_value.post = post
            with self.assertRaises(Error) as context:
                await real_async_post('/createPaymentLink', {})
        self.assertEqual(loads(context.exception.message)['code'], 'ERR031')
        self.assertEqual(context.exception.error_code, 'down')
        url = post.call_args.args[0]
        self.assertEqual(url, 'https://psp.test/createPaymentLink')
        headers = post.call_args.kwargs['headers']
        self.assertEqual(headers['X-Access-Token'], 'test-ms-access-token')


class RefundTests(GraphQLTestCase):
    def fail_refunds(self):
        def response(path, data):
            if path == '/refundPayment':
                raise PSPError('insufficient_balance')
            return psp_response(path, data)
        self.async_post.side_effect = response

    async def test_payment_is_completed(self):
        transfer = await self.paid_transfer()
        payment = await models.Transaction.objects.aget(transfer=transfer)
        self.assertEqual(payment.status, 'COMPLETED')

    async def test_successful_refund_is_completed(self):
        transfer = await self.paid_transfer()
        payment = await models.Transaction.objects.aget(transfer=transfer)
        data = {'id': str(payment.id), 'amount': '100'}
        result = await self.execute(REFUND_TRANSACTION, {'data': data}, self.admin)
        self.assertEqual(self.ok(result, 'refundTransaction')['status'], 'COMPLETED')
        refund = await models.Transaction.objects.aget(category='REFUND')
        result = await self.execute(RETRY_REFUND, {'id': str(refund.id)}, self.admin)
        self.assertEqual(self.error_code(result), 'ERR042')

    async def test_failed_refund_stays_pending_and_can_be_retried(self):
        transfer = await self.paid_transfer()
        self.fail_refunds()
        result = await self.execute(
            CANCEL_TRANSFER, {'id': str(transfer.id)}, self.customer
        )
        # The cancellation goes through, the refund waits as PENDING
        self.assertEqual(self.ok(result, 'cancelTransfer')['status'], 'CANCELLED')
        refund = await models.Transaction.objects.aget(category='REFUND')
        self.assertEqual(refund.status, 'PENDING')
        # The payment service's error is kept on the refund
        self.assertEqual(refund.error_code, 'insufficient_balance')
        self.assertFalse(await models.Notification.objects.filter(
            action='REFUND'
        ).aexists())
        # The PENDING amount is reserved: no second refund on top of it
        payment = await models.Transaction.objects.aget(category='PAYMENT')
        data = {'id': str(payment.id)}
        result = await self.execute(REFUND_TRANSACTION, {'data': data}, self.admin)
        self.assertEqual(self.error_code(result), 'ERR032')
        variables = {'id': str(refund.id)}
        # Still failing: stays PENDING
        result = await self.execute(RETRY_REFUND, variables, self.customer)
        self.assertEqual(self.ok(result, 'retryRefund')['status'], 'PENDING')
        # Only the customer of the transfer (and staff) can retry
        result = await self.execute(RETRY_REFUND, variables, self.other_customer)
        self.assertEqual(self.error_code(result), 'ERR034')
        result = await self.execute(RETRY_REFUND, variables, self.driver)
        self.assertEqual(self.error_code(result), 'ERR001')
        self.async_post.side_effect = psp_response
        result = await self.execute(RETRY_REFUND, variables, self.customer)
        self.assertEqual(self.ok(result, 'retryRefund')['status'], 'COMPLETED')
        refund = await models.Transaction.objects.aget(category='REFUND')
        self.assertEqual(refund.order_id, 'refund_1')
        self.assertEqual(refund.error_code, '')
        self.assertEqual(await models.Notification.objects.filter(
            action='REFUND'
        ).acount(), 1)
        # A repeated retry does not refund twice
        result = await self.execute(RETRY_REFUND, variables, self.customer)
        self.assertEqual(self.error_code(result), 'ERR042')
        refund_calls = self.psp_paths().count('/refundPayment')
        self.assertEqual(refund_calls, 3)

    async def refunded_amounts(self):
        # category is not requested: refundedAmount must load it itself
        query = '{ transactions { transactions { id refundedAmount } } }'
        result = await self.execute(query, user=self.admin)
        rows = self.ok(result, 'transactions')['transactions']
        return {row['id']: row['refundedAmount'] for row in rows}

    async def test_refunded_amount_counts_completed_refunds(self):
        transfer = await self.paid_transfer()
        payment = await models.Transaction.objects.aget(transfer=transfer)
        pid = str(payment.id)
        self.assertEqual(await self.refunded_amounts(), {pid: '0.00'})
        self.fail_refunds()
        data = {'id': pid, 'amount': '10'}
        await self.execute(REFUND_TRANSACTION, {'data': data}, self.admin)
        refund = await models.Transaction.objects.aget(category='REFUND')
        rid = str(refund.id)
        # A failed (PENDING) refund returned no money
        self.assertEqual(
            await self.refunded_amounts(), {pid: '0.00', rid: None}
        )
        self.async_post.side_effect = psp_response
        await self.execute(RETRY_REFUND, {'id': rid}, self.admin)
        data['amount'] = '5.50'
        await self.execute(REFUND_TRANSACTION, {'data': data}, self.admin)
        amounts = await self.refunded_amounts()
        self.assertEqual(amounts[pid], '15.50')
        self.assertIsNone(amounts[rid])
        # Without the get_transactions annotation the field queries itself
        payment = await models.Transaction.objects.aget(pk=pid)
        field = outputs.Transaction.refunded_amount
        total = await field.base_resolver(payment, None)
        self.assertEqual(total, Decimal('15.50'))

    async def test_concurrent_refunds_do_not_duplicate(self):
        transfer = await self.paid_transfer()
        payment = await models.Transaction.objects.aget(transfer=transfer)
        data = {'id': str(payment.id)}
        results = await asyncio.gather(*[
            self.execute(REFUND_TRANSACTION, {'data': data}, self.admin)
            for _ in range(3)
        ])
        succeeded = [r for r in results if 'errors' not in r]
        self.assertEqual(len(succeeded), 1)
        self.assertTrue(all(
            self.error_code(r) == 'ERR032' for r in results if 'errors' in r
        ))
        self.assertEqual(await models.Transaction.objects.filter(
            category='REFUND'
        ).acount(), 1)


class ExpiringTests(GraphQLTestCase):
    async def pending_transfer(self, hours_ago):
        created = await self.create_transfer()
        transfer_id = created['transfer']['id']
        data = {'id': transfer_id, 'vehicleCategoryId': str(self.sedan.id)}
        await self.execute(PAY_TRANSFER, {'data': data}, self.customer)
        pickup = timezone.now() - timedelta(hours=hours_ago)
        transfers = models.Transfer.objects.filter(pk=transfer_id)
        await transfers.aupdate(pickup_time=pickup)
        return int(transfer_id)

    async def test_past_pending_transfers_expire(self):
        past = await self.pending_transfer(hours_ago=1)
        future = await self.pending_transfer(hours_ago=-5)
        paid = await self.paid_transfer()
        await models.Transfer.objects.filter(pk=paid.pk).aupdate(
            pickup_time=timezone.now() - timedelta(hours=1)
        )
        self.assertEqual(await tasks.check_transfers_expiring(), [past])
        statuses = {
            t.id: t.status async for t in models.Transfer.objects.all()
        }
        self.assertEqual(statuses[past], 'EXPIRED')
        self.assertEqual(statuses[future], 'PENDING')
        # Paid transfers are never expired
        self.assertEqual(statuses[paid.pk], 'ACCEPTED')
        self.assertEqual(self.psp_paths()[-1], '/expirePaymentLink')
        # Already expired ones are skipped on the next run
        self.assertEqual(await tasks.check_transfers_expiring(), [])

    def test_hourly_schedule(self):
        from django.conf import settings
        entry = settings.CELERY_BEAT_SCHEDULE['hourly-tasks']
        self.assertEqual(entry['task'], 'core.tasks.run_hourly_tasks')
        self.assertEqual(tasks.run_hourly_tasks.delay().get(), {
            'expired_transfers': []
        })


class MessageTests(GraphQLTestCase):
    async def send(self, transfer, user, **data):
        data = {'transferId': str(transfer.id), **data}
        return await self.execute(SEND_MESSAGE, {'data': data}, user)

    async def test_messaging_requires_assigned_transfer(self):
        transfer = await self.paid_transfer()
        result = await self.send(transfer, self.customer, message='Hi')
        self.assertEqual(self.error_code(result), 'ERR019')

    async def test_participants(self):
        transfer = await self.assigned_transfer()
        result = await self.send(transfer, self.customer, message='Hi')
        message = self.ok(result, 'sendMessage')
        receiver_ids = {int(user['id']) for user in message['receivers']}
        expected = {self.driver.id, self.operator.id, self.admin.id,
                    self.staff.id}
        self.assertEqual(receiver_ids, expected)
        result = await self.send(transfer, self.other_operator, message='Hi')
        self.assertEqual(self.error_code(result), 'ERR001')
        result = await self.send(transfer, self.other_driver, message='Hi')
        self.assertEqual(self.error_code(result), 'ERR001')
        variables = {'transferId': str(transfer.id)}
        result = await self.execute(MESSAGES, variables, self.driver)
        self.assertEqual(len(self.ok(result, 'messages')), 1)
        result = await self.execute(MESSAGES, variables, self.admin)
        self.assertEqual(len(self.ok(result, 'messages')), 1)
        notifications = models.Notification.objects.filter(action='MESSAGE')
        user_ids = {n.user_id async for n in notifications}
        self.assertEqual(user_ids, expected)

    async def test_admin_and_staff_join_every_conversation(self):
        transfer = await models.Transfer.objects.acreate(
            customer=self.customer, operator=self.operator, status='ACCEPTED',
            vehicle_category=self.sedan, state=self.turkey,
            pickup_time=timezone.now() + timedelta(days=1),
            duration=30, distance='10', passenger_name='X',
            pickup_address='A', pickup_latitude=0, pickup_longitude=0,
            dropoff_address='B', dropoff_latitude=0, dropoff_longitude=0
        )
        await models.TransferDriver.objects.acreate(
            transfer=transfer, driver=self.driver
        )
        await User.objects.acreate(
            email='admin2@example.com', role='ADMIN', first_name='Off',
            last_name='Test', is_active=False
        )
        result = await self.send(transfer, self.customer, message='Hi')
        message = self.ok(result, 'sendMessage')
        receiver_ids = {int(user['id']) for user in message['receivers']}
        participants = {self.driver.id, self.operator.id}
        staff = {self.admin.id, self.staff.id}
        self.assertEqual(receiver_ids, participants | staff)
        notifications = models.Notification.objects.filter(action='MESSAGE')
        user_ids = {n.user_id async for n in notifications}
        self.assertEqual(user_ids, participants | staff)
        # Staff can write too, the other participants receive it
        result = await self.send(transfer, self.staff, message='Hello')
        message = self.ok(result, 'sendMessage')
        receiver_ids = {int(user['id']) for user in message['receivers']}
        self.assertEqual(
            receiver_ids, {self.customer.id, self.admin.id} | participants
        )
        variables = {'transferId': str(transfer.id)}
        result = await self.execute(SEE_MESSAGES, variables, self.admin)
        self.assertTrue(self.ok(result, 'seeMessages')['success'])

    async def test_update_message_marks_edited(self):
        transfer = await self.assigned_transfer()
        result = await self.send(transfer, self.customer, message='Hi')
        message_id = self.ok(result, 'sendMessage')['id']
        message = await models.Message.objects.aget(pk=message_id)
        self.assertFalse(message.is_edited)
        data = {'id': message_id, 'message': 'Hello'}
        result = await self.execute(UPDATE_MESSAGE, {'data': data}, self.customer)
        self.assertEqual(
            self.ok(result, 'updateMessage'),
            {'id': message_id, 'message': 'Hello', 'isEdited': True}
        )


class MessageStatusTests(GraphQLTestCase):
    """SENT -> DELIVERED -> SEEN (ticks)."""

    async def send(self, user, message='Hi'):
        data = {'transferId': str(self.transfer.id), 'message': message}
        result = await self.execute(SEND_MESSAGE, {'data': data}, user)
        return self.ok(result, 'sendMessage')['id']

    async def deliver(self, user, transfer_id=None):
        variables = {'transferId': transfer_id}
        result = await self.execute(DELIVER_MESSAGES, variables, user)
        self.assertTrue(self.ok(result, 'deliverMessages')['success'])

    async def see(self, user, up_to=None):
        variables = {
            'transferId': str(self.transfer.id), 'upToMessageId': up_to
        }
        result = await self.execute(SEE_MESSAGES_UP_TO, variables, user)
        self.assertTrue(self.ok(result, 'seeMessages')['success'])

    async def status(self, pk):
        return (await models.Message.objects.aget(pk=pk)).status

    async def test_customer_message(self):
        self.transfer = await self.assigned_transfer()
        pk = await self.send(self.customer)
        self.assertEqual(await self.status(pk), 'SENT')
        # Neither the sender nor the driver nor users outside count
        for user in (self.customer, self.driver, self.other_operator,
                     self.other_customer):
            await self.deliver(user)
            self.assertEqual(await self.status(pk), 'SENT')
        for user in (self.customer, self.driver):
            await self.see(user)
            self.assertEqual(await self.status(pk), 'SENT')
        # One of ADMIN, STAFF, OPERATOR is enough
        await self.deliver(self.staff, str(self.transfer.id))
        self.assertEqual(await self.status(pk), 'DELIVERED')
        await self.see(self.operator)
        self.assertEqual(await self.status(pk), 'SEEN')
        # Never backwards
        await self.deliver(self.admin)
        self.assertEqual(await self.status(pk), 'SEEN')

    async def test_seen_without_delivery(self):
        self.transfer = await self.assigned_transfer()
        pk = await self.send(self.driver)
        await self.see(self.admin)
        self.assertEqual(await self.status(pk), 'SEEN')

    async def test_dispatch_message_needs_the_customer(self):
        self.transfer = await self.assigned_transfer()
        for sender in (self.operator, self.admin):
            pk = await self.send(sender)
            for user in (self.admin, self.staff, self.operator, self.driver):
                await self.deliver(user)
                await self.see(user)
            self.assertEqual(await self.status(pk), 'SENT')
            await self.deliver(self.customer)
            self.assertEqual(await self.status(pk), 'DELIVERED')
            await self.see(self.customer)
            self.assertEqual(await self.status(pk), 'SEEN')

    async def test_staff_who_joined_later_counts(self):
        self.transfer = await self.assigned_transfer()
        pk = await self.send(self.customer)
        admin = await User.objects.acreate(
            email='admin3@example.com', role='ADMIN', first_name='New',
            last_name='Admin'
        )
        await self.see(admin)
        self.assertEqual(await self.status(pk), 'SEEN')

    async def test_up_to_message(self):
        self.transfer = await self.assigned_transfer()
        first = await self.send(self.customer)
        second = await self.send(self.customer)
        await self.see(self.operator, first)
        self.assertEqual(await self.status(first), 'SEEN')
        self.assertEqual(await self.status(second), 'SENT')
        seen = models.ThroughMessage.objects.filter(
            receiver=self.operator, is_seen=True
        ).values_list('message_id', flat=True)
        self.assertEqual([str(pk) async for pk in seen], [first])

    async def test_only_sender_sees_status(self):
        self.transfer = await self.assigned_transfer()
        pk = await self.send(self.customer)
        variables = {'transferId': str(self.transfer.id)}
        for user, status in (
            (self.customer, 'SENT'), (self.operator, None),
            (self.admin, None), (self.driver, None)
        ):
            result = await self.execute(MESSAGE_STATUSES, variables, user)
            self.assertEqual(
                self.ok(result, 'messages'), [{'id': pk, 'status': status}]
            )
        result = await self.execute(DELETE_MESSAGE, {'id': pk}, self.customer)
        self.assertEqual(
            self.ok(result, 'deleteMessage'),
            {'id': pk, 'status': None, 'isDeleted': True}
        )

    async def test_edit_keeps_status(self):
        self.transfer = await self.assigned_transfer()
        pk = await self.send(self.customer)
        messages = models.Message.objects
        stale = await messages.aget(pk=pk)
        await self.see(self.admin)
        # The edit loads the message before it is seen and saves after
        with patch(
            'core.schemas.message.get_message_receiver_ids',
            AsyncMock(return_value=[])
        ), patch('core.schemas.message.manager') as manager:
            manager.filter.return_value.afirst = AsyncMock(return_value=stale)
            data = {'id': pk, 'message': 'Edited'}
            result = await self.execute(
                UPDATE_MESSAGE, {'data': data}, self.customer
            )
            self.ok(result, 'updateMessage')
        message = await messages.aget(pk=pk)
        self.assertEqual((message.message, message.status), ('Edited', 'SEEN'))

    async def test_sender_gets_one_event_per_change(self):
        self.transfer = await self.assigned_transfer()
        pks = [await self.send(self.customer) for _ in range(2)]
        with patch('core.utils.broadcast', AsyncMock()) as broadcast:
            await self.deliver(self.admin)
            await self.deliver(self.staff)
            await self.see(self.operator)
            await self.see(self.admin)
        calls = broadcast.await_args_list
        self.assertEqual(len(calls), 2)
        for call, reader, status in zip(
            calls, (self.admin, self.operator), ('DELIVERED', 'SEEN')
        ):
            self.assertEqual(
                call.args, (reader.id, 'MESSAGE_STATUS', [self.customer.id])
            )
            self.assertEqual(call.kwargs, {
                'transfer_id': str(self.transfer.id),
                'message_ids': pks,
                'message_status': status
            })

    async def test_backfill(self):
        self.transfer = await self.assigned_transfer()
        by_customer = await self.send(self.customer)
        by_operator = await self.send(self.operator)
        through = models.ThroughMessage.objects
        backfill = import_module(
            'core.migrations.0021_backfill_message_status'
        ).backfill
        # Seen by those who do not count
        await through.filter(
            message_id=by_customer, receiver=self.driver
        ).aupdate(is_seen=True)
        await through.filter(
            message_id=by_operator, receiver=self.admin
        ).aupdate(is_seen=True)
        await s2a(backfill)(apps, None)
        self.assertEqual(await self.status(by_customer), 'SENT')
        self.assertEqual(await self.status(by_operator), 'SENT')
        await through.filter(
            message_id=by_customer, receiver=self.staff
        ).aupdate(is_seen=True)
        await through.filter(
            message_id=by_operator, receiver=self.customer
        ).aupdate(is_seen=True)
        await s2a(backfill)(apps, None)
        self.assertEqual(await self.status(by_customer), 'SEEN')
        self.assertEqual(await self.status(by_operator), 'SEEN')


class MessageReactionTests(GraphQLTestCase):
    async def send(self, user):
        data = {'transferId': str(self.transfer.id), 'message': 'Hi'}
        result = await self.execute(SEND_MESSAGE, {'data': data}, user)
        return self.ok(result, 'sendMessage')['id']

    async def react(self, user, pk, emoji=None):
        variables = {'id': pk, 'emoji': emoji}
        return await self.execute(REACT_MESSAGE, variables, user)

    async def reactions(self, user):
        variables = {'transferId': str(self.transfer.id)}
        result = await self.execute(MESSAGE_REACTIONS, variables, user)
        return [
            [(r['emoji'], r['userId'], r['user']['id']) for r in m['reactions']]
            for m in self.ok(result, 'messages')
        ]

    async def test_add_replace_remove(self):
        self.transfer = await self.assigned_transfer()
        pk = await self.send(self.customer)
        customer, operator = str(self.customer.id), str(self.operator.id)
        result = await self.react(self.operator, pk, 'LIKE')
        self.assertEqual(
            self.ok(result, 'reactMessage'),
            {'id': pk, 'reactions': [{'emoji': 'LIKE', 'userId': operator}]}
        )
        # Own message
        self.ok(await self.react(self.customer, pk, 'PRAY'), 'reactMessage')
        # One per user: replaced
        self.ok(await self.react(self.operator, pk, 'LOVE'), 'reactMessage')
        for user in (self.customer, self.driver, self.admin):
            self.assertEqual(await self.reactions(user), [[
                ('LOVE', operator, operator), ('PRAY', customer, customer)
            ]])
        for _ in range(2):
            result = await self.react(self.operator, pk)
            self.assertEqual(
                self.ok(result, 'reactMessage')['reactions'],
                [{'emoji': 'PRAY', 'userId': customer}]
            )
        result = await self.react(self.operator, pk, 'DANCE')
        self.assertIn('errors', result)

    async def test_access(self):
        self.transfer = await self.assigned_transfer()
        pk = await self.send(self.customer)
        for user in (self.other_customer, self.other_driver,
                     self.other_operator):
            result = await self.react(user, pk, 'LIKE')
            self.assertEqual(self.error_code(result), 'ERR001')
        result = await self.react(self.customer, '0', 'LIKE')
        self.assertEqual(self.error_code(result), 'ERR001')
        # A driver who joined later does not see the message
        await models.TransferDriver.objects.acreate(
            transfer=self.transfer, driver=self.other_driver
        )
        result = await self.react(self.other_driver, pk, 'LIKE')
        self.assertEqual(self.error_code(result), 'ERR001')
        transfers = models.Transfer.objects.filter(pk=self.transfer.pk)
        await transfers.aupdate(status='COMPLETED')
        result = await self.react(self.operator, pk, 'LIKE')
        self.assertEqual(self.error_code(result), 'ERR019')

    async def test_deleted_message(self):
        self.transfer = await self.assigned_transfer()
        pk = await self.send(self.customer)
        self.ok(await self.react(self.operator, pk, 'SAD'), 'reactMessage')
        result = await self.execute(DELETE_MESSAGE, {'id': pk}, self.customer)
        self.ok(result, 'deleteMessage')
        self.assertEqual(await self.reactions(self.customer), [[]])
        result = await self.react(self.operator, pk, 'LIKE')
        self.assertEqual(self.error_code(result), 'ERR021')

    async def test_event_without_notification(self):
        self.transfer = await self.assigned_transfer()
        pk = await self.send(self.customer)
        new_admin = await User.objects.acreate(
            email='admin4@example.com', role='ADMIN', first_name='New',
            last_name='Admin'
        )
        # Joined later: cannot see the message, gets no event
        await models.TransferDriver.objects.acreate(
            transfer=self.transfer, driver=self.other_driver
        )
        before = await models.Notification.objects.acount()
        with patch('core.utils.broadcast', AsyncMock()) as broadcast:
            await self.react(self.operator, pk, 'WOW')
            await self.react(self.operator, pk)
            # Nothing to remove, no event
            await self.react(self.operator, pk)
        self.assertEqual(await models.Notification.objects.acount(), before)
        calls = broadcast.await_args_list
        self.assertEqual(len(calls), 2)
        for call, emoji in zip(calls, ('WOW', '')):
            sender_id, action, receiver_ids = call.args
            self.assertEqual(sender_id, self.operator.id)
            self.assertEqual(action, 'MESSAGE_REACTION')
            self.assertEqual(set(receiver_ids), {
                self.customer.id, self.driver.id, self.admin.id,
                self.staff.id, new_admin.id
            })
            self.assertEqual(call.kwargs, {
                'transfer_id': str(self.transfer.id), 'message_id': pk,
                'message': emoji
            })

    async def test_list_query_count(self):
        self.transfer = await self.assigned_transfer()

        async def count():
            # The request may use another connection than the test
            queries = []
            execute = CursorWrapper.execute

            def capture(cursor, sql, params=None):
                queries.append(sql)
                return execute(cursor, sql, params)

            with patch.object(CursorWrapper, 'execute', capture):
                await self.reactions(self.operator)
            return len(queries)

        pk = await self.send(self.customer)
        await self.react(self.operator, pk, 'LIKE')
        one = await count()
        for _ in range(4):
            pk = await self.send(self.customer)
            await self.react(self.operator, pk, 'LIKE')
            await self.react(self.admin, pk, 'LOVE')
        self.assertGreater(one, 1)
        self.assertEqual(await count(), one)


UPDATE_FCM_TOKEN = '''
mutation($data: UpdateFcmToken!) { updateFcmToken(data: $data) { success } }
'''
SUBSCRIBE = '''
subscription { subscribe { action senderId transferId notificationId message } }
'''


class PushTests(GraphQLTestCase):
    @override_settings(FCM_ENABLED=True)
    async def test_notification_is_pushed_to_devices(self):
        data = {'registrationId': 'device-token', 'type': 'ANDROID'}
        result = await self.execute(UPDATE_FCM_TOKEN, {'data': data}, self.operator)
        self.assertTrue(self.ok(result, 'updateFcmToken')['success'])
        device = await FCMDevice.objects.aget(registration_id='device-token')
        self.assertEqual(device.user_id, self.operator.id)
        target = 'fcm_django.models.FCMDeviceQuerySet.send_message'
        with patch(target) as send_message:
            await self.paid_transfer()
        message = send_message.call_args_list[0].args[0]
        self.assertEqual(message.notification.title, 'New Transfer')
        self.assertEqual(message.data['action'], 'TRANSFER_ACCEPTED')
        self.assertEqual(message.data['model'], 'Transfer')


class SubscriptionTests(GraphQLTestCase):
    async def test_operator_receives_transfer_paid_event(self):
        communicator = GraphQLWebsocketCommunicator(
            application=application, path='/graphql',
            connection_params={
                'authorization': utils.create_token(self.operator.email)
            }
        )
        await communicator.gql_init()
        try:
            stream = communicator.subscribe(SUBSCRIBE)
            task = asyncio.create_task(anext(stream))
            await asyncio.sleep(0.1)
            transfer = await self.paid_transfer()
            events = [(await asyncio.wait_for(task, 5)).data['subscribe']]
            events.append((await asyncio.wait_for(anext(stream), 5)).data['subscribe'])
        finally:
            await communicator.disconnect()
        actions = [event['action'] for event in events]
        self.assertEqual(actions, ['NOTIFICATION', 'TRANSFER_ACCEPTED'])
        self.assertEqual(events[1]['transferId'], str(transfer.id))

    async def test_sender_receives_message_status_event(self):
        transfer = await self.assigned_transfer()
        data = {'transferId': str(transfer.id), 'message': 'Hi'}
        result = await self.execute(SEND_MESSAGE, {'data': data}, self.customer)
        pk = self.ok(result, 'sendMessage')['id']
        communicator = GraphQLWebsocketCommunicator(
            application=application, path='/graphql',
            connection_params={
                'authorization': utils.create_token(self.customer.email)
            }
        )
        await communicator.gql_init()
        try:
            stream = communicator.subscribe(
                'subscription { subscribe '
                '{ action transferId messageIds messageStatus } }'
            )
            task = asyncio.create_task(anext(stream))
            await asyncio.sleep(0.1)
            variables = {'transferId': str(transfer.id)}
            await self.execute(SEE_MESSAGES, variables, self.operator)
            event = (await asyncio.wait_for(task, 5)).data['subscribe']
        finally:
            await communicator.disconnect()
        self.assertEqual(event, {
            'action': 'MESSAGE_STATUS', 'transferId': str(transfer.id),
            'messageIds': [pk], 'messageStatus': 'SEEN'
        })
