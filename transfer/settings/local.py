from transfer.settings.base import *


# Security Settings

DEBUG = True
ALLOWED_HOSTS = ['*']
CSRF_TRUSTED_ORIGINS = ['http://localhost:8000', 'http://127.0.0.1:8000']


# Channels: no Redis needed locally

CHANNEL_LAYERS = {
    'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}
}


# Celery: tasks run inline, no broker needed locally

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True


# Email: printed to console

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
