"""
Django base settings for COORDINATE.
"""

import os
import sys
import environ
from pathlib import Path

# --- Paths -------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DJANGO_SECRET_KEY=(str, 'insecure-dev-key-change-me'),
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, ['localhost', '127.0.0.1', 'testserver']),
    DATABASE_URL=(str, 'postgres://campus_admin:CampusPass123!@127.0.0.1:5432/campus_nexus'),
    GUEST_DEMO_DATABASE_URL=(str, 'postgres://campus_admin:CampusPass123!@127.0.0.1:5432/campus_guest_demo'),
    REDIS_URL=(str, 'redis://127.0.0.1:6379/0?protocol=2&socket_timeout=15&socket_connect_timeout=5'),
    EMAIL_BACKEND=(str, 'django.core.mail.backends.smtp.EmailBackend'),
    EMAIL_HOST=(str, 'smtp.gmail.com'),
    EMAIL_PORT=(int, 587),
    EMAIL_USE_TLS=(bool, True),
    EMAIL_HOST_USER=(str, 'noreply.campus.nexus@gmail.com'),
    EMAIL_HOST_PASSWORD=(str, ''),
    EMAIL_TIMEOUT=(int, 10),
    DEFAULT_FROM_EMAIL=(str, 'Campus Nexus <noreply.campus.nexus@gmail.com>'),
    DJANGO_SESSION_ENGINE=(str, 'django.contrib.sessions.backends.cached_db'),
    SESSION_IDLE_TIMEOUT=(int, 1800),
    SESSION_COOKIE_AGE=(int, 86400),
    DJANGO_TRUST_X_FORWARDED_FOR=(bool, False),
    DJANGO_TRUSTED_PROXY_IPS=(list, ['127.0.0.1']),
    WEBAUTHN_RP_ID=(str, 'localhost'),
    WEBAUTHN_RP_NAME=(str, 'Campus Nexus'),
    WEBAUTHN_ALLOWED_ORIGINS=(list, ['http://localhost:8000', 'http://127.0.0.1:8000', 'http://testserver']),
    CORS_ALLOW_ALL_ORIGINS=(bool, False),
    CORS_ALLOWED_ORIGINS=(list, []),
)
environ.Env.read_env(BASE_DIR / '.env')


# --- Core --------------------------------------------------------------------

SECRET_KEY = env.str('DJANGO_SECRET_KEY')

DEBUG = env.bool('DJANGO_DEBUG')

ALLOWED_HOSTS = env.list('DJANGO_ALLOWED_HOSTS')


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
    'accounts.middleware.CampusSecurityMiddleware',
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

_DEFAULT_DB_URL = env.str('DATABASE_URL')
_GUEST_DEMO_DB_URL = env.str('GUEST_DEMO_DATABASE_URL')

DATABASES = {
    'default': env.db_url_config(_DEFAULT_DB_URL),
    'permanent': env.db_url_config(_DEFAULT_DB_URL),
    'guest_demo': env.db_url_config(_GUEST_DEMO_DB_URL),
}


# --- Channels / Redis --------------------------------------------------------

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [env.str('REDIS_URL')],
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

EMAIL_BACKEND = env.str('EMAIL_BACKEND')
EMAIL_HOST = env.str('EMAIL_HOST')
EMAIL_PORT = env.int('EMAIL_PORT')
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS')
EMAIL_HOST_USER = env.str('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = env.str('EMAIL_HOST_PASSWORD')
EMAIL_TIMEOUT = env.int('EMAIL_TIMEOUT')
DEFAULT_FROM_EMAIL = env.str('DEFAULT_FROM_EMAIL')


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
SESSION_ENGINE = env.str('DJANGO_SESSION_ENGINE')

# Inactivity timeout in seconds (default: 1800s = 30 minutes of idle time)
SESSION_IDLE_TIMEOUT = env.int('SESSION_IDLE_TIMEOUT')

# Absolute maximum session lifetime in seconds (default: 86400s = 24 hours)
SESSION_COOKIE_AGE = env.int('SESSION_COOKIE_AGE')
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'


# --- Messages ----------------------------------------------------------------

from django.contrib.messages import constants as message_constants  # noqa: E402

MESSAGE_TAGS = {
    message_constants.ERROR: 'danger',
}


# --- Proxy & IP Resolution ---------------------------------------------------

TRUST_X_FORWARDED_FOR = env.bool('DJANGO_TRUST_X_FORWARDED_FOR')
TRUSTED_PROXY_IPS = env.list('DJANGO_TRUSTED_PROXY_IPS')
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')


# --- WebAuthn / Passkeys -----------------------------------------------------

WEBAUTHN_RP_ID = env.str('WEBAUTHN_RP_ID')
WEBAUTHN_RP_NAME = env.str('WEBAUTHN_RP_NAME')
WEBAUTHN_ALLOWED_ORIGINS = env.list('WEBAUTHN_ALLOWED_ORIGINS')


# --- CORS (Cross-Origin Resource Sharing for React Native & Mobile APIs) ------

CORS_ALLOW_ALL_ORIGINS = env.bool('CORS_ALLOW_ALL_ORIGINS')
CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS')
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

# --- Security Headers, HSTS, SSL Redirect & Cookie Hardening -----------------
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_HSTS_SECONDS = env.int('DJANGO_SECURE_HSTS_SECONDS', default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool('DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS', default=True)
SECURE_HSTS_PRELOAD = env.bool('DJANGO_SECURE_HSTS_PRELOAD', default=True)
SECURE_SSL_REDIRECT = env.bool('DJANGO_SECURE_SSL_REDIRECT', default=True)
SECURE_REDIRECT_EXEMPT = [
    r'^robots\.txt$',
    r'^llms\.txt$',
    r'^\.well-known/',
]

# Cookie Security & Hardening
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = env.bool('DJANGO_SESSION_COOKIE_SECURE', default=True)

CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SECURE = env.bool('DJANGO_CSRF_COOKIE_SECURE', default=True)
