"""
Django base settings for COORDINATE.
"""

import os
import sys
import environ
from pathlib import Path

# --- Paths -------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / '.env')


# --- Core --------------------------------------------------------------------

SECRET_KEY = env('DJANGO_SECRET_KEY', default='insecure-dev-key-change-me')

DEBUG = env.bool('DJANGO_DEBUG', default=False)

ALLOWED_HOSTS = env.list('DJANGO_ALLOWED_HOSTS', default=['localhost', '127.0.0.1', 'testserver'])


# --- Apps --------------------------------------------------------------------

INSTALLED_APPS = [
    'accounts',
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',

    # Third party
    'corsheaders',
    'channels',
    'django_htmx',

    # Local
    'coordinate',
]


# --- Middleware ---------------------------------------------------------------

MIDDLEWARE = [
    'django.middleware.gzip.GZipMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'accounts.middleware.SingleSessionMiddleware',
    'accounts.middleware.IdleSessionTimeoutMiddleware',
    'accounts.middleware.ClientIdentityLoggingMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_htmx.middleware.HtmxMiddleware',
    'campus.telemetry.TrafficTelemetryMiddleware',
]


# --- URLs / WSGI / ASGI ------------------------------------------------------

ROOT_URLCONF = 'campus.urls'

WSGI_APPLICATION = 'campus.wsgi.application'
ASGI_APPLICATION = 'campus.asgi.application'


# --- Templates ---------------------------------------------------------------

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'accounts.context_processors.early_launch_context',
            ],
        },
    },
]


# --- Early Launch Mode -------------------------------------------------------
# When enabled, in-person physical verification is waived and students can immediately
# self-declare with their enrollment number & phone number in an isolated temporary database.
# Untrusted self-declared accounts are stored temporarily and will not conflict with real accounts.
# Can be toggled via CLI: python manage.py runserver --early-launch
# Or via environment variable: EARLY_LAUNCH_MODE=True
EARLY_LAUNCH_MODE = (
    '--early-launch' in sys.argv
    or os.environ.get('CAMPUS_EARLY_LAUNCH_MODE', '').lower() in ('true', '1', 'yes')
    or env.bool('EARLY_LAUNCH_MODE', default=False)
)

EARLY_LAUNCH_DB_PATH = BASE_DIR / 'temp_early_access.sqlite3'
GUEST_DEMO_DB_PATH = BASE_DIR / 'guest_demo.sqlite3'


# --- Database ----------------------------------------------------------------

if EARLY_LAUNCH_MODE:
    DATABASES = {
        'default': env.db_url_config(os.environ.get('EARLY_LAUNCH_DATABASE_URL') or f'sqlite:///{EARLY_LAUNCH_DB_PATH}'),
        'permanent': env.db_url_config(os.environ.get('DATABASE_URL') or f'sqlite:///{BASE_DIR}/db.sqlite3'),
        'guest_demo': env.db_url_config(os.environ.get('GUEST_DEMO_DATABASE_URL') or f'sqlite:///{GUEST_DEMO_DB_PATH}'),
    }
else:
    DATABASES = {
        'default': env.db_url_config(os.environ.get('DATABASE_URL') or f'sqlite:///{BASE_DIR}/db.sqlite3'),
        'temp_early_access': env.db_url_config(os.environ.get('EARLY_LAUNCH_DATABASE_URL') or f'sqlite:///{EARLY_LAUNCH_DB_PATH}'),
        'guest_demo': env.db_url_config(os.environ.get('GUEST_DEMO_DATABASE_URL') or f'sqlite:///{GUEST_DEMO_DB_PATH}'),
    }


# --- Channels / Redis --------------------------------------------------------

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [env('REDIS_URL', default='redis://127.0.0.1:6379/0?protocol=2&socket_timeout=15&socket_connect_timeout=5')],
        },
    },
}


# --- Auth / Users ------------------------------------------------------------

AUTH_USER_MODEL = 'accounts.User'

AUTHENTICATION_BACKENDS = [
    'accounts.backends.EnrollmentBackend',
    'django.contrib.auth.backends.ModelBackend',
]

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/login/'


# --- i18n --------------------------------------------------------------------

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True


# --- Static / Media ----------------------------------------------------------

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

# Ensure WhiteNoise serves static files directly via finders when DEBUG=False
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = True
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'


# --- Email -------------------------------------------------------------------

EMAIL_BACKEND = env('EMAIL_BACKEND', default='django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = env('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='noreply.campus.nexus@gmail.com')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
EMAIL_TIMEOUT = env.int('EMAIL_TIMEOUT', default=10)
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='Campus Nexus <noreply.campus.nexus@gmail.com>')


# --- Sessions & Inactivity Expiration -----------------------------------------

# Default in-memory cache for local dev/testing; overridden with RedisCache in prod.py
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'campus-nexus-locmem-cache',
    }
}

# Cached DB Session Engine: In-memory cache reads cut database queries by ~30-40%
# across authenticated endpoints while maintaining persistent write-through to DB.
SESSION_ENGINE = env('DJANGO_SESSION_ENGINE', default='django.contrib.sessions.backends.cached_db')

# Inactivity timeout in seconds (default: 1800s = 30 minutes of idle time)
SESSION_IDLE_TIMEOUT = env.int('SESSION_IDLE_TIMEOUT', default=1800)

# Absolute maximum session lifetime in seconds (default: 86400s = 24 hours)
SESSION_COOKIE_AGE = env.int('SESSION_COOKIE_AGE', default=86400)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'


# --- Messages ----------------------------------------------------------------

from django.contrib.messages import constants as message_constants  # noqa: E402

MESSAGE_TAGS = {
    message_constants.ERROR: 'danger',
}


# --- Proxy & IP Resolution ---------------------------------------------------

TRUST_X_FORWARDED_FOR = env.bool('DJANGO_TRUST_X_FORWARDED_FOR', default=False)
TRUSTED_PROXY_IPS = env.list('DJANGO_TRUSTED_PROXY_IPS', default=['127.0.0.1'])
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')


# --- WebAuthn / Passkeys -----------------------------------------------------

WEBAUTHN_RP_ID = env('WEBAUTHN_RP_ID', default='localhost')
WEBAUTHN_RP_NAME = env('WEBAUTHN_RP_NAME', default='Campus Nexus')
WEBAUTHN_ALLOWED_ORIGINS = env.list(
    'WEBAUTHN_ALLOWED_ORIGINS',
    default=['http://localhost:8000', 'http://127.0.0.1:8000', 'http://testserver']
)


# --- CORS (Cross-Origin Resource Sharing for React Native & Mobile APIs) ------

CORS_ALLOW_ALL_ORIGINS = env.bool('CORS_ALLOW_ALL_ORIGINS', default=False)
CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=[])
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
]


# --- Misc --------------------------------------------------------------------

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# --- Console & Server Request Logging -----------------------------------------

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'channels_server': {
            'format': '%(message)s',
        },
        'standard': {
            'format': '[%(asctime)s] %(levelname)s [%(name)s] %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
        },
        'channels_console': {
            'class': 'logging.StreamHandler',
            'formatter': 'channels_server',
        },
    },
    'loggers': {
        'django.channels.server': {
            'handlers': ['channels_console'],
            'level': 'INFO',
            'propagate': False,
        },
        'daphne': {
            'handlers': ['channels_console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}


# --- WhiteNoise Long-Lived Static Cache Headers --------------------------------
WHITENOISE_MAX_AGE = env.int("WHITENOISE_MAX_AGE", default=31536000)
WHITENOISE_IMMUTABLE_FILE_TEST = lambda path, url: True
