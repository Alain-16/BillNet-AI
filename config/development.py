from .base import *
from decouple import config

DATABASES ={
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('DB_NAME'),
        'USER': config('DB_USER'),
        'PASSWORD': config('DB_PASSWORD'),
        'HOST': config('DB_HOST'),
        'PORT': int(config('DB_PORT', default=5432))
    }
}

CELERY_TASK_ALWAYS_EAGER = True

