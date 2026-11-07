"""Guest Demo Mode for Campus Nexus and COORDINATE.

Provides an isolated, ephemeral guest demonstration mode where:
1. Visitors can explore the platform without authenticating as an NIT student.
2. Real student rides can be browsed, but student identities (names, enrollments, ratings, photos)
   and departure timings are strictly redacted.
3. Visitors can test the ride creation / booking mechanism; created demo rides are stored
   exclusively in an isolated third database (`guest_demo.sqlite3`).
4. Ephemeral demo rides are tagged by session and purged when the session ends or expires.
5. All sensitive endpoints (student directory, student profiles, direct messages, web sockets,
   protected media) are quarantined against guest demo access.
"""

import datetime
from typing import Optional

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.shortcuts import redirect
from django.utils import timezone


def is_guest_demo(request_or_user) -> bool:
    """Return True if the current request or user is operating in Guest Demo Mode."""
    if not request_or_user:
        return False

    # Check HttpRequest session
    session = getattr(request_or_user, 'session', None)
    if session and session.get('is_guest_demo'):
        return True

    # Check User instance
    user = getattr(request_or_user, 'user', request_or_user)
    if getattr(user, 'is_authenticated', False):
        if getattr(user, 'is_guest_demo', False):
            return True
        if getattr(user, 'username', '') == 'GUEST_DEMO':
            return True
        if getattr(user, 'enrollment_number', '') == 'GUEST_DEMO':
            return True

    return False


def ensure_guest_demo_user(using: str = 'default'):
    """Ensure the special GUEST_DEMO user exists in the specified database."""
    from accounts.models import User
    try:
        user = User.objects.using(using).filter(username='GUEST_DEMO').first()
        if not user:
            user = User.objects.using(using).create(
                username='GUEST_DEMO',
                enrollment_number='GUEST_DEMO',
                first_name='Guest',
                last_name='Explorer',
                email='guest.demo@campus.internal',
                phone_number='0000000000',
                is_active=True,
                verified=False,
            )
            user.set_unusable_password()
            user.save(using=using)
        return user
    except Exception:
        return None


def _ensure_guest_demo_schema():
    """Auto-migrate guest_demo database if tables are missing (e.g. on fresh clone)."""
    from django.db import connections
    from django.core.management import call_command
    try:
        connection = connections['guest_demo']
        with connection.cursor() as cursor:
            tables = connection.introspection.table_names(cursor)
            if 'accounts_user' not in tables or 'coordinate_riderequest' not in tables:
                call_command('migrate', database='guest_demo', verbosity=0)
    except Exception:
        pass


def ensure_guest_demo_ready():
    """Prepare guest demo database and prune stale ephemeral demo rides older than 2 hours."""
    from coordinate.models import RideRequest

    _ensure_guest_demo_schema()

    # Ensure user exists in default and guest_demo databases
    demo_user = ensure_guest_demo_user('default')
    ensure_guest_demo_user('guest_demo')

    # Ephemeral garbage collection: delete demo rides older than 2 hours
    try:
        cutoff = timezone.now() - datetime.timedelta(hours=2)
        RideRequest.objects.using('guest_demo').filter(created_at__lt=cutoff).delete()
    except Exception:
        pass

    return demo_user


def cleanup_guest_demo_session(session_key: Optional[str]):
    """Purge all ephemeral rides and associated participants for this guest session."""
    if not session_key:
        return
    try:
        from coordinate.models import RideRequest
        RideRequest.objects.using('guest_demo').filter(demo_session_key=session_key).delete()
    except Exception:
        pass


def enter_guest_demo(request):
    """Entry point for external visitors to explore Campus Nexus in Guest Demo Mode."""
    # Ensure demo database and user readiness
    demo_user = ensure_guest_demo_ready()
    if not demo_user:
        # Fallback to in-memory / local creation if needed
        from accounts.models import User
        demo_user, _ = User.objects.get_or_create(
            username='GUEST_DEMO',
            defaults={
                'enrollment_number': 'GUEST_DEMO',
                'first_name': 'Guest',
                'last_name': 'Explorer',
                'email': 'guest.demo@campus.internal',
                'phone_number': '0000000000',
                'is_active': True,
                'verified': False,
            }
        )

    # Log in as demo user
    auth_login(request, demo_user, backend='django.contrib.auth.backends.ModelBackend')
    request.session['is_guest_demo'] = True
    request.session.modified = True

    messages.info(
        request,
        "Welcome to Campus Nexus Guest Demo! Student personal data and timings are redacted. "
        "Test bookings you create are stored in an isolated, ephemeral sandbox."
    )
    return redirect('ride_list')


def exit_guest_demo(request):
    """Exit guest demo mode and wipe all session-specific ephemeral demo rides."""
    session_key = getattr(request.session, 'session_key', None)
    cleanup_guest_demo_session(session_key)

    auth_logout(request)
    if hasattr(request, 'session'):
        request.session.flush()

    messages.success(request, "You have exited Guest Demo Mode. All ephemeral session data has been purged.")
    return redirect('landing')
