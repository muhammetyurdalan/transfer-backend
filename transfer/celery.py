from celery import Celery
from os import environ


environ.setdefault('DJANGO_SETTINGS_MODULE', 'transfer.settings.local')
app = Celery('transfer')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
