from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0018_reasons'),
    ]

    operations = [
        migrations.RenameField(
            model_name='transfer',
            old_name='price',
            new_name='customer_price',
        ),
        migrations.AlterField(
            model_name='transfer',
            name='customer_price',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='Vehicle price: unit price * vehicle_count', max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='transfer',
            name='cancel_price',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='Cancellation protection (unconditional refund) fee, never refunded on a customer cancellation. 0: standard refund', max_digits=10),
        ),
        migrations.AddField(
            model_name='state',
            name='allow_unconditional_refund',
            field=models.BooleanField(default=False, help_text='Customers may buy a cancellation protection at payment'),
        ),
        migrations.AddField(
            model_name='state',
            name='cancel_price_rate',
            field=models.DecimalField(decimal_places=4, default=Decimal('0.00'), help_text='Share (0-1) of the price paid for the cancellation protection (Transfer.cancel_price)', max_digits=5),
        ),
    ]
