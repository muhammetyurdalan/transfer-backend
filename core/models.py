from datetime import timedelta
from decimal import Decimal
from django.contrib.auth.models import AbstractBaseUser
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from strawberry_django.descriptors import model_property
from typing import Optional
from uuid import uuid4
from core import enums
from core.managers import (
    TransferQuerySet, UserManager, VehicleCategoryQuerySet, VehicleQuerySet
)


ZERO = Decimal('0.00')
DECIMAL_10 = {'max_digits': 10, 'decimal_places': 2}
DECIMAL_10_ZERO = {'max_digits': 10, 'decimal_places': 2, 'default': ZERO}
COORDINATE = {'max_digits': 9, 'decimal_places': 6}


def upload_to(instance, filename):
    folder = instance.__class__.__name__.lower()
    extension = filename.split('.')[-1]
    return f'{folder}/{uuid4()}.{extension}'


class User(AbstractBaseUser):
    role = models.CharField(
        max_length=10,
        choices=enums.Role.choices,
        default=enums.Role.CUSTOMER
    )
    email = models.EmailField(max_length=100, unique=True)
    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    phone = models.CharField(max_length=20, blank=True)
    language = models.CharField(
        max_length=2,
        choices=enums.Language.choices,
        default=enums.Language.EN
    )
    profile_image = models.FileField(upload_to=upload_to, blank=True)
    psp_data = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = UserManager()
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    def __str__(self):
        return f'{self.first_name} {self.last_name}'

    def has_perm(self, perm, obj=None):
        return True

    def has_module_perms(self, app_label):
        return True

    @property
    def is_staff(self):
        return self.role == enums.Role.ADMIN

    @model_property(only=['first_name', 'last_name'])
    def name(self) -> Optional[str]:
        return f'{self.first_name} {self.last_name}'


class Notification(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='notifications'
    )
    title = models.CharField(max_length=100)
    body = models.TextField()
    action = models.CharField(
        max_length=30,
        choices=enums.Action.choices,
        blank=True, null=True
    )
    data = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    is_seen = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['user', 'is_seen'])]

    def __str__(self):
        return f'user_id: {self.user_id}, title: {self.title}'


class VehicleCategory(models.Model):
    name = models.CharField(max_length=50)
    description = models.TextField(blank=True)
    image = models.FileField(upload_to=upload_to, blank=True)
    max_passengers = models.PositiveSmallIntegerField()
    max_luggage = models.PositiveSmallIntegerField()
    child_seat_capacity = models.PositiveSmallIntegerField(default=0)
    is_wheelchair_accessible = models.BooleanField(default=False)
    base_fare = models.DecimalField(**DECIMAL_10_ZERO)
    per_km_price = models.DecimalField(**DECIMAL_10_ZERO)
    per_minute_price = models.DecimalField(**DECIMAL_10_ZERO)
    minimum_fare = models.DecimalField(**DECIMAL_10_ZERO)
    currency = models.CharField(
        max_length=3,
        choices=enums.Currency.choices,
        default=enums.Currency.TRY
    )
    order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = VehicleCategoryQuerySet.as_manager()

    class Meta:
        ordering = ['order', 'id']
        verbose_name_plural = 'vehicle categories'

    def __str__(self):
        return self.name

    def get_price(self, distance, duration):
        price = (
            self.base_fare
            + self.per_km_price * Decimal(distance)
            + self.per_minute_price * Decimal(duration)
        )
        return max(price, self.minimum_fare).quantize(ZERO)


class Vehicle(models.Model):
    driver = models.ForeignKey(
        User,
        related_name='vehicles',
        on_delete=models.CASCADE
    )
    category = models.ForeignKey(
        VehicleCategory,
        related_name='vehicles',
        on_delete=models.PROTECT
    )
    plate = models.CharField(max_length=20, unique=True)
    brand = models.CharField(max_length=50)
    model = models.CharField(max_length=50)
    year = models.PositiveSmallIntegerField(blank=True, null=True)
    color = models.CharField(max_length=30, blank=True)
    image = models.FileField(upload_to=upload_to, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = VehicleQuerySet.as_manager()

    class Meta:
        ordering = ['plate']

    def __str__(self):
        return self.plate


class Transfer(models.Model):
    customer = models.ForeignKey(
        User,
        related_name='transfers',
        on_delete=models.CASCADE
    )
    operator = models.ForeignKey(
        User,
        related_name='operator_transfers',
        on_delete=models.SET_NULL,
        blank=True, null=True
    )
    driver = models.ForeignKey(
        User,
        related_name='driver_transfers',
        on_delete=models.SET_NULL,
        blank=True, null=True
    )
    vehicle_category = models.ForeignKey(
        VehicleCategory,
        related_name='transfers',
        on_delete=models.PROTECT,
        blank=True, null=True
    )
    vehicle = models.ForeignKey(
        Vehicle,
        related_name='transfers',
        on_delete=models.PROTECT,
        blank=True, null=True
    )
    status = models.CharField(
        max_length=10,
        choices=enums.TransferStatus.choices,
        default=enums.TransferStatus.PENDING
    )
    pickup_address = models.CharField(max_length=255)
    pickup_latitude = models.DecimalField(**COORDINATE)
    pickup_longitude = models.DecimalField(**COORDINATE)
    dropoff_address = models.CharField(max_length=255)
    dropoff_latitude = models.DecimalField(**COORDINATE)
    dropoff_longitude = models.DecimalField(**COORDINATE)
    pickup_time = models.DateTimeField()
    end_time = models.DateTimeField(editable=False)
    distance = models.DecimalField(
        max_digits=8, decimal_places=2,
        help_text='Route distance in km'
    )
    duration = models.PositiveIntegerField(help_text='Route duration in minutes')
    passenger_count = models.PositiveSmallIntegerField(default=1)
    luggage_count = models.PositiveSmallIntegerField(default=0)
    child_seat_count = models.PositiveSmallIntegerField(default=0)
    needs_wheelchair = models.BooleanField(default=False)
    passenger_name = models.CharField(max_length=100)
    passenger_phone = models.CharField(max_length=20, blank=True)
    flight_number = models.CharField(max_length=10, blank=True)
    note = models.TextField(blank=True)
    price = models.DecimalField(**DECIMAL_10, blank=True, null=True)
    currency = models.CharField(
        max_length=3,
        choices=enums.Currency.choices,
        blank=True, null=True
    )
    psp_data = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TransferQuerySet.as_manager()

    class Meta:
        ordering = ['-pickup_time', '-id']
        indexes = [
            models.Index(fields=['driver', 'status', 'pickup_time']),
        ]

    def __str__(self):
        return f'id: {self.id}, pickup: {self.pickup_time}'

    def save(self, *args, **kwargs):
        self.end_time = self.pickup_time + timedelta(minutes=self.duration)
        return super().save(*args, **kwargs)


class Transaction(models.Model):
    transfer = models.ForeignKey(
        Transfer,
        related_name='transactions',
        on_delete=models.CASCADE
    )
    user = models.ForeignKey(
        User,
        related_name='transactions',
        on_delete=models.CASCADE
    )
    category = models.CharField(
        max_length=10,
        choices=enums.TransactionCategory.choices
    )
    parent = models.ForeignKey(
        'self',
        related_name='refunds',
        on_delete=models.CASCADE,
        blank=True, null=True
    )
    order_amount = models.DecimalField(**DECIMAL_10)
    currency = models.CharField(max_length=3, choices=enums.Currency.choices)
    order_id = models.CharField(max_length=100, blank=True)
    order_time = models.DateTimeField()
    note = models.TextField(blank=True)

    class Meta:
        ordering = ['-order_time', '-id']

    def __str__(self):
        return f'transfer_id: {self.transfer_id}, category: {self.category}'


class Message(models.Model):
    transfer = models.ForeignKey(
        Transfer,
        related_name='messages',
        on_delete=models.CASCADE
    )
    sender = models.ForeignKey(
        User,
        related_name='sent_messages',
        on_delete=models.CASCADE
    )
    receivers = models.ManyToManyField(
        User,
        related_name='received_messages',
        through='ThroughMessage'
    )
    parent = models.ForeignKey(
        'self',
        related_name='replies',
        on_delete=models.CASCADE,
        blank=True, null=True
    )
    time = models.DateTimeField(auto_now_add=True)
    message = models.TextField(blank=True)
    location = models.TextField(blank=True)
    file = models.FileField(upload_to=upload_to, blank=True)
    is_deleted = models.BooleanField(default=False)
    deleted_time = models.DateTimeField(blank=True, null=True)
    deleted_by = models.ForeignKey(
        User,
        related_name='deleted_messages',
        on_delete=models.SET_NULL,
        blank=True, null=True
    )

    class Meta:
        ordering = ['time', 'id']

    def __str__(self):
        return f'transfer_id: {self.transfer_id}, id: {self.id}'


class ThroughMessage(models.Model):
    transfer = models.ForeignKey(
        Transfer,
        related_name='through_messages',
        on_delete=models.CASCADE
    )
    message = models.ForeignKey(
        Message,
        related_name='through_messages',
        on_delete=models.CASCADE
    )
    receiver = models.ForeignKey(
        User,
        related_name='through_messages',
        on_delete=models.CASCADE
    )
    is_seen = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['message', 'receiver'],
                name='unique_message_receiver'
            )
        ]
