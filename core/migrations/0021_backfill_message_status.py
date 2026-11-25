from django.db import migrations
from django.db.models import Exists, F, OuterRef, Q


DISPATCH_ROLES = ('ADMIN', 'STAFF', 'OPERATOR')


def backfill(apps, schema_editor):
    """
    Already seen messages become SEEN (same rule as core/schemas/message.py),
    delivery was not tracked, the others stay SENT.
    """
    Message = apps.get_model('core', 'Message')
    ThroughMessage = apps.get_model('core', 'ThroughMessage')
    seen = ThroughMessage.objects.filter(
        message=OuterRef('pk'), is_seen=True
    ).exclude(receiver=OuterRef('sender'))
    dispatch = Q(sender__role__in=DISPATCH_ROLES)
    by_customer = Q(sender=F('transfer__customer'))
    for messages, readers in (
        (
            Message.objects.filter(~dispatch | by_customer),
            seen.filter(receiver__role__in=DISPATCH_ROLES)
        ),
        (
            Message.objects.filter(dispatch).exclude(by_customer),
            seen.filter(receiver=OuterRef('transfer__customer'))
        ),
    ):
        ids = messages.filter(Exists(readers)).values('pk')
        Message.objects.filter(pk__in=ids).update(status='SEEN')


class Migration(migrations.Migration):
    # Separate from 0020: no data update and schema change in one transaction

    dependencies = [
        ('core', '0020_message_status'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
