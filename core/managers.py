from datetime import timedelta
from django.apps import apps
from django.contrib.auth.base_user import BaseUserManager
from django.db.models import (
    F, Exists, ExpressionWrapper, IntegerField, OuterRef, Q, QuerySet, Value
)
from django.db.models.functions import Greatest


BUSY_STATUSES = ['ACCEPTED', 'STARTED']


def ceil_div(amount, field):
    """ceil(amount / field) with integer division in the database."""
    return ExpressionWrapper(
        (Value(amount) + F(field) - 1) / F(field),
        output_field=IntegerField()
    )


def get_model(name):
    """Get a model from the core app by name."""
    return apps.get_model('core', name)


class UserQuerySet(QuerySet):
    def drivers(self):
        return self.filter(role='DRIVER', is_active=True)

    def available_drivers(self, transfer):
        """
        Active, ACCEPTED (verified) drivers who have a free active vehicle in
        the transfer's vehicle category, are not assigned to this transfer yet
        and have no other busy transfer overlapping its time window.
        """
        vehicles = get_model('Vehicle').objects.suitable_for(transfer)
        vehicles = vehicles.filter(driver_id=OuterRef('pk'))
        overlapping = get_model('Transfer').objects.overlapping(transfer)
        assignments = get_model('TransferDriver').objects.filter(
            Q(transfer__in=overlapping) | Q(transfer_id=transfer.pk),
            driver_id=OuterRef('pk')
        )
        drivers = self.drivers().filter(status='ACCEPTED')
        return drivers.filter(Exists(vehicles)).exclude(Exists(assignments))


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
        kwargs.setdefault('status', 'ACCEPTED')
        return self.create_user(email, password, **kwargs)

    def create_staff(self, email, password=None, **kwargs):
        kwargs['role'] = 'STAFF'
        kwargs.setdefault('status', 'ACCEPTED')
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
    
    def with_counts(self, transfer):
        """
        Active categories that fit the transfer's per-vehicle needs, annotated
        with `count`: how many vehicles of the category the transfer needs to
        carry all passengers and luggage.
        """
        categories = self.filter(
            is_active=True,
            max_passengers__gt=0,
            child_seat_capacity__gte=transfer.child_seat_count
        )
        if transfer.luggage_count:
            categories = categories.filter(max_luggage__gt=0)
        if transfer.needs_wheelchair:
            categories = categories.filter(is_wheelchair_accessible=True)
        counts = [ceil_div(transfer.passenger_count, 'max_passengers')]
        if transfer.luggage_count:
            counts.append(ceil_div(transfer.luggage_count, 'max_luggage'))
        count = Greatest(*counts) if len(counts) > 1 else counts[0]
        return categories.annotate(count=count)

    def options_for(self, transfer, multiple_vehicles):
        """
        Vehicle options of the transfer, annotated with `count`. With multiple
        vehicles (State.allow_multiple_vehicles) a category may need several
        vehicles, otherwise only categories that fit in one vehicle are listed.
        """
        if multiple_vehicles:
            return self.with_counts(transfer)
        one = Value(1, output_field=IntegerField())
        return self.eligible_for(transfer).annotate(count=one)


class VehicleQuerySet(QuerySet):
    def suitable_for(self, transfer):
        """
        Active vehicles of the transfer's category that are not used by this
        transfer or by an overlapping busy transfer.
        """
        overlapping = get_model('Transfer').objects.overlapping(transfer)
        assignments = get_model('TransferDriver').objects.filter(
            Q(transfer__in=overlapping) | Q(transfer_id=transfer.pk),
            vehicle_id=OuterRef('pk')
        )
        return self.filter(
            is_active=True,
            category_id=transfer.vehicle_category_id
        ).exclude(Exists(assignments))

    def available_for(self, transfer):
        drivers = get_model('User').objects.available_drivers(transfer)
        return self.suitable_for(transfer).filter(driver__in=drivers)


class TransferQuerySet(QuerySet):
    def overlapping(self, transfer):
        """
        Busy transfers (other than this one) whose time window, extended by
        DRIVER_BUFFER_MINUTES on both sides, intersects this transfer's.
        """
        state = transfer.state
        driver_buffer_minutes = state.driver_buffer_minutes
        buffer = timedelta(minutes=driver_buffer_minutes)
        return self.filter(
            status__in=BUSY_STATUSES,
            pickup_time__lt=transfer.end_time + buffer,
            end_time__gt=transfer.pickup_time - buffer
        ).exclude(pk=transfer.pk)
