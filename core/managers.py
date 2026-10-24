from datetime import timedelta
from django.apps import apps
from django.conf import settings
from django.contrib.auth.base_user import BaseUserManager
from django.db.models import Exists, OuterRef, QuerySet


# Transfers in these statuses keep their driver busy
BUSY_STATUSES = ['ASSIGNED', 'STARTED']


def get_model(name):
    # models.py imports this module, so models are resolved lazily
    return apps.get_model('core', name)


class UserQuerySet(QuerySet):
    def drivers(self):
        return self.filter(role='DRIVER', is_active=True)

    def available_drivers(self, transfer):
        """
        Active drivers who have an active vehicle in the transfer's vehicle
        category and no other busy transfer overlapping its time window.
        """
        vehicles = get_model('Vehicle').objects.suitable_for(transfer)
        vehicles = vehicles.filter(driver_id=OuterRef('pk'))
        transfers = get_model('Transfer').objects.overlapping(transfer)
        transfers = transfers.filter(driver_id=OuterRef('pk'))
        return self.drivers().filter(Exists(vehicles)).exclude(Exists(transfers))


class UserManager(BaseUserManager.from_queryset(UserQuerySet)):
    def create_user(self, email, password=None, **kwargs):
        if not email:
            raise ValueError('Email is required')
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **kwargs)
        user.set_password(password)
        user.save()
        return user

    def create_superuser(self, email, password=None, **kwargs):
        kwargs['role'] = 'ADMIN'
        return self.create_user(email, password, **kwargs)

    def create_staff(self, email, password=None, **kwargs):
        kwargs['role'] = 'STAFF'
        return self.create_user(email, password, **kwargs)


class VehicleCategoryQuerySet(QuerySet):
    def eligible_for(self, transfer):
        """Active categories that fit the transfer's passengers and needs."""
        categories = self.filter(
            is_active=True,
            max_passengers__gte=transfer.passenger_count,
            max_luggage__gte=transfer.luggage_count,
            child_seat_capacity__gte=transfer.child_seat_count
        )
        if transfer.needs_wheelchair:
            categories = categories.filter(is_wheelchair_accessible=True)
        return categories


class VehicleQuerySet(QuerySet):
    def suitable_for(self, transfer):
        return self.filter(
            is_active=True,
            category_id=transfer.vehicle_category_id
        )

    def available_for(self, transfer):
        drivers = get_model('User').objects.available_drivers(transfer)
        return self.suitable_for(transfer).filter(driver__in=drivers)


class TransferQuerySet(QuerySet):
    def overlapping(self, transfer):
        """
        Busy transfers (other than this one) whose time window, extended by
        DRIVER_BUFFER_MINUTES on both sides, intersects this transfer's.
        """
        buffer = timedelta(minutes=settings.DRIVER_BUFFER_MINUTES)
        return self.filter(
            status__in=BUSY_STATUSES,
            pickup_time__lt=transfer.end_time + buffer,
            end_time__gt=transfer.pickup_time - buffer
        ).exclude(pk=transfer.pk)
