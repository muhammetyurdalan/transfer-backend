from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from modeltranslation.admin import TranslationAdmin
from core.models import (
    City, District, Leg, Message, Notification, State, ThroughMessage,
    Transaction, Transfer, User, Vehicle, VehicleCategory
)


class UserCreateForm(forms.ModelForm):
    password1 = forms.CharField(label='Password', widget=forms.PasswordInput)
    password2 = forms.CharField(
        label='Password confirmation',
        widget=forms.PasswordInput
    )

    class Meta:
        model = User
        fields = ['email', 'role', 'first_name', 'last_name']

    def clean_email(self):
        return User.objects.normalize_email(self.cleaned_data['email']).lower()

    def clean_password2(self):
        password1 = self.cleaned_data.get('password1')
        password2 = self.cleaned_data.get('password2')
        if password1 and password2 and password1 != password2:
            raise ValidationError('Passwords do not match')
        return password2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])
        if commit:
            user.save()
        return user


class UserUpdateForm(forms.ModelForm):
    password = ReadOnlyPasswordHashField()

    class Meta:
        model = User
        fields = [
            'email', 'password', 'role', 'first_name', 'last_name', 'phone',
            'language', 'profile_image', 'psp_data', 'is_active'
        ]


class UserAdmin(BaseUserAdmin):
    form = UserUpdateForm
    add_form = UserCreateForm
    list_display = [
        'id', 'email', 'role', 'first_name', 'last_name', 'is_active'
    ]
    list_filter = ['role', 'is_active']
    fieldsets = [
        (
            None,
            {
                'fields': [
                    'email', 'password', 'role', 'first_name', 'last_name',
                    'phone', 'language', 'profile_image', 'psp_data',
                    'is_active'
                ]
            }
        )
    ]
    add_fieldsets = [
        (
            None,
            {
                'classes': ['wide'],
                'fields': [
                    'email', 'role', 'first_name', 'last_name',
                    'password1', 'password2'
                ],
            },
        )
    ]
    search_fields = ['email', 'first_name', 'last_name']
    ordering = ['email']
    filter_horizontal = []


admin.site.register(User, UserAdmin)
admin.site.unregister(Group)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['id', 'user_id', 'title', 'action', 'is_seen', 'created_at']
    list_filter = ['action', 'is_seen']
    search_fields = ['title', 'user__email']
    raw_id_fields = ['user']


@admin.register(State)
class StateAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'code']
    search_fields = ['name', 'code']


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'state']
    list_filter = ['state']
    search_fields = ['name']


@admin.register(District)
class DistrictAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'city', 'is_active']
    list_filter = ['is_active', 'city__state']
    search_fields = ['name', 'city__name']
    raw_id_fields = ['city']


@admin.register(VehicleCategory)
class VehicleCategoryAdmin(TranslationAdmin):
    list_display = [
        'id', 'name', 'max_passengers', 'max_luggage', 'base_fare',
        'per_km_price', 'per_minute_price', 'minimum_fare', 'currency',
        'order', 'is_active'
    ]
    list_filter = ['is_active', 'currency', 'is_wheelchair_accessible']
    search_fields = ['name']


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'plate', 'brand', 'model', 'category', 'driver', 'is_active'
    ]
    list_filter = ['is_active', 'category']
    search_fields = ['plate', 'brand', 'model', 'driver__email']
    raw_id_fields = ['driver']


class LegInline(admin.TabularInline):
    model = Leg
    extra = 0


@admin.register(Transfer)
class TransferAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'customer_id', 'operator_id', 'driver_id', 'vehicle_category',
        'status', 'pickup_time', 'price', 'currency'
    ]
    list_filter = ['status', 'vehicle_category', 'state']
    search_fields = [
        'passenger_name', 'flight_number', 'customer__email',
        'pickup_address', 'dropoff_address'
    ]
    raw_id_fields = [
        'customer', 'operator', 'driver', 'vehicle', 'pickup_district',
        'dropoff_district'
    ]
    inlines = [LegInline]


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'transfer_id', 'user_id', 'category', 'order_amount',
        'currency', 'order_time'
    ]
    list_filter = ['category']
    search_fields = ['order_id', 'user__email']
    raw_id_fields = ['transfer', 'user', 'parent']


class ThroughMessageInline(admin.TabularInline):
    model = ThroughMessage
    raw_id_fields = ['transfer', 'receiver']
    extra = 0


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ['id', 'transfer_id', 'sender_id', 'time', 'is_deleted']
    list_filter = ['is_deleted']
    raw_id_fields = ['transfer', 'sender', 'parent', 'deleted_by']
    inlines = [ThroughMessageInline]
