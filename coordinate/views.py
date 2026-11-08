"""Views for the coordinate (ride coordination) app.

URL summary:
  /coordinate/                            → ride_list
  /coordinate/my-rides/                   → my_rides (history and active rides)
  /coordinate/create/                     → create_ride
  /coordinate/<id>/                       → ride_detail
  /coordinate/<id>/join/                  → join_ride
  /coordinate/<id>/leave/                 → leave_ride
  /coordinate/<id>/terminate/             → terminate_ride
  /coordinate/<id>/complete/              → complete_ride
  /coordinate/<id>/kick/<participant_id>/ → kick_participant
  /coordinate/<id>/review/<user_id>/      → review_user
  /coordinate/<id>/message/              → send_message (WS fallback)
"""

import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, F, Prefetch, Q, When
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.template.defaultfilters import date as date_filter
from django.views.decorators.http import require_POST

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from accounts.models import Friendship, User

from .forms import RideMessageForm, RideRequestForm, RideReviewForm
from .models import (
    ParticipantStatus,
    RideConsent,
    RideIncidentReport,
    RideJoinRequest,
    RideMessage,
    RideParticipant,
    RideRequest,
    RideReview,
    RideWaitlist,
)
from .participation import transition_participant_state
from .policies import (
    can_view_ride,
    can_view_ride_contact,
    is_verified_student,
    record_ride_consent,
    revoke_ride_consent,
)
from .services import (
    alter_ride_capacity_service,
    approve_ride_join_request,
    cancel_ride_service,
    decline_ride_join_request,
    kick_passenger_service,
    leave_ride_service,
    promote_waitlist_passenger,
    request_to_join_ride,
)


# ---------------------------------------------------------------------------
# Access helpers
# ---------------------------------------------------------------------------

def _user_has_active_ride(user) -> bool:
    """True if the user has any active or upcoming confirmed ride."""
    from coordinate.services import user_has_active_ride
    return user_has_active_ride(user)


def _user_has_overlapping_ride(user, departure_time, exclude_ride_id=None) -> bool:
    """True if user has another confirmed ride within 2 hours of departure_time."""
    from coordinate.services import user_has_overlapping_ride
    return user_has_overlapping_ride(user, departure_time, exclude_ride_id=exclude_ride_id)


def _are_friends(user1, user2) -> bool:
    """True if user1 and user2 have a Friendship record in either direction."""
    return Friendship.objects.filter(
        Q(user1=user1, user2=user2) | Q(user1=user2, user2=user1)
    ).exists()


def _user_can_see_ride(user, ride, **kwargs) -> bool:
    """Enforce server-authoritative audience comfort and ride visibility via centralized policy."""
    return can_view_ride(user, ride, **kwargs)


def _user_can_join_ride(
    user,
    ride,
    *,
    is_participant: bool = None,
    has_pending_request: bool = None,
    has_overlapping_ride: bool = None,
    is_kicked: bool = None,
    **kwargs,
) -> bool:
    """
    All conditions that must be satisfied to join / request to join:
      1. Ride is OPEN
      2. Departure is in the future
      3. User can see the ride (visibility check)
      4. User is not already a participant
      5. User does not have an overlapping ride (within 2 hours)
      6. User was not kicked from this ride
      7. User does not already have a pending join request
    """
    if ride.status != 'OPEN':
        return False
    if ride.departure_time <= timezone.now():
        return False
    if not _user_can_see_ride(user, ride):
        return False
    if is_participant if is_participant is not None else RideParticipant.objects.filter(ride=ride, user=user).exists():
        return False
    if has_overlapping_ride if has_overlapping_ride is not None else _user_has_overlapping_ride(user, ride.departure_time, exclude_ride_id=ride.id):
        return False
    if is_kicked if is_kicked is not None else ride.kicked_users.filter(id=user.id).exists():
        return False
    if has_pending_request if has_pending_request is not None else RideJoinRequest.objects.filter(ride=ride, user=user, status='PENDING').exists():
        return False
    return True


def _user_can_waitlist(
    user,
    ride,
    *,
    is_participant: bool = None,
    is_waitlisted: bool = None,
    has_overlapping_ride: bool = None,
    is_kicked: bool = None,
    **kwargs,
) -> bool:
    """
    Conditions to join standby waitlist:
      1. User is authenticated
      2. Ride is FULL (or all spots filled)
      3. Departure is in future
      4. User can see the ride
      5. User is not creator and not already a participant
      6. User is not already on waitlist
      7. User does not have an overlapping ride (within 2 hours)
      8. User was not kicked
    """
    if not user or not user.is_authenticated:
        return False
    if ride.status not in ['FULL', 'OPEN'] or not ride.is_full():
        return False
    if ride.departure_time <= timezone.now():
        return False
    if not _user_can_see_ride(user, ride):
        return False
    if ride.creator_id == user.pk:
        return False
    if is_participant if is_participant is not None else RideParticipant.objects.filter(ride=ride, user=user).exists():
        return False
    if is_waitlisted if is_waitlisted is not None else RideWaitlist.objects.filter(ride=ride, user=user).exists():
        return False
    if has_overlapping_ride if has_overlapping_ride is not None else _user_has_overlapping_ride(user, ride.departure_time, exclude_ride_id=ride.id):
        return False
    if is_kicked if is_kicked is not None else ride.kicked_users.filter(id=user.id).exists():
        return False
    return True


def _broadcast_ride_teardown(ride, action: str):
    """Notify all connected WebSocket clients that ride has concluded, closing connections."""
    try:
        channel_layer = get_channel_layer()
        if channel_layer:
            async_to_sync(channel_layer.group_send)(
                f'ride_{ride.id}',
                {
                    'type': 'system.notice',
                    'action': action,
                    'content': f"This ride was marked {action}. Chat session ended.",
                }
            )
    except Exception:
        pass


def _promote_next_waitlist_passenger(ride):
    """Delegate to centralized lifecycle service without synthesizing consent."""
    return promote_waitlist_passenger(ride)


def _parse_date(value: str):
    try:
        return datetime.date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _parse_time(value: str):
    try:
        return datetime.time.fromisoformat(value)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Ride list & My Rides
# ---------------------------------------------------------------------------

@login_required
def ride_list(request):
    """Browse open rides with optional date-time range, ordering, queue filters, and date density."""
    selected_route = request.GET.get('route', '')
    selected_visibility = request.GET.get('visibility', '')
    order_by_param = request.GET.get('order_by', 'time_asc')
    queue_only = request.GET.get('queue_only', '')
    custom_from = request.GET.get('custom_from', '').strip()
    custom_to = request.GET.get('custom_to', '').strip()

    # Date-time range parameters (with backward-compatible fallbacks)
    date_from_str = request.GET.get('date_from', '') or request.GET.get('date', '')
    date_to_str = request.GET.get('date_to', '')
    time_from_str = request.GET.get('time_from', '') or request.GET.get('time', '')
    time_to_str = request.GET.get('time_to', '')

    today = timezone.now().date()
    chosen_date = _parse_date(date_from_str)
    date_to = _parse_date(date_to_str)
    time_from = _parse_time(time_from_str)
    time_to = _parse_time(time_to_str)

    current_tz = timezone.get_current_timezone()

    # Determine date range boundaries
    has_date_filter = bool(chosen_date or date_to or time_from or time_to)
    is_custom_range = bool(chosen_date and date_to and date_to != chosen_date)

    if chosen_date and date_to:
        if date_to < chosen_date:
            date_to = chosen_date
        start_date = chosen_date
        end_date = date_to
    elif chosen_date:
        start_date = chosen_date
        end_date = chosen_date
    elif date_to:
        start_date = today
        end_date = date_to
    else:
        start_date = today
        end_date = None

    start_time_val = time_from or datetime.time(0, 0, 0)
    end_time_val = time_to or datetime.time(23, 59, 59)

    # Base queryset: open and full rides
    rides_qs = (
        RideRequest.objects
        .filter(status__in=['OPEN', 'FULL'])
        .select_related('creator')
        .prefetch_related(
            Prefetch(
                'participants',
                queryset=RideParticipant.objects.filter(status=ParticipantStatus.CONFIRMED).select_related('user'),
            )
        )
        .annotate(participants_total=Count('participants', filter=Q(participants__status='CONFIRMED')))
    )

    if has_date_filter:
        start_dt = timezone.make_aware(datetime.datetime.combine(start_date, start_time_val), current_tz)
        rides_qs = rides_qs.filter(departure_time__gte=start_dt)
        if end_date:
            end_dt = timezone.make_aware(datetime.datetime.combine(end_date, end_time_val), current_tz)
            rides_qs = rides_qs.filter(departure_time__lte=end_dt)
    else:
        # Default: all future rides, un-clamped by default
        rides_qs = rides_qs.filter(departure_time__gte=timezone.now())

    if selected_route:
        rides_qs = rides_qs.filter(route=selected_route)
    if custom_from:
        rides_qs = rides_qs.filter(custom_from_location__icontains=custom_from)
    if custom_to:
        rides_qs = rides_qs.filter(custom_to_location__icontains=custom_to)
    if selected_visibility:
        rides_qs = rides_qs.filter(visibility=selected_visibility)
    if queue_only in ['1', 'true', 'True']:
        rides_qs = rides_qs.filter(status='FULL')

    # Ordering
    if order_by_param == 'time_desc':
        rides_qs = rides_qs.order_by('-departure_time', '-participants_total')
    elif order_by_param == 'spots_desc':
        rides_qs = rides_qs.order_by('-total_spots', 'departure_time')
    elif order_by_param == 'created_desc':
        rides_qs = rides_qs.order_by('-created_at')
    elif order_by_param == 'rating_desc':
        rides_qs = rides_qs.order_by('-creator__on_time_count', 'departure_time')
    else:  # time_asc default
        rides_qs = rides_qs.order_by('departure_time', '-participants_total')

    from accounts.demo import is_guest_demo
    guest_mode = is_guest_demo(request)

    if guest_mode:
        # Exclude FRIENDS-only rides from guest view
        guest_visible_real_rides = [r for r in rides_qs if r.visibility in ['MIXED', 'BOYS', 'GIRLS']]
        for r in guest_visible_real_rides:
            r.is_demo_ride = False
            r.is_redacted = True
            r.spots_left = max(0, r.total_spots - r.participants_total)
            r.has_pending_request = False
            r.is_participant = False
            r.is_waitlisted = False
            r.waitlist_count = 0
            r.pending_request_count = 0
            r.can_join = False
            r.can_waitlist = False
            r.creator_display = "Verified Student"
            r.co_riders_count = r.participants_total

        # Query guest's ephemeral demo rides from third DB
        demo_rides = []
        session_key = getattr(request.session, 'session_key', None)
        if session_key:
            demo_rides = list(
                RideRequest.objects.using('guest_demo')
                .filter(demo_session_key=session_key, status__in=['OPEN', 'FULL'])
                .select_related('creator')
                .prefetch_related(
                    Prefetch(
                        'participants',
                        queryset=RideParticipant.objects.using('guest_demo').filter(status=ParticipantStatus.CONFIRMED).select_related('user'),
                    )
                )
                .annotate(participants_total=Count('participants', filter=Q(participants__status='CONFIRMED')))
                .order_by('-created_at')
            )
            for dr in demo_rides:
                dr.is_demo_ride = True
                dr.is_redacted = False
                dr.spots_left = max(0, dr.total_spots - dr.participants_total)
                dr.has_pending_request = False
                dr.is_participant = True
                dr.is_waitlisted = False
                dr.waitlist_count = 0
                dr.pending_request_count = 0
                dr.can_join = False
                dr.can_waitlist = False
                dr.creator_display = "Guest Explorer (You)"

        visible_rides = demo_rides + guest_visible_real_rides
    else:
        # Precompute privacy and permission sets to eliminate per-ride N+1 queries
        kicked_ids = set()
        part_ids = set()
        friends_ids = set()
        if request.user.is_authenticated:
            kicked_ids = set(
                RideParticipant.all_rows.filter(
                    user=request.user, status=ParticipantStatus.KICKED
                ).values_list('ride_id', flat=True)
            ) | set(request.user.kicked_from_rides.values_list('id', flat=True))
            part_ids = set(
                RideParticipant.objects.filter(
                    user=request.user, status=ParticipantStatus.CONFIRMED
                ).values_list('ride_id', flat=True)
            )
            friends_ids = set(
                Friendship.objects.filter(user1=request.user).values_list('user2_id', flat=True)
            ) | set(
                Friendship.objects.filter(user2=request.user).values_list('user1_id', flat=True)
            )

        # Filter visibility per user with pre-evaluated sets
        visible_rides = [
            r for r in rides_qs
            if _user_can_see_ride(
                request.user,
                r,
                kicked_ride_ids=kicked_ids,
                friend_user_ids=friends_ids,
                participating_ride_ids=part_ids,
            )
        ]

    if not guest_mode:
        # Pre-fetch user state and aggregates to eliminate N+1 queries
        visible_ride_ids = [r.id for r in visible_rides]
        user_authenticated = request.user.is_authenticated
        user_active_ride = _user_has_active_ride(request.user) if user_authenticated else False

        user_participant_ride_ids = set(
            RideParticipant.objects.filter(
                user=request.user,
                ride_id__in=visible_ride_ids,
            ).values_list('ride_id', flat=True)
        ) if user_authenticated else set()

        user_waitlist_ride_ids = set(
            RideWaitlist.objects.filter(
                user=request.user,
                ride_id__in=visible_ride_ids,
            ).values_list('ride_id', flat=True)
        ) if user_authenticated else set()

        user_pending_request_ride_ids = set(
            RideJoinRequest.objects.filter(
                user=request.user,
                ride_id__in=visible_ride_ids,
                status='PENDING',
            ).values_list('ride_id', flat=True)
        ) if user_authenticated else set()

        user_kicked_ride_ids = set(
            request.user.kicked_from_rides.filter(id__in=visible_ride_ids).values_list('id', flat=True)
        ) if user_authenticated else set()

        waitlist_counts = dict(
            RideWaitlist.objects.filter(ride_id__in=visible_ride_ids)
            .values('ride_id')
            .annotate(total=Count('id'))
            .values_list('ride_id', 'total')
        )

        pending_counts = dict(
            RideJoinRequest.objects.filter(
                ride_id__in=visible_ride_ids,
                status='PENDING',
            )
            .values('ride_id')
            .annotate(total=Count('id'))
            .values_list('ride_id', 'total')
        )

        user_confirmed_departures = list(
            RideRequest.objects
            .filter(
                participants__user=request.user,
                participants__status=ParticipantStatus.CONFIRMED,
                status__in=['OPEN', 'FULL'],
            )
            .values_list('id', 'departure_time')
        ) if user_authenticated else []

        for ride in visible_rides:
            ride.spots_left = max(0, ride.total_spots - ride.participants_total)
            ride.has_pending_request = ride.id in user_pending_request_ride_ids
            ride.is_participant = ride.id in user_participant_ride_ids
            ride.is_waitlisted = ride.id in user_waitlist_ride_ids
            ride.waitlist_count = waitlist_counts.get(ride.id, 0)
            ride.pending_request_count = pending_counts.get(ride.id, 0) if ride.creator_id == request.user.id else 0

            # Conflict checking: student already has another confirmed ride within 2 hours
            has_overlap = any(
                abs(ride.departure_time - c_time) < datetime.timedelta(hours=2)
                for c_id, c_time in user_confirmed_departures
                if c_id != ride.id
            )
            ride.has_overlap = has_overlap

            is_kicked = ride.id in user_kicked_ride_ids
            ride.can_join = _user_can_join_ride(
                request.user,
                ride,
                is_participant=ride.is_participant,
                has_pending_request=ride.has_pending_request,
                has_overlapping_ride=has_overlap,
                is_kicked=is_kicked,
            )
            ride.can_waitlist = _user_can_waitlist(
                request.user,
                ride,
                is_participant=ride.is_participant,
                is_waitlisted=ride.is_waitlisted,
                has_overlapping_ride=has_overlap,
                is_kicked=is_kicked,
            )
    else:
        user_participant_ride_ids = {r.id for r in visible_rides if getattr(r, 'is_demo_ride', False)}

    # Density calculation: default 14 days, configurable to 30 days or 3 months (90 days)
    density_view = request.GET.get('density_view', '14').strip().lower()
    if density_view in ['30', '30d', '30days']:
        density_days = 30
        bar_min_width = '18px'
    elif density_view in ['90', '3m', '3months', '90d']:
        density_days = 90
        bar_min_width = '8px'
    else:
        density_view = '14'
        density_days = 14
        bar_min_width = '38px'

    density_raw = (
        RideRequest.objects
        .filter(
            status__in=['OPEN', 'FULL'],
            departure_time__gte=timezone.now(),
            departure_time__lte=timezone.now() + datetime.timedelta(days=density_days),
        )
        .values('departure_time')
    )
    counts_by_date = {}
    for r in density_raw:
        dt = r['departure_time']
        if timezone.is_aware(dt):
            dt = timezone.localtime(dt)
        d_key = dt.date()
        counts_by_date[d_key] = counts_by_date.get(d_key, 0) + 1

    max_density = max(counts_by_date.values()) if counts_by_date else 1
    density_data = []
    for i in range(density_days):
        d = today + datetime.timedelta(days=i)
        c = counts_by_date.get(d, 0)
        pct = max(10, int((c / max(1, max_density)) * 100)) if c > 0 else 6
        density_data.append({
            'date': d,
            'date_str': d.strftime('%Y-%m-%d'),
            'day_name': d.strftime('%a'),
            'day_num': d.strftime('%d'),
            'month_name': d.strftime('%b'),
            'count': c,
            'height_pct': pct,
            'is_today': d == today,
            'is_selected': (chosen_date == d) if chosen_date else False,
            'day_index': i,
        })

    # User's active ride
    if guest_mode:
        has_active_ride = bool(demo_rides)
        my_ride = demo_rides[0] if demo_rides else None
    else:
        has_active_ride = _user_has_active_ride(request.user)
        my_ride = None
        if has_active_ride:
            my_ride_part = (
                RideParticipant.objects
                .filter(user=request.user, ride__status__in=['OPEN', 'FULL'])
                .select_related('ride', 'ride__creator')
                .first()
            )
            if my_ride_part:
                my_ride = my_ride_part.ride

    # Quick-select date strip: dynamically moving window centered around chosen_date
    ref_date = chosen_date or today
    if ref_date < today:
        ref_date = today

    if ref_date > today:
        window_start = max(today, ref_date - datetime.timedelta(days=2))
    else:
        window_start = today

    date_options = []
    for i in range(7):
        d = window_start + datetime.timedelta(days=i)
        date_options.append({
            'date': d,
            'date_str': d.strftime('%Y-%m-%d'),
            'label': d.strftime('%a, %b %d'),
            'day_name': d.strftime('%a'),
            'day_num': d.strftime('%d'),
            'is_selected': d == ref_date and has_date_filter,
            'is_today': d == today,
        })

    prev_date = (ref_date - datetime.timedelta(days=1)) if ref_date > today else None
    next_date = ref_date + datetime.timedelta(days=1)

    template_name = 'coordinate/partials/ride_list_content.html' if request.htmx else 'coordinate/ride_list.html'
    return render(request, template_name, {
        'rides': visible_rides,
        'has_active_ride': has_active_ride,
        'my_ride': my_ride,
        'selected_route': selected_route,
        'selected_visibility': selected_visibility,
        'custom_from': custom_from,
        'custom_to': custom_to,
        'selected_date': chosen_date,
        'date_from': date_from_str,
        'date_to': date_to_str,
        'time_from': time_from_str,
        'time_to': time_to_str,
        'has_date_filter': has_date_filter,
        'is_custom_range': is_custom_range,
        'order_by': order_by_param,
        'queue_only': queue_only in ['1', 'true', 'True'],
        'density_data': density_data,
        'density_view': density_view,
        'density_days': density_days,
        'bar_min_width': bar_min_width,
        'max_density': max_density,
        'date_options': date_options,
        'prev_date': prev_date.strftime('%Y-%m-%d') if prev_date else None,
        'next_date': next_date.strftime('%Y-%m-%d'),
        'route_choices': RideRequest.ROUTE_CHOICES,
        'visibility_choices': RideRequest.VISIBILITY_CHOICES,
    })


@login_required
def my_rides(request):
    """RIDE-03: Dedicated Ride History and active participations interface."""
    from accounts.demo import is_guest_demo
    if is_guest_demo(request):
        session_key = getattr(request.session, 'session_key', None)
        demo_rides = []
        if session_key:
            demo_rides = list(
                RideRequest.objects.using('guest_demo')
                .filter(demo_session_key=session_key)
                .order_by('-departure_time')
            )
        return render(request, 'coordinate/my_rides.html', {
            'active_rides': demo_rides,
            'past_rides': [],
            'created_rides': demo_rides,
            'is_guest_demo': True,
        })

    participations = (
        RideParticipant.objects
        .filter(user=request.user)
        .select_related('ride', 'ride__creator')
        .prefetch_related(
            Prefetch(
                'ride__participants',
                queryset=RideParticipant.objects.filter(status=ParticipantStatus.CONFIRMED).select_related('user'),
            )
        )
        .order_by('-ride__departure_time')
    )

    active_rides = []
    past_rides = []

    for p in participations:
        ride = p.ride
        if ride.status in ['OPEN', 'FULL'] and ride.departure_time > timezone.now() - datetime.timedelta(hours=2):
            active_rides.append(ride)
        else:
            past_rides.append(ride)

    created_rides = (
        RideRequest.objects
        .filter(creator=request.user)
        .order_by('-departure_time')
    )

    return render(request, 'coordinate/my_rides.html', {
        'active_rides': active_rides,
        'past_rides': past_rides,
        'created_rides': created_rides,
    })


# ---------------------------------------------------------------------------
# Create ride
# ---------------------------------------------------------------------------

@login_required
def create_ride(request):
    from accounts.demo import is_guest_demo
    guest_mode = is_guest_demo(request)

    if not guest_mode and not is_verified_student(request.user):
        messages.error(request, 'Only verified students can create rides.')
        return redirect('ride_list')

    active_rides_count = (
        RideRequest.objects.filter(creator=request.user, status__in=['OPEN', 'FULL']).count()
        if not guest_mode
        else RideRequest.objects.using('guest_demo').filter(
            demo_session_key=request.session.session_key,
            status__in=['OPEN', 'FULL'],
        ).count()
    )

    if active_rides_count >= 10:
        messages.error(request, 'Ride creation limit reached: you cannot have more than 10 active rides in the catalogue. Please complete or cancel existing rides to create a new one.')
        return redirect('ride_list')

    if request.method == 'POST':
        form = RideRequestForm(request.POST, user=request.user)
        if form.is_valid():
            visibility = form.cleaned_data.get('visibility')
            dept_time = form.cleaned_data.get('departure_time')

            if not guest_mode:
                # Overlap validation: student cannot host two overlapping rides
                if dept_time and _user_has_overlapping_ride(request.user, dept_time):
                    form.add_error('departure_time', 'You already have another confirmed ride departing within 2 hours of this time. Rides cannot overlap.')
                    return render(request, 'coordinate/create_ride.html', {'form': form})

                # SEC-03: Gender-restricted creator validation
                if visibility == 'GIRLS' and request.user.gender != 'F':
                    form.add_error('visibility', 'Only female students can create Girls Only rides.')
                    return render(request, 'coordinate/create_ride.html', {'form': form})
                if visibility == 'BOYS' and request.user.gender != 'M':
                    form.add_error('visibility', 'Only male students can create Boys Only rides.')
                    return render(request, 'coordinate/create_ride.html', {'form': form})

            if guest_mode:
                if not request.session.session_key:
                    request.session.save()
                from accounts.demo import ensure_guest_demo_user
                demo_user = ensure_guest_demo_user('guest_demo')
                ride = form.save(commit=False)
                ride.creator = demo_user or request.user
                ride.demo_session_key = request.session.session_key
                ride.save(using='guest_demo')
                RideParticipant.objects.using('guest_demo').create(
                    ride=ride,
                    user=demo_user or request.user,
                    status=ParticipantStatus.CONFIRMED,
                )
                messages.success(request, 'Demo ride created! Stored in the ephemeral third database and visible only to your session.')
                return redirect('ride_list')

            with transaction.atomic():
                User.objects.select_for_update().filter(id=request.user.id).first()
                if _user_has_overlapping_ride(request.user, dept_time):
                    messages.error(request, 'You already have another confirmed ride departing within 2 hours of this time.')
                    return redirect('ride_list')
                ride = form.save(commit=False)
                ride.creator = request.user
                ride.save()
                RideParticipant.objects.create(ride=ride, user=request.user)
                disclose_identity = form.cleaned_data.get('disclose_identity', True)
                share_contact = form.cleaned_data.get('share_contact', True)
                record_ride_consent(
                    ride,
                    request.user,
                    request=request,
                    disclose_identity=disclose_identity,
                    share_contact=share_contact,
                )
                ride.sync_status()

            messages.success(request, 'Ride created! Share the details with your classmates.')
            return redirect('ride_list')
    else:
        form = RideRequestForm(user=request.user)

    return render(request, 'coordinate/create_ride.html', {
        'form': form,
        'active_rides_count': active_rides_count,
    })


# ---------------------------------------------------------------------------
# Join / leave / terminate
# ---------------------------------------------------------------------------

@login_required
@require_POST
def join_ride(request, ride_id):
    """
    RIDE-JOIN-REQ: Students submit a join request that requires host approval.
    """
    from accounts.demo import is_guest_demo
    if is_guest_demo(request):
        messages.warning(request, "Guest Demo Mode: You cannot join real student rides.")
        if request.htmx:
            return HttpResponse('<div class="alert alert-warning py-1 px-2 small mb-0"><i class="bi bi-shield-lock me-1"></i>Guest Demo Mode: Joining rides is disabled.</div>')
        return redirect('ride_list')
    disclose_identity = request.POST.get('disclose_identity', 'true').lower() in ('1', 'true', 'on')
    share_contact = request.POST.get('share_contact', 'true').lower() in ('1', 'true', 'on')

    if not share_contact or not disclose_identity:
        messages.error(request, 'Consenting to disclose identity and share contact details with confirmed co-passengers is mandatory to join a ride.')
        if request.htmx:
            ride = get_object_or_404(RideRequest, id=ride_id)
            return render(request, 'coordinate/partials/ride_actions.html', {
                'ride': ride,
                'has_active_ride': _user_has_active_ride(request.user),
            })
        return redirect('ride_detail', ride_id=ride_id)

    ok, msg, details = request_to_join_ride(
        ride_id=ride_id,
        user=request.user,
        request=request,
        disclose_identity=disclose_identity,
        share_contact=share_contact,
    )
    if ok:
        messages.success(request, msg)
    else:
        if details.get("code") == "ALREADY_PENDING":
            messages.info(request, msg)
        else:
            messages.error(request, msg)

    if request.htmx:
        ride = get_object_or_404(RideRequest, id=ride_id)
        has_pending = RideJoinRequest.objects.filter(ride=ride, user=request.user, status='PENDING').exists()
        is_participant = ride.participants.filter(user=request.user).exists()
        return render(request, 'coordinate/partials/ride_actions.html', {
            'ride': ride,
            'joined': is_participant,
            'has_pending': has_pending,
            'has_active_ride': _user_has_active_ride(request.user),
        })

    return redirect('ride_list')


@login_required
@require_POST
def approve_join_request(request, ride_id, request_id):
    """Host approves a pending join request, atomically adding the participant."""
    ok, msg, details = approve_ride_join_request(
        ride_id=ride_id,
        request_id=request_id,
        host_user=request.user,
        request=request,
    )
    if ok:
        messages.success(request, msg)
    else:
        if details.get("code") == "FORBIDDEN":
            return HttpResponseForbidden(msg)
        messages.error(request, msg)
    return redirect('ride_detail', ride_id=ride_id)


@login_required
@require_POST
def decline_join_request(request, ride_id, request_id):
    """Host declines a pending join request."""
    ok, msg, details = decline_ride_join_request(
        ride_id=ride_id,
        request_id=request_id,
        host_user=request.user,
    )
    if ok:
        messages.info(request, msg)
    else:
        if details.get("code") == "FORBIDDEN":
            return HttpResponseForbidden(msg)
        messages.error(request, msg)
    return redirect('ride_detail', ride_id=ride_id)


@login_required
@require_POST
def cancel_join_request(request, ride_id):
    """Student cancels their own pending join request."""
    ride = get_object_or_404(RideRequest, id=ride_id)
    deleted, _ = RideJoinRequest.objects.filter(ride=ride, user=request.user, status='PENDING').delete()
    try:
        transition_participant_state(ride, request.user, ParticipantStatus.CANCELLED)
    except Exception:
        pass
    if deleted:
        messages.info(request, 'Your join request was cancelled.')
    return redirect('ride_list')


@login_required
@require_POST
def leave_ride(request, ride_id):
    ok, msg, details = leave_ride_service(ride_id=ride_id, user=request.user)
    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)

    if request.htmx:
        try:
            ride = RideRequest.objects.get(id=ride_id)
            return render(request, 'coordinate/partials/ride_actions.html', {
                'ride': ride,
                'joined': False,
                'has_active_ride': _user_has_active_ride(request.user),
            })
        except RideRequest.DoesNotExist:
            return HttpResponse(status=204)

    return redirect('ride_list')


@login_required
@require_POST
def terminate_ride(request, ride_id):
    """Creator terminates their own ride. Cancels rather than deletes to preserve history and audit log."""
    ok, msg = cancel_ride_service(ride_id=ride_id, host_user=request.user)
    if ok:
        messages.info(request, msg)
    else:
        messages.error(request, msg)
    return redirect('ride_list')


@login_required
@require_POST
def reschedule_ride(request, ride_id):
    """Host reschedules ride departure time without cancelling it."""
    ride = get_object_or_404(RideRequest, id=ride_id, creator=request.user)

    if ride.status in ['COMPLETED', 'CANCELLED']:
        messages.error(request, 'Completed or cancelled rides cannot be rescheduled.')
        return redirect('ride_detail', ride_id=ride_id)

    if ride.departure_time + datetime.timedelta(hours=2) <= timezone.now():
        messages.error(request, 'Cannot reschedule a ride that has already concluded.')
        return redirect('ride_detail', ride_id=ride_id)

    new_time_raw = request.POST.get('departure_time')
    if not new_time_raw:
        messages.error(request, 'Please provide a new departure date and time.')
        return redirect('ride_detail', ride_id=ride_id)

    try:
        new_dep_time = datetime.datetime.fromisoformat(new_time_raw)
        if timezone.is_naive(new_dep_time):
            new_dep_time = timezone.make_aware(new_dep_time, timezone.get_current_timezone())
    except (ValueError, TypeError):
        messages.error(request, 'Invalid date/time format.')
        return redirect('ride_detail', ride_id=ride_id)

    now = timezone.now()
    if new_dep_time <= now:
        messages.error(request, 'Departure time must be in the future.')
        return redirect('ride_detail', ride_id=ride_id)

    if new_dep_time > now + datetime.timedelta(days=365):
        messages.error(request, 'Cannot schedule more than 1 year in advance.')
        return redirect('ride_detail', ride_id=ride_id)

    ride.departure_time = new_dep_time
    ride.save(update_fields=['departure_time'])

    formatted_time = timezone.localtime(new_dep_time).strftime('%b %d, %Y at %I:%M %p')

    # Record system message in chat
    RideMessage.objects.create(
        ride=ride,
        author=request.user,
        content=f"⏰ Ride Rescheduled: Host updated departure time to {formatted_time}."
    )

    try:
        channel_layer = get_channel_layer()
        if channel_layer:
            async_to_sync(channel_layer.group_send)(
                f'ride_{ride.id}',
                {
                    'type': 'system.notice',
                    'action': 'rescheduled',
                    'departure_time': formatted_time,
                    'content': f"⏰ Departure time was rescheduled to {formatted_time} by host.",
                }
            )
    except Exception:
        pass

    try:
        from accounts.notifications import send_user_notification
        for p in ride.participants.exclude(user=request.user):
            send_user_notification(
                user_id=p.user_id,
                title='Ride Rescheduled',
                content=f'Departure time for ({ride.route_display}) was updated to {formatted_time} by host.',
                url=f'/coordinate/{ride.id}/',
                category='ride_update',
                sender_user=request.user,
                badge='Ride Rescheduled',
                badge_class='bg-info-subtle text-info border border-info-subtle',
                icon='bi-clock-history',
            )
    except Exception:
        pass

    messages.success(request, f'Ride successfully rescheduled to {formatted_time}.')
    return redirect('ride_detail', ride_id=ride_id)


@login_required
@require_POST
def alter_ride_capacity(request, ride_id):
    """Host alters ride total spots (increasing or decreasing capacity)."""
    ride = get_object_or_404(RideRequest, id=ride_id)

    if ride.creator != request.user:
        messages.error(request, 'Only the ride host can alter capacity.')
        return redirect('ride_detail', ride_id=ride_id)

    if ride.status in ['COMPLETED', 'CANCELLED']:
        messages.error(request, 'Completed or cancelled rides cannot be modified.')
        return redirect('ride_detail', ride_id=ride_id)

    try:
        new_capacity = int(request.POST.get('total_spots', ''))
    except (ValueError, TypeError):
        messages.error(request, 'Please provide a valid numeric capacity.')
        return redirect('ride_detail', ride_id=ride_id)

    kick_user_ids = request.POST.getlist('kick_users')
    ok, msg, data = alter_ride_capacity_service(
        ride_id=ride_id,
        host_user=request.user,
        new_capacity=new_capacity,
        kick_user_ids=kick_user_ids,
    )

    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)

    return redirect('ride_detail', ride_id=ride_id)


@login_required
@require_POST
def join_waitlist(request, ride_id):
    """Join the standby queue for a full ride."""
    from accounts.demo import is_guest_demo
    if is_guest_demo(request):
        messages.warning(request, "Guest Demo Mode: You cannot join waitlists for real student rides.")
        return redirect('ride_list')
    ride = get_object_or_404(RideRequest, id=ride_id)
    if not _user_can_waitlist(request.user, ride):
        messages.error(request, 'You cannot join the standby list for this ride.')
        return redirect('ride_list')

    try:
        RideWaitlist.objects.create(ride=ride, user=request.user)
        try:
            transition_participant_state(ride, request.user, ParticipantStatus.WAITLISTED)
        except Exception:
            pass
        pos = ride.waitlist_entries.count()
        messages.success(request, f'Joined standby list (Position #{pos}). You will be automatically added if a spot opens up!')
    except IntegrityError:
        messages.info(request, 'You are already on the standby list for this ride.')

    return redirect('ride_detail', ride_id=ride_id)


@login_required
@require_POST
def leave_waitlist(request, ride_id):
    """Leave the standby queue."""
    ride = get_object_or_404(RideRequest, id=ride_id)
    deleted, _ = RideWaitlist.objects.filter(ride=ride, user=request.user).delete()
    try:
        transition_participant_state(ride, request.user, ParticipantStatus.CANCELLED)
    except Exception:
        pass
    if deleted:
        messages.info(request, 'You have left the standby list.')
    return redirect('ride_list')


# ---------------------------------------------------------------------------
# Ride detail
# ---------------------------------------------------------------------------

@login_required
def ride_detail(request, ride_id):
    from accounts.demo import is_guest_demo
    if is_guest_demo(request):
        session_key = getattr(request.session, 'session_key', None)
        demo_ride = None
        if session_key:
            demo_ride = RideRequest.objects.using('guest_demo').filter(
                id=ride_id,
                demo_session_key=session_key,
            ).select_related('creator').first()

        if not demo_ride:
            messages.error(request, "Live coordination rooms for real student rides are strictly private in Guest Demo Mode.")
            return redirect('ride_list')

        participants = list(
            RideParticipant.objects.using('guest_demo')
            .filter(ride=demo_ride)
            .select_related('user')
            .order_by('joined_at')
        )
        return render(request, 'coordinate/ride_detail.html', {
            'ride': demo_ride,
            'participants': participants,
            'is_creator': True,
            'is_participant': True,
            'is_guest_demo': True,
            'can_message': True,
            'chat_messages': [],
            'waitlist_entries': [],
            'pending_join_requests': [],
            'has_active_ride': True,
        })

    ride = get_object_or_404(
        RideRequest.objects.select_related('creator'),
        id=ride_id,
    )

    is_creator = ride.creator == request.user
    is_participant = RideParticipant.objects.filter(ride=ride, user=request.user).exists()
    is_waitlisted = RideWaitlist.objects.filter(ride=ride, user=request.user).exists()
    has_join_request = RideJoinRequest.objects.filter(ride=ride, user=request.user, status='PENDING').exists()

    if not can_view_ride(request.user, ride):
        raise Http404

    if ride.status in ['COMPLETED', 'CANCELLED'] and not (is_creator or is_participant or is_waitlisted or has_join_request or request.user.is_staff or request.user.is_superuser):
        raise Http404

    participants = list(
        RideParticipant.objects
        .filter(ride=ride)
        .select_related('user')
        .order_by('joined_at')
    )
    for p in participants:
        p.can_view_contact = can_view_ride_contact(request.user, ride, p.user)

    waitlist_entries = (
        RideWaitlist.objects
        .filter(ride=ride)
        .select_related('user')
        .order_by('joined_at')
    )

    pending_join_requests = (
        ride.join_requests
        .filter(status='PENDING')
        .select_related('user')
        .order_by('created_at')
    ) if is_creator else []

    from django.core.cache import cache
    online_user_ids = list(cache.get(f"ride_online_{ride.id}") or set())

    user_participant = next((p for p in participants if p.user == request.user), None)
    if is_creator or ride.share_chat_history:
        chat_messages = (
            RideMessage.objects
            .filter(ride=ride)
            .select_related('author')
            .prefetch_related('read_by')
            .order_by('created_at')
        )
    elif is_participant and user_participant and user_participant.joined_at:
        # Only show messages sent after participant joined
        chat_messages = (
            RideMessage.objects
            .filter(ride=ride, created_at__gte=user_participant.joined_at)
            .select_related('author')
            .prefetch_related('read_by')
            .order_by('created_at')
        )
    else:
        chat_messages = RideMessage.objects.none()

    can_message = (
        is_participant
        and ride.status not in ['COMPLETED', 'CANCELLED']
        and ride.departure_time + datetime.timedelta(hours=2) > timezone.now()
    )

    can_waitlist = _user_can_waitlist(request.user, ride)
    can_join = _user_can_join_ride(request.user, ride)

    user_waitlist_pos = None
    if is_waitlisted:
        entry = RideWaitlist.objects.filter(ride=ride, user=request.user).first()
        if entry:
            user_waitlist_pos = RideWaitlist.objects.filter(ride=ride, joined_at__lte=entry.joined_at).count()

    reviewable_user_ids = set()
    already_reviewed = set(
        RideReview.objects
        .filter(ride=ride, reviewer=request.user)
        .values_list('reviewed_user_id', flat=True)
    )

    if ride.status == 'COMPLETED' and is_participant:
        reviewable_user_ids.update({
            p.user_id for p in participants
            if p.user != request.user and p.user_id not in already_reviewed
        })

    # Immediate reviews for kicked users or users who left early
    for u in ride.kicked_users.all():
        if (is_creator or is_participant) and u != request.user and u.id not in already_reviewed:
            reviewable_user_ids.add(u.id)
        if request.user == u and ride.creator_id not in already_reviewed:
            reviewable_user_ids.add(ride.creator_id)

    for u in ride.left_users.all():
        if (is_creator or is_participant) and u != request.user and u.id not in already_reviewed:
            reviewable_user_ids.add(u.id)
        if request.user == u and ride.creator_id not in already_reviewed:
            reviewable_user_ids.add(ride.creator_id)

    return render(request, 'coordinate/ride_detail.html', {
        'ride': ride,
        'is_creator': is_creator,
        'is_participant': is_participant,
        'is_waitlisted': is_waitlisted,
        'has_join_request': has_join_request,
        'can_waitlist': can_waitlist,
        'can_join': can_join,
        'user_waitlist_pos': user_waitlist_pos,
        'participants': participants,
        'non_host_participants': [p for p in participants if p.user_id != ride.creator_id],
        'confirmed_count': len(participants),
        'waitlist_entries': waitlist_entries,
        'pending_join_requests': pending_join_requests,
        'chat_messages': chat_messages,
        'message_form': RideMessageForm(),
        'can_message': can_message,
        'reviewable_user_ids': reviewable_user_ids,
        'kicked_users': ride.kicked_users.all(),
        'left_users': ride.left_users.all(),
        'online_user_ids': online_user_ids,
    })


# ---------------------------------------------------------------------------
# Kick / complete
# ---------------------------------------------------------------------------

@login_required
@require_POST
def kick_participant(request, ride_id, participant_id):
    participant = get_object_or_404(RideParticipant, id=participant_id, ride_id=ride_id)
    ok, msg = kick_passenger_service(ride_id=ride_id, host_user=request.user, passenger_user=participant.user)
    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect('ride_detail', ride_id=ride_id)


@login_required
@require_POST
def complete_ride(request, ride_id):
    with transaction.atomic():
        # Canonical lock hierarchy: User (ordered by ID ASC) -> RideRequest
        p_uids = list(RideParticipant.objects.filter(ride_id=ride_id).values_list('user_id', flat=True))
        creator_id = RideRequest.objects.filter(id=ride_id).values_list('creator_id', flat=True).first()
        if creator_id:
            p_uids.append(creator_id)
        sorted_uids = sorted(list(set(p_uids)))

        locked_users = {
            u.id: u for u in User.objects.select_for_update().filter(id__in=sorted_uids).order_by('id')
        }

        ride = get_object_or_404(
            RideRequest.objects.select_for_update(),
            id=ride_id,
            creator=request.user,
        )

        if ride.status in ['COMPLETED', 'CANCELLED']:
            messages.error(request, f'Cannot complete a {ride.status.lower()} ride.')
            return redirect('ride_detail', ride_id=ride_id)

        if ride.departure_time > timezone.now():
            messages.error(request, 'Cannot complete a ride before its departure time.')
            return redirect('ride_detail', ride_id=ride_id)

        ride.status = 'COMPLETED'
        ride.save(update_fields=['status'])
        # Bulk cancel lingering pending and waitlisted rows
        now = timezone.now()
        RideParticipant.all_rows.filter(
            ride=ride, status__in=[ParticipantStatus.PENDING, ParticipantStatus.WAITLISTED]
        ).update(status=ParticipantStatus.CANCELLED, is_revoked=True, revoked_at=now)
        transaction.on_commit(lambda: _broadcast_ride_teardown(ride, 'completed'))

        # RIDE-05: Update attendance and reliability statistics only for group rides (>= 2 participants)
        # Prevents solo reputation farming
        participant_users = list(
            RideParticipant.objects
            .filter(ride=ride)
            .select_related('user')
        )
        if len(participant_users) >= 2:
            fifteen_days_ago = timezone.now() - datetime.timedelta(days=15)
            for rp in participant_users:
                user_obj = locked_users.get(rp.user_id, rp.user)
                # Rides completed always increments for verified trips
                user_obj.rides_completed = F('rides_completed') + 1

                # Anti-farming reputation rate limit: on-time reliability bonus capped at 3 boosts per 15-day rolling window
                prior_completed_window = (
                    RideParticipant.objects
                    .filter(
                        user=user_obj,
                        ride__status='COMPLETED',
                        ride__departure_time__gte=fifteen_days_ago
                    )
                    .exclude(ride=ride)
                    .count()
                )
                if prior_completed_window < 3:
                    user_obj.on_time_count = F('on_time_count') + 1
                    user_obj.save(update_fields=['rides_completed', 'on_time_count'])
                else:
                    user_obj.save(update_fields=['rides_completed'])

    messages.success(request, 'Ride marked as completed. You can now review your fellow passengers.')
    return redirect('ride_detail', ride_id=ride_id)


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------

@login_required
def review_user(request, ride_id, user_id):
    ride = get_object_or_404(RideRequest, id=ride_id)
    reviewed_user = get_object_or_404(User, id=user_id)

    if reviewed_user == request.user:
        return HttpResponseForbidden('You cannot review yourself.')

    # Check eligibility:
    # 1. Standard: Ride completed and both are/were participants
    # 2. Immediate grievance: reviewed_user was kicked or left, OR reviewer was kicked/left and reviewing host
    is_kicked_or_left = (
        ride.kicked_users.filter(id=reviewed_user.id).exists()
        or ride.left_users.filter(id=reviewed_user.id).exists()
    )
    reviewer_was_kicked_or_left = (
        ride.kicked_users.filter(id=request.user.id).exists()
        or ride.left_users.filter(id=request.user.id).exists()
    )

    reviewer_was_there = (
        RideParticipant.objects.filter(ride=ride, user=request.user).exists()
        or reviewer_was_kicked_or_left
    )
    reviewed_was_there = (
        RideParticipant.objects.filter(ride=ride, user=reviewed_user).exists()
        or is_kicked_or_left
    )

    is_completed_review = (ride.status == 'COMPLETED' and reviewer_was_there and reviewed_was_there)
    is_immediate_grievance_review = (
        (is_kicked_or_left and reviewer_was_there)
        or (reviewer_was_kicked_or_left and reviewed_user == ride.creator)
    )

    if not (is_completed_review or is_immediate_grievance_review):
        return HttpResponseForbidden('You are not eligible to review this user for this ride at this time.')

    already_reviewed = RideReview.objects.filter(
        ride=ride, reviewer=request.user, reviewed_user=reviewed_user
    ).exists()
    if already_reviewed:
        messages.info(request, 'You have already reviewed this participant.')
        return redirect('ride_detail', ride_id=ride_id)

    if request.method == 'POST':
        form = RideReviewForm(request.POST)
        if form.is_valid():
            review = form.save(commit=False)
            review.ride = ride
            review.reviewer = request.user
            review.reviewed_user = reviewed_user
            review.save()

            update_fields = []
            if review.grievance == 'LATE' or review.punctuality_rating <= 2:
                reviewed_user.late_count = F('late_count') + 1
                reviewed_user.on_time_count = Case(
                    When(on_time_count__gt=0, then=F('on_time_count') - 1),
                    default=0,
                )
                update_fields.extend(['late_count', 'on_time_count'])
            elif review.grievance == 'NO_SHOW':
                reviewed_user.no_show_count = F('no_show_count') + 1
                reviewed_user.on_time_count = Case(
                    When(on_time_count__gt=0, then=F('on_time_count') - 1),
                    default=0,
                )
                update_fields.extend(['no_show_count', 'on_time_count'])

            if review.grievance in ['RUDE', 'SAFETY', 'CUSTOM'] or review.rating <= 2:
                reviewed_user.behavior_reports = F('behavior_reports') + 1
                update_fields.append('behavior_reports')

            if update_fields:
                reviewed_user.save(update_fields=update_fields)

            messages.success(request, 'Review submitted successfully.')
            return redirect('ride_detail', ride_id=ride_id)
    else:
        form = RideReviewForm()

    return render(request, 'coordinate/review_user.html', {
        'form': form,
        'ride': ride,
        'reviewed_user': reviewed_user,
        'is_immediate_grievance': is_immediate_grievance_review,
    })


# ---------------------------------------------------------------------------
# Message (HTTP fallback — real-time is via WebSocket)
# ---------------------------------------------------------------------------

@login_required
@require_POST
def send_message(request, ride_id):
    ride = get_object_or_404(RideRequest, id=ride_id)

    is_participant = RideParticipant.objects.filter(ride=ride, user=request.user).exists()
    if not is_participant or ride.status in ['COMPLETED', 'CANCELLED'] or ride.departure_time + datetime.timedelta(hours=2) <= timezone.now():
        return HttpResponseForbidden('You cannot send messages for this ride.')

    # Rate limiting on HTTP fallback send_message (5 messages per 3s)
    from django.core.cache import cache
    import time
    cache_key = f"http_msg_rl:{request.user.id}"
    now_ts = time.time()
    history = [t for t in (cache.get(cache_key) or []) if now_ts - t < 3.0]
    if len(history) >= 5:
        messages.error(request, 'Message rate limit exceeded. Please wait a moment before sending again.')
        return redirect('ride_detail', ride_id=ride_id)
    history.append(now_ts)
    cache.set(cache_key, history, timeout=5)

    form = RideMessageForm(request.POST)
    if form.is_valid():
        msg = form.save(commit=False)
        msg.ride = ride
        msg.author = request.user
        msg.save()

        # Broadcast to ride Channels group so WebSocket listeners receive it in real-time
        try:
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync
            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    f'ride_{ride.id}',
                    {
                        'type': 'chat.message',
                        'message_id': msg.id,
                        'author': request.user.enrollment_number,
                        'content': msg.content,
                        'timestamp': date_filter(timezone.localtime(msg.created_at), 'M j, H:i'),
                    }
                )
        except Exception:
            pass
    else:
        messages.error(request, 'Message could not be sent.')

    return redirect('ride_detail', ride_id=ride_id)


# ---------------------------------------------------------------------------
# Incident Escalation to Institute Proctorial Board
# ---------------------------------------------------------------------------

@login_required
@require_POST
def report_incident(request, ride_id):
    """File a formal misconduct incident report.
    
    Escalated directly to the Institute Proctorial Board / Dean of Students Welfare.
    Optionally captures the chronological in-ride chat transcript as official evidence.
    """
    import uuid
    from accounts.models import log_audit_event
    from coordinate.models import RideIncidentReport

    ride = get_object_or_404(RideRequest, id=ride_id)
    is_participant = RideParticipant.objects.filter(ride=ride, user=request.user).exists()
    if not is_participant:
        return HttpResponseForbidden("Only confirmed participants in this ride can file an incident report.")

    category = request.POST.get('category', 'OTHER').strip()
    statement = request.POST.get('statement', '').strip()[:5000]
    reported_enrollment = request.POST.get('reported_user', '').strip().upper()
    include_chat = request.POST.get('include_chat_log') in ['on', 'true', '1', True]

    valid_categories = dict(RideIncidentReport.CATEGORY_CHOICES)
    if category not in valid_categories:
        messages.error(request, "Invalid incident report category.")
        return redirect('ride_detail', ride_id=ride_id)

    if not statement:
        messages.error(request, "A detailed statement describing the incident is required.")
        return redirect('ride_detail', ride_id=ride_id)

    reported_user = None
    if reported_enrollment:
        reported_user = User.objects.filter(enrollment_number=reported_enrollment).first()
        valid_targets = [ride.creator] + [p.user for p in ride.participants.select_related('user').all()]
        if reported_user not in valid_targets:
            messages.error(request, "Reported student must be a host or confirmed participant of this ride.")
            return redirect('ride_detail', ride_id=ride_id)

    chat_transcript = ""
    if include_chat:
        messages_qs = RideMessage.objects.filter(ride=ride).select_related('author').order_by('created_at')
        lines = []
        for m in messages_qs:
            lines.append(f"[{timezone.localtime(m.created_at).strftime('%Y-%m-%d %H:%M:%S')}] {m.author.enrollment_number}: {m.content}")
        chat_transcript = "\n".join(lines) if lines else "(No chat messages in this ride session)"

    current_year = timezone.now().year
    reference_id = f"ESC-{current_year}-{uuid.uuid4().hex[:6].upper()}"

    report = RideIncidentReport.objects.create(
        reference_id=reference_id,
        ride=ride,
        reporter=request.user,
        reported_user=reported_user,
        category=category,
        statement=statement,
        include_chat_log=include_chat,
        chat_transcript=chat_transcript,
        escalated_to_institute=True,
        status='ESCALATED',
    )

    log_audit_event(
        event_type="INCIDENT_ESCALATED",
        actor_enrollment=request.user.enrollment_number,
        description=f"Incident {reference_id} filed in Ride #{ride.id} ({report.get_category_display()}) for Proctorial review",
        target_id=reference_id,
    )

    messages.warning(
        request,
        f"Formal incident report filed (Ref: {reference_id}). "
        f"Your statement {'and chat transcript ' if include_chat else ''}have been compiled for the "
        f"Institute Proctorial Board. Platform staff do not mediate disputes internally."
    )
    return redirect('ride_detail', ride_id=ride_id)




@login_required
@require_POST
def toggle_ride_chat_history(request, ride_id):
    """Toggle chat history sharing for later joiners in this ride."""
    ride = get_object_or_404(RideRequest, id=ride_id)
    is_part = RideParticipant.objects.filter(ride=ride, user=request.user).exists()
    if not (ride.creator == request.user or is_part or request.user.is_staff):
        messages.error(request, "Permission denied.")
        return redirect('ride_detail', ride_id=ride.id)

    ride.share_chat_history = not ride.share_chat_history
    ride.save(update_fields=['share_chat_history'])
    status_str = "visible to all participants" if ride.share_chat_history else "restricted to messages sent after joining"
    messages.success(request, f"Chat history is now {status_str}.")
    return redirect('ride_detail', ride_id=ride.id)
