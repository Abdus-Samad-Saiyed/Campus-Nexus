from .base import *  # noqa: F401, F403
import os
import sys

# Production default: DEBUG is strictly False unless explicitly enabled via environment variable
DEBUG = os.environ.get('DJANGO_DEBUG', 'False').lower() in ('true', '1')

# Import local untracked development secrets if present
try:
    from . import secrets_local
    for attr in dir(secrets_local):
        if not attr.startswith('_'):
            val = getattr(secrets_local, attr)
            # Environment variables take precedence if already populated
            if attr == 'DJANGO_SECRET_KEY' and not os.environ.get('DJANGO_SECRET_KEY'):
                SECRET_KEY = val
            elif attr == 'EMAIL_HOST_PASSWORD' and not os.environ.get('EMAIL_HOST_PASSWORD'):
                EMAIL_HOST_PASSWORD = val
            elif attr == 'EMAIL_HOST_USER' and not os.environ.get('EMAIL_HOST_USER'):
                EMAIL_HOST_USER = val
            elif not hasattr(globals(), attr) or getattr(globals(), attr) == '':
                globals()[attr] = val
except ImportError:
    pass

# Safe development fallback for SECRET_KEY if still empty
if not SECRET_KEY or SECRET_KEY == 'insecure-dev-key-change-me':
    SECRET_KEY = 'insecure-dev-only-secret-key-for-local-testing'

# Redis Channel Layer for multi-worker WebSockets and real-time messaging
# If Redis is running on localhost:6379 or specified via REDIS_URL, activate RedisChannelLayer.
# Otherwise, safely fallback to InMemoryChannelLayer for standalone dev.
_redis_url = os.environ.get('REDIS_URL', 'redis://127.0.0.1:6379/0')
_use_redis = False

try:
    import socket
    _s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _s.settimeout(0.1)
    _s.connect(('127.0.0.1', 6379))
    _s.close()
    _use_redis = True
except Exception:
    _use_redis = bool(os.environ.get('REDIS_URL'))

if _use_redis:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {
                'hosts': [_redis_url],
            },
        },
    }
else:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels.layers.InMemoryChannelLayer',
        },
    }

# Tune PBKDF2 Password Hasher Iterations for Dev / Staging / Load Testing
# Cuts login CPU duration from ~500ms down to ~5-10ms per auth operation,
# preventing single-core CPU saturation during concurrent login bursts while
# strictly preserving PBKDF2 verification compatibility for existing hashes.
from django.contrib.auth.hashers import PBKDF2PasswordHasher


class FastPBKDF2PasswordHasher(PBKDF2PasswordHasher):
    iterations = int(os.environ.get('DJANGO_PBKDF2_ITERATIONS', 10000))


PASSWORD_HASHERS = [
    'campus.settings.dev.FastPBKDF2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher',
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.BCryptSHA256PasswordHasher',
    'django.contrib.auth.hashers.ScryptPasswordHasher',
]

# Email configuration:
# Uses SMTP if EMAIL_HOST_PASSWORD is provided, otherwise falls back to console in dev.
if EMAIL_HOST_PASSWORD and EMAIL_HOST_USER:
    EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
else:
    EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')

# Allow access across local subnets, hotspots, and dev tunnels in dev mode
_env_hosts = os.environ.get('DJANGO_ALLOWED_HOSTS', '')
if _env_hosts:
    _configured_hosts = [h.strip() for h in _env_hosts.split(',') if h.strip()]
else:
    _configured_hosts = ['*']

if '*' in _configured_hosts:
    ALLOWED_HOSTS = ['*']
else:
    # Always ensure wildcard and common dev tunnels are permitted in dev mode
    ALLOWED_HOSTS = list(dict.fromkeys(
        list(_configured_hosts) + [
            '*',
            'localhost',
            '127.0.0.1',
            '.trycloudflare.com',
            '.ngrok-free.app',
            '.pinggy.link',
        ]
    ))
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

_env_origins = os.environ.get('CSRF_TRUSTED_ORIGINS', '')
if _env_origins:
    CSRF_TRUSTED_ORIGINS = [o.strip() for o in _env_origins.split(',') if o.strip()]
else:
    CSRF_TRUSTED_ORIGINS = [
        'http://localhost:8000',
        'http://127.0.0.1:8000',
        'http://*.local',
        'http://10.*',
        'http://172.16.*',
        'http://172.17.*',
        'http://172.18.*',
        'http://172.19.*',
        'http://172.20.*',
        'http://172.21.*',
        'http://172.22.*',
        'http://172.23.*',
        'http://172.24.*',
        'http://172.25.*',
        'http://172.26.*',
        'http://172.27.*',
        'http://172.28.*',
        'http://172.29.*',
        'http://172.30.*',
        'http://172.31.*',
        'http://192.168.*',
        'https://*.trycloudflare.com',
        'http://*.trycloudflare.com',
        'https://*.ngrok-free.app',
        'http://*.ngrok-free.app',
        'https://*.pinggy.link',
        'http://*.pinggy.link',
        'https://campus-nexus.online',
        'http://campus-nexus.online',
        'https://*.campus-nexus.online',
        'http://*.campus-nexus.online',
    ]

# Early Launch Mode override from CLI flag or environment
GUEST_DEMO_DB_PATH = BASE_DIR / 'guest_demo.sqlite3'

if (
    '--early-launch' in sys.argv
    or os.environ.get('CAMPUS_EARLY_LAUNCH_MODE', '').lower() in ('true', '1', 'yes')
    or os.environ.get('EARLY_LAUNCH_MODE', '').lower() in ('true', '1', 'yes')
):
    EARLY_LAUNCH_MODE = True
    EARLY_LAUNCH_DB_PATH = BASE_DIR / 'temp_early_access.sqlite3'
    DATABASES = {
        'default': env.db_url_config(os.environ.get('EARLY_LAUNCH_DATABASE_URL') or f'sqlite:///{EARLY_LAUNCH_DB_PATH}'),
        'permanent': env.db_url_config(os.environ.get('DATABASE_URL') or f'sqlite:///{BASE_DIR}/db.sqlite3'),
        'guest_demo': env.db_url_config(os.environ.get('GUEST_DEMO_DATABASE_URL') or f'sqlite:///{GUEST_DEMO_DB_PATH}'),
    }
else:
    # Always guarantee guest_demo database is configured in all developer environments
    DATABASES['guest_demo'] = env.db_url_config(os.environ.get('GUEST_DEMO_DATABASE_URL') or f'sqlite:///{GUEST_DEMO_DB_PATH}')
