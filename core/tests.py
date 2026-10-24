import asyncio
import re
from datetime import timedelta
from decimal import Decimal
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from fcm_django.models import FCMDevice
from json import dumps, loads
from strawberry.channels.testing import GraphQLWebsocketCommunicator
from strawberry.exceptions import StrawberryGraphQLError as Error
from unittest.mock import AsyncMock, Mock, patch
from core import models, utils
from core.psp import async_post as real_async_post
from transfer.asgi import application


User = utils.User
URL = '/graphql/'
PSP_RETURN_URL = '/psp-return/'
PASSWORD = 'S3cure-Passw0rd!'

GET_CODE = '''
mutation($data: GetCode!) { getCode(data: $data) { secret } }
'''
VALIDATE_CODE = '''
mutation($data: ValidateCode!) { validateCode(data: $data) { secret } }
'''
CREATE_USER = '''
mutation($data: CreateUser!) { createUser(data: $data) { token user { role } } }
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
  id status price currency vehicleCategoryId driverId vehicleId operatorId
  stateId pickupDistrictId dropoffDistrictId
'''
OPTION_FIELDS = '''
  vehicleCategory { id name } distance duration price currency
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
UPDATE_TRANSFER_STATUS = f'''
mutation($data: UpdateTransferStatus!) {{
  updateTransferStatus(data: $data) {{ {TRANSFER_FIELDS} }}
}}
'''
CANCEL_TRANSFER = f'''
mutation($id: ID!) {{ cancelTransfer(id: $id) {{ {TRANSFER_FIELDS} }} }}
'''
TIP_DRIVER = '''
mutation($data: TipDriver!) { tipDriver(data: $data) { paymentLink } }
'''
TRANSACTIONS = '''
{ transactions { count transactions { id category orderAmount parentId } } }
'''
REFUND_TRANSACTION = '''
mutation($data: RefundTransaction!) {
  refundTransaction(data: $data) { id category orderAmount parentId }
}
'''
SEND_MESSAGE = '''
mutation($data: SendMessage!) {
  sendMessage(data: $data) { id message receivers { id } }
}
'''
MESSAGES = '''
query($transferId: ID!) { messages(transferId: $transferId) { id message } }
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
        def create(email, role='CUSTOMER'):
            return User.objects.create(
                email=email, role=role, first_name=email.split('@')[0],
                last_name='Test'
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
            'passengerName': 'Ada Lovelace', 'passengerCount': 2,
            'luggageCount': 2,
        }
        data.update(kwargs)
        return data

    async def create_transfer(self, customer=None, **kwargs):
        result = await self.execute(
            CREATE_TRANSFER, {'data': self.transfer_data(**kwargs)},
            user=customer or self.customer
        )
        return self.ok(result, 'createTransfer')

    async def paid_transfer(self, category=None, **kwargs):
        created = await self.create_transfer(**kwargs)
        transfer_id = created['transfer']['id']
        data = {
            'id': transfer_id,
            'vehicleCategoryId': str((category or self.sedan).id)
        }
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
    async def sign_up(self, email, **kwargs):
        data = {'email': email, 'language': 'EN', 'isUser': False}
        result = await self.execute(GET_CODE, {'data': data})
        secret = self.ok(result, 'getCode')['secret']
        code = re.search(r'\d{6}', mail.outbox[-1].body).group()
        data = {'code': code, 'secret': secret}
        result = await self.execute(VALIDATE_CODE, {'data': data})
        secret = self.ok(result, 'validateCode')['secret']
        data = {
            'secret': secret, 'password': PASSWORD,
            'firstName': 'Ada', 'lastName': 'Lovelace', **kwargs
        }
        return await self.execute(CREATE_USER, {'data': data})

    async def test_sign_up_role(self):
        result = await self.sign_up('new@example.com')
        self.assertEqual(self.ok(result, 'createUser')['user']['role'], 'CUSTOMER')
        result = await self.sign_up('newdriver@example.com', role='DRIVER')
        self.assertEqual(self.ok(result, 'createUser')['user']['role'], 'DRIVER')

    async def test_sign_up_cannot_choose_other_roles(self):
        result = await self.sign_up('x@example.com', role='OPERATOR')
        self.assertIn('errors', result)
        self.assertFalse(await User.objects.filter(email='x@example.com').aexists())


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
        result = await self.execute(CREATE_VEHICLE, {'data': data}, self.driver)
        self.assertEqual(self.error_code(result), 'ERR001')


class TransferTests(GraphQLTestCase):
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
        created = await self.create_transfer(passengerCount=5)
        names = [o['vehicleCategory']['name'] for o in created['vehicleOptions']]
        self.assertEqual(names, ['Van'])
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
        data = {'id': transfer_id, 'vehicleCategoryId': str(self.sedan.id)}
        result = await self.execute(PAY_TRANSFER, {'data': data}, self.customer)
        self.assertEqual(self.error_code(result), 'ERR025')
        data['vehicleCategoryId'] = str(self.van.id)
        result = await self.execute(PAY_TRANSFER, {'data': data}, self.customer)
        payment = self.ok(result, 'payTransfer')
        self.assertEqual(payment['paymentLink'], 'https://pay.test/createPaymentLink')
        transfer = await models.Transfer.objects.aget(pk=transfer_id)
        self.assertEqual(transfer.status, 'PENDING')
        self.assertEqual(transfer.vehicle_category_id, self.van.id)
        self.assertEqual(transfer.price, Decimal('957.50'))
        self.assertEqual(transfer.psp_data['order_amount'], '957.50')
        path, payload = self.async_post.call_args.args
        self.assertEqual(path, '/createPaymentLink')
        self.assertEqual(payload['transaction']['order_amount'], Decimal('957.50'))
        # Choosing again expires the previous link first
        result = await self.execute(PAY_TRANSFER, {'data': data}, self.customer)
        self.ok(result, 'payTransfer')
        self.assertEqual(
            self.psp_paths()[-2:], ['/expirePaymentLink', '/createPaymentLink']
        )

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
        names = [o['vehicleCategory']['name'] for o in updated['vehicleOptions']]
        self.assertEqual(names, ['Van'])
        self.assertEqual(self.psp_paths()[-1], '/expirePaymentLink')

    async def test_payment_return_marks_paid_and_notifies_operators(self):
        transfer = await self.paid_transfer()
        self.assertEqual(transfer.status, 'PAID')
        self.assertEqual(transfer.vehicle_category_id, self.sedan.id)
        payments = models.Transaction.objects.filter(transfer=transfer)
        payment = await payments.aget()
        self.assertEqual(payment.category, 'PAYMENT')
        self.assertEqual(payment.order_amount, Decimal('605.00'))
        self.assertEqual(payment.order_id, 'order_1')
        notifications = models.Notification.objects.filter(action='TRANSFER_PAID')
        user_ids = {n.user_id async for n in notifications}
        self.assertEqual(user_ids, {self.operator.id, self.other_operator.id})
        # A repeated return is processed once
        await self.psp_return({
            'action': 'PAYMENT_RETURN', 'id': str(transfer.id), 'success': True
        })
        self.assertEqual(await payments.acount(), 1)
        self.assertEqual(await notifications.acount(), 2)

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

        def driver(email, category, **kwargs):
            user = User.objects.create(
                email=email, role='DRIVER', first_name='D', last_name='T'
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

    async def available_ids(self, transfer):
        result = await self.execute(
            AVAILABLE_DRIVERS, {'transferId': str(transfer.id)}, self.operator
        )
        drivers = self.ok(result, 'availableDrivers')
        self.assertEqual(drivers['count'], len(drivers['users']))
        return {int(user['id']) for user in drivers['users']}

    async def busy_transfer(self, driver, pickup_time, duration=60):
        return await models.Transfer.objects.acreate(
            customer=self.other_customer, driver=driver, status='ASSIGNED',
            vehicle_category=self.sedan, pickup_time=pickup_time,
            duration=duration, distance='10', passenger_name='X',
            pickup_address='A', pickup_latitude=0, pickup_longitude=0,
            dropoff_address='B', dropoff_latitude=0, dropoff_longitude=0
        )

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
        self.assertEqual(assigned['status'], 'ASSIGNED')
        self.assertEqual(assigned['driverId'], str(self.driver.id))
        self.assertEqual(assigned['vehicleId'], str(self.driver_vehicle.id))
        self.assertEqual(assigned['operatorId'], str(self.operator.id))
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

    async def test_reassign_notifies_previous_driver(self):
        transfer = await self.assigned_transfer()
        data = {'id': str(transfer.id), 'driverId': str(self.other_driver.id)}
        result = await self.execute(ASSIGN_DRIVER, {'data': data}, self.admin)
        self.assertEqual(
            self.ok(result, 'assignDriver')['driverId'], str(self.other_driver.id)
        )
        notifications = models.Notification.objects.filter(
            user=self.driver, action='TRANSFER_CANCELLED'
        )
        self.assertTrue(await notifications.aexists())

    async def test_status_transitions(self):
        transfer = await self.assigned_transfer()
        data = {'id': str(transfer.id), 'status': 'COMPLETED'}
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
        self.assertEqual(user_ids, {self.driver.id, self.operator.id})

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
        url = post.call_args.args[0]
        self.assertEqual(url, 'https://psp.test/createPaymentLink')
        headers = post.call_args.kwargs['headers']
        self.assertEqual(headers['X-Access-Token'], 'test-ms-access-token')


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
        self.assertEqual(receiver_ids, {self.driver.id, self.operator.id})
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
        self.assertEqual(user_ids, {self.driver.id, self.operator.id})


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
        self.assertEqual(message.data['action'], 'TRANSFER_PAID')
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
        self.assertEqual(actions, ['NOTIFICATION', 'TRANSFER_PAID'])
        self.assertEqual(events[1]['transferId'], str(transfer.id))
