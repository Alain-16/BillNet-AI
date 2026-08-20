from .base import *
from decouple import Csv, config


DEBUG=False

ALLOWED_HOSTS=config("ALLOWED_HOSTS",cast=Csv())
CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())

DATABASES = {
    "default":{
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("DB_NAME"),
        "USER": config("DB_USER"),
        "PASSWORD": config("DB_Password"),
        "HOST": config("DB_HOST"),
        "PORT": int(config("DB_PORT", default=5432)),
        "CONN_MAX_AGE":60,
        "CONN_HEALTH_CHECKS":True,
    }
}

CELERY_TASK_ACKS_LATE= True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=True, cast=bool)
SESSION_COOKIE_SECURE= True
CSRF_COOKIE_SECURE= True
SECURE_HSTS_SECONDS= config("SECURE_HSTS_SECONDS", default=31536000, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS= True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF=True
X_FRAME_OPTIONS= "DENY"

if config("BEHIND_TLS_PROXY", default=False, cast=bool):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Receipt evidence. In the container this is a mounted VOLUME, not image space --
# see the compose file. Anything written inside the image is lost on redeploy.
OBJECT_STORAGE_ROOT = config("OBJECT_STORAGE_ROOT", default="/data/objects")

MALWARE_SCAN_ENABLED = config("MALWARE_SCAN_ENABLED", default=False, cast=bool)

LOGGING["handlers"]["console"] = {          # noqa: F405
    "class": "logging.StreamHandler", "formatter": "verbose",
}
LOGGING["root"] = {"handlers": ["console"], "level": "INFO"} 