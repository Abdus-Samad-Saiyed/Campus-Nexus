"""
Cryptographically secure Two-Factor Authentication (2FA) & WebAuthn / Passkey Engine.
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import timedelta
from typing import Optional, Tuple

from django.conf import settings
from django.utils import timezone

from .models import PasskeyCredential, PhoneOTP, User

import re
from django.db import transaction
from django.db.models import F

logger = logging.getLogger(__name__)

OTP_EXPIRY_SECONDS = 300       # 5 minutes
RESEND_COOLDOWN_SECONDS = 60   # 1 minute
MAX_ATTEMPTS = 3

import threading
_ACTIVE_OTP_LOCK = threading.Lock()
_ACTIVE_OTP_REGISTRY = {}  # key -> (raw_code, expire_timestamp)

_OTP_SEND_CIRCUIT_CACHE_KEY = 'otp_dispatch_circuit_open_until'
_OTP_CIRCUIT_DURATION_SECONDS = 18000  # 5 hours


def is_otp_send_circuit_open() -> bool:
    """Check if the 5-hour OTP external dispatch circuit breaker is currently active."""
    from django.core.cache import cache
    until = cache.get(_OTP_SEND_CIRCUIT_CACHE_KEY)
    if until and time.time() < until:
        return True
    return False


def trip_otp_send_circuit(duration_seconds: int = _OTP_CIRCUIT_DURATION_SECONDS):
    """Trip 5-hour circuit breaker after dispatch failure, pausing external SMS/SMTP relays."""
    from django.core.cache import cache
    until = time.time() + duration_seconds
    cache.set(_OTP_SEND_CIRCUIT_CACHE_KEY, until, timeout=duration_seconds)
    logger.warning("[OTP CIRCUIT BREAKER] Tripped for %d seconds (5 hours). External SMS/SMTP relay paused until %s.", duration_seconds, time.ctime(until))



def store_active_otp(
    raw_code: str,
    user: User = None,
    phone_number: str = None,
    email: str = None,
    purpose: str = '2FA',
    expires_seconds: int = OTP_EXPIRY_SECONDS,
):
    """Cache the active plain-text OTP code across Django cache and in-memory registry.
    Enables console printing alongside requests when OTP limits are reached.
    """
    from django.core.cache import cache
    now = time.time()
    exp_ts = now + expires_seconds
    target_phone = normalize_phone_number(phone_number or (user.phone_number if user else None))
    target_email = (email or (user.email if user else None) or '').strip().lower()

    keys = []
    if user:
        if getattr(user, 'id', None):
            keys.append(f"otp_raw:user:{user.id}:{purpose}")
            keys.append(f"otp_raw:user:{user.id}:ANY")
        if getattr(user, 'enrollment_number', None):
            keys.append(f"otp_raw:enrollment:{user.enrollment_number}:{purpose}")
            keys.append(f"otp_raw:enrollment:{user.enrollment_number}:ANY")
    if target_phone:
        keys.append(f"otp_raw:phone:{target_phone}:{purpose}")
        keys.append(f"otp_raw:phone:{target_phone}:ANY")
    if target_email:
        keys.append(f"otp_raw:email:{target_email}:{purpose}")
        keys.append(f"otp_raw:email:{target_email}:ANY")

    with _ACTIVE_OTP_LOCK:
        dead = [k for k, v in _ACTIVE_OTP_REGISTRY.items() if v[1] < now]
        for k in dead:
            _ACTIVE_OTP_REGISTRY.pop(k, None)
        for k in keys:
            _ACTIVE_OTP_REGISTRY[k] = (raw_code, exp_ts)

    for k in keys:
        try:
            cache.set(k, raw_code, timeout=expires_seconds)
        except Exception:
            pass


def clear_active_otp(
    user: User = None,
    phone_number: str = None,
    email: str = None,
    purpose: str = '2FA',
):
    """Purge active OTP from in-memory registry and Django cache upon verification or expiry."""
    from django.core.cache import cache
    target_phone = normalize_phone_number(phone_number or (user.phone_number if user else None))
    target_email = (email or (user.email if user else None) or '').strip().lower()

    keys = []
    if user:
        if getattr(user, 'id', None):
            keys.append(f"otp_raw:user:{user.id}:{purpose}")
            keys.append(f"otp_raw:user:{user.id}:ANY")
        if getattr(user, 'enrollment_number', None):
            keys.append(f"otp_raw:enrollment:{user.enrollment_number}:{purpose}")
            keys.append(f"otp_raw:enrollment:{user.enrollment_number}:ANY")
    if target_phone:
        keys.append(f"otp_raw:phone:{target_phone}:{purpose}")
        keys.append(f"otp_raw:phone:{target_phone}:ANY")
    if target_email:
        keys.append(f"otp_raw:email:{target_email}:{purpose}")
        keys.append(f"otp_raw:email:{target_email}:ANY")

    with _ACTIVE_OTP_LOCK:
        for k in keys:
            _ACTIVE_OTP_REGISTRY.pop(k, None)

    for k in keys:
        try:
            cache.delete(k)
        except Exception:
            pass


def get_active_otp(
    user: User = None,
    phone_number: str = None,
    email: str = None,
    purpose: str = '2FA',
) -> Optional[str]:
    """Retrieve currently active raw 6-digit OTP code for the given target and purpose.
    Checks in-memory registry, Django cache, and performs fallback hash recovery if needed.
    """
    from django.core.cache import cache
    target_phone = normalize_phone_number(phone_number or (user.phone_number if user else None))
    target_email = (email or (user.email if user else None) or '').strip().lower()

    keys = []
    if user:
        if getattr(user, 'id', None):
            keys.append(f"otp_raw:user:{user.id}:{purpose}")
            keys.append(f"otp_raw:user:{user.id}:ANY")
        if getattr(user, 'enrollment_number', None):
            keys.append(f"otp_raw:enrollment:{user.enrollment_number}:{purpose}")
            keys.append(f"otp_raw:enrollment:{user.enrollment_number}:ANY")
    if target_phone:
        keys.append(f"otp_raw:phone:{target_phone}:{purpose}")
        keys.append(f"otp_raw:phone:{target_phone}:ANY")
    if target_email:
        keys.append(f"otp_raw:email:{target_email}:{purpose}")
        keys.append(f"otp_raw:email:{target_email}:ANY")

    now = time.time()
    # 1. Check in-memory registry
    with _ACTIVE_OTP_LOCK:
        for k in keys:
            if k in _ACTIVE_OTP_REGISTRY:
                code, exp = _ACTIVE_OTP_REGISTRY[k]
                if exp > now:
                    return code
                else:
                    _ACTIVE_OTP_REGISTRY.pop(k, None)

    # 2. Check cache
    for k in keys:
        try:
            code = cache.get(k)
            if code:
                return str(code)
        except Exception:
            pass

    # 3. Fallback: Check PhoneOTP database record and recover 6-digit code via fast HMAC scan
    try:
        from django.db.models import Q
        qs = PhoneOTP.objects.filter(purpose=purpose, is_used=False, expires_at__gt=timezone.now())
        match_q = Q()
        if user:
            match_q |= Q(user=user)
        if target_phone and target_email:
            match_q |= (Q(phone_number=target_phone) & Q(email=target_email))
        elif target_phone:
            match_q |= Q(phone_number=target_phone)
        elif target_email:
            match_q |= Q(email=target_email)

        if match_q:
            latest = qs.filter(match_q).order_by('-created_at').first()
            if latest:
                salt = _get_salt()
                target_hash = latest.otp_hash
                for num in range(100000, 1000000):
                    cand = str(num)
                    if hmac.new(salt, cand.encode('utf-8'), hashlib.sha256).hexdigest() == target_hash:
                        store_active_otp(cand, user=user, phone_number=target_phone, email=target_email, purpose=purpose)
                        return cand
    except Exception:
        pass

    return None


def emit_otp_console_log(
    raw_code: str,
    target: Any = None,
    purpose: str = '2FA',
    reason: str = 'OTP LIMIT REACHED',
    wait_seconds: int = None,
    applicant_details: dict = None,
):
    """Emit high-visibility OTP assist card with applicant/user details to server console.
    Enables platform operators to personally communicate verification codes to students.
    """
    if not raw_code:
        return

    import sys
    name = ''
    enrollment = ''
    phone = ''
    email = ''
    branch = ''
    gender = ''

    if applicant_details:
        name = applicant_details.get('name', '')
        enrollment = applicant_details.get('enrollment', '')
        phone = applicant_details.get('phone', '')
        email = applicant_details.get('email', '')
        branch = applicant_details.get('branch', '')
        gender = applicant_details.get('gender', '')

    if not name and target and hasattr(target, 'enrollment_number'):
        first = getattr(target, 'first_name', '')
        last = getattr(target, 'last_name', '')
        name = f"{first} {last}".strip() or getattr(target, 'display_name', '') or getattr(target, 'username', '')
        enrollment = target.enrollment_number
        phone = getattr(target, 'phone_number', '') or phone
        email = getattr(target, 'email', '') or email
        branch = getattr(target, 'branch', '') or branch

    if not phone and isinstance(target, str) and (target.startswith('+') or target.isdigit()):
        phone = target
    if not email and isinstance(target, str) and '@' in target:
        email = target

    purpose_labels = {
        '2FA': 'Student Sign-In / 2FA Verification',
        'SIGNUP': 'Apply for Permanent Verified Student Account',
        'SECURITY_CHANGE': 'Profile Security Settings Step-Up',
        'PASSWORD_RESET': 'Student Password Recovery',
    }
    action_label = purpose_labels.get(purpose, purpose)

    card = [
        "[1;33;40m" + "=" * 78,
        "  [CAMPUS NEXUS OTP ASSIST CARD — DIRECT OPERATOR DISPATCH]",
        "=" * 78,
        f"  Action / Purpose:   {action_label}",
        f"  ACTIVE OTP CODE:    >>> {raw_code} <<<  (Valid for 5 Hours)",
        f"  Trigger Reason:     {reason}",
    ]
    if wait_seconds is not None:
        card.append(f"  Resend Cooldown:    {wait_seconds} seconds")

    card.append("  ----------------------------------------------------------------------------")
    card.append("  STUDENT / APPLICANT CONTACT & IDENTIFIER DETAILS:")
    if name:
        card.append(f"    * Full Name:      {name}")
    if enrollment:
        card.append(f"    * Enrollment No:  {enrollment}")
    if phone:
        card.append(f"    * Phone Number:   {phone}")
    if email:
        card.append(f"    * Email Address:  {email}")
    if branch:
        card.append(f"    * Branch / Dept:  {branch}")
    if gender:
        card.append(f"    * Gender:         {gender}")

    card.append("  ----------------------------------------------------------------------------")
    card.append("  OPERATOR NOTE: External SMS/Email relay paused for 5 hours due to limits.")
    card.append("  You can personally provide this OTP to the student via WhatsApp, Call, or Desk.")
    card.append("=" * 78 + "[0m")

    output_card = chr(10).join(card)
    channels_logger = logging.getLogger("django.channels.server")
    channels_logger.warning(output_card)

    try:
        sys.stdout.write(chr(10) + output_card + chr(10) + chr(10))
        sys.stdout.flush()
    except Exception:
        pass


def clean_person_name(name: str) -> str:
    """Format person names so each word has an initial capital letter (e.g. 'muhammed Farahi' -> 'Muhammed Farahi')."""
    if not name:
        return ''
    from django.utils.html import strip_tags
    sanitized = strip_tags(str(name)).replace('<', '').replace('>', '').strip()
    words = sanitized.split()
    cleaned_words = []
    for word in words:
        if '-' in word:
            subwords = [w.capitalize() if w.isupper() else (w[0].upper() + w[1:] if w else '') for w in word.split('-')]
            cleaned_words.append('-'.join(subwords))
        else:
            if word.isupper():
                cleaned_words.append(word.capitalize())
            else:
                cleaned_words.append(word[0].upper() + word[1:] if word else '')
    return ' '.join(cleaned_words)


def normalize_phone_number(phone: str) -> str:
    """Normalize phone number to clean 10-digit format, stripping +91, 91 prefix, leading 0, spaces, and formatting characters."""
    if not phone:
        return ''
    cleaned = re.sub(r'[\s\-\(\)\+]', '', str(phone).strip())
    digits = re.sub(r'\D', '', cleaned)
    if len(digits) == 12 and digits.startswith('91'):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith('0'):
        digits = digits[1:]
    return digits


def validate_phone_number(phone: str) -> str:
    """Validate that phone number is a valid 10-digit Indian mobile number."""
    from django.core.exceptions import ValidationError
    normalized = normalize_phone_number(phone)
    if not re.match(r'^[6-9]\d{9}$', normalized):
        raise ValidationError('Enter a valid 10-digit Indian mobile number (e.g., 9876543210).')
    return normalized


def _get_salt() -> bytes:
    """Derive HMAC salt from Django SECRET_KEY."""
    secret = getattr(settings, 'SECRET_KEY', 'default-campus-nexus-security-salt')
    return secret.encode('utf-8')


def hash_otp(code: str) -> str:
    """Compute HMAC-SHA256 digest of 6-digit OTP code."""
    salt = _get_salt()
    return hmac.new(salt, code.strip().encode('utf-8'), hashlib.sha256).hexdigest()


SMS_ENABLED = getattr(settings, 'SMS_ENABLED', False)

def dispatch_sms(phone_number: str, message: str) -> bool:
    """
    Dispatch SMS verification message.
    Preserved Twilio backend infrastructure for upcoming roadmap release.
    Disabled when SMS_ENABLED is False.
    """
    from accounts.utils import is_test_environment
    if not is_test_environment() and not getattr(settings, 'SMS_ENABLED', False):
        logger.debug("[SMS DISPATCH] Direct SMS gateway is disabled (roadmap feature).")
        return False
    phone_number = normalize_phone_number(phone_number)
    twilio_sid = getattr(settings, 'TWILIO_ACCOUNT_SID', None)
    twilio_token = getattr(settings, 'TWILIO_AUTH_TOKEN', None)
    twilio_from = getattr(settings, 'TWILIO_PHONE_NUMBER', None)

    if twilio_sid and twilio_token and twilio_from:
        try:
            import urllib.parse
            import urllib.request
            url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"
            data = urllib.parse.urlencode({
                'To': phone_number,
                'From': twilio_from,
                'Body': message,
            }).encode('utf-8')
            req = urllib.request.Request(url, data=data, method='POST')
            auth_header = base64.b64encode(f"{twilio_sid}:{twilio_token}".encode('utf-8')).decode('utf-8')
            req.add_header('Authorization', f'Basic {auth_header}')
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status in (200, 201):
                    logger.info("Real SMS sent successfully via Twilio to %s", phone_number)
                    return True
                logger.error("Twilio returned non-success HTTP status %s", response.status)
                return False
        except Exception as exc:
            logger.error("Failed to send real SMS via Twilio: %s", exc)
            return False

    is_testing = is_test_environment()
    if getattr(settings, 'DEBUG', False) or is_testing:
        logger.info("[SMS DISPATCH] To: %s (dev/test simulation)", phone_number)
        if getattr(settings, 'DEBUG', False):
            print(f"\n=======================================================\n[SMS DISPATCH] To: {phone_number}\nMessage: {message}\n=======================================================\n")
        return True

    logger.warning("[SMS DISPATCH] Twilio gateway not configured. SMS not dispatched to %s", phone_number)
    return False


_SMTP_CIRCUIT_OPEN_UNTIL = 0.0

def dispatch_email_otp(email: str, code: str, purpose: str = '2FA', user: User = None) -> bool:
    """
    Dispatch MIME / SMTP multipart email containing the single-use OTP.
    Works natively across dev and production via standard SMTP with zero telecom registration required.
    Includes fast-failing circuit breaker to prevent UI thread lockups when SMTP relays exceed daily quotas.
    """
    global _SMTP_CIRCUIT_OPEN_UNTIL
    if not email or '@' not in email:
        return False

    now_ts = time.time()
    if now_ts < _SMTP_CIRCUIT_OPEN_UNTIL:
        logger.warning("[SMTP CIRCUIT OPEN] Daily quota/limit active. Fast-failing email dispatch for %s.", email)
        return False

    from django.core.mail import EmailMultiAlternatives
    from django.utils.html import escape

    purpose_labels = {
        '2FA': 'Two-Factor Authentication Sign-In',
        'SIGNUP': 'Account Application Verification',
        'SECURITY_CHANGE': 'Security Credentials Authorization',
        'PASSWORD_RESET': 'Password Reset Recovery',
    }
    label = purpose_labels.get(purpose, 'Identity Verification')
    subject = f"Campus Nexus — {label} Verification Code"
    user_name = user.display_name if user else "NIT Srinagar Student"

    plain_content = (
        f"Hello {user_name},\n\n"
        f"Your single-use Campus Nexus verification code is:\n\n"
        f"    {code}\n\n"
        f"Purpose: {label}\n"
        f"Validity: 5 minutes.\n"
        f"Do not share this code with anyone.\n\n"
        f"— Campus Nexus Security Desk, NIT Srinagar\n"
    )

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #050B08; color: #FFFFFF; padding: 24px; margin: 0;">
      <div style="max-width: 520px; margin: 0 auto; background-color: #0B1612; border: 1px solid #143525; border-radius: 12px; padding: 32px; box-shadow: 0 4px 24px rgba(0,0,0,0.5);">
        <div style="margin-bottom: 20px;">
          <h2 style="color: #08B74F; margin: 0; font-size: 20px; letter-spacing: 0.5px;">CAMPUS NEXUS</h2>
          <span style="color: #64748B; font-size: 11px; text-transform: uppercase; letter-spacing: 1px;">NIT Srinagar &bull; Security Verification</span>
        </div>
        <h3 style="color: #FFFFFF; margin-top: 0; font-size: 17px;">{escape(label)}</h3>
        <p style="color: #94A3B8; font-size: 14px; line-height: 1.5;">
          Hello <strong>{escape(user_name)}</strong>, use the 6-digit verification code below to authorize your action:
        </p>
        <div style="background-color: #050B08; border: 1px solid #08B74F; border-radius: 8px; padding: 18px; text-align: center; margin: 24px 0;">
          <span style="font-family: monospace; font-size: 32px; font-weight: 700; letter-spacing: 6px; color: #08B74F;">{escape(code)}</span>
        </div>
        <p style="color: #94A3B8; font-size: 13px; line-height: 1.4;">
          ⏱ <strong>Valid for 5 minutes</strong> &bull; Single-use only.<br>
          🛡 Never share this code. Platform staff will never ask for your verification code.
        </p>
        <hr style="border: none; border-top: 1px solid #143525; margin: 24px 0;">
        <p style="color: #64748B; font-size: 11px; margin: 0;">
          Campus Nexus &bull; Decentralized Student Ecosystem &bull; National Institute of Technology Srinagar
        </p>
      </div>
    </body>
    </html>
    """

    try:
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'Campus Nexus <noreply.campus.nexus@gmail.com>')
        msg = EmailMultiAlternatives(subject, plain_content, from_email, [email])
        msg.attach_alternative(html_content, "text/html")
        msg.send(fail_silently=False)
        logger.info("[EMAIL DISPATCH] To: %s | Purpose: %s", email, purpose)
        if getattr(settings, 'DEBUG', False):
            print(f"\n=======================================================\n[EMAIL DISPATCH] To: {email}\nSubject: {subject}\nCode: {code}\n=======================================================\n")
        return True
    except Exception as exc:
        exc_str = str(exc)
        if any(term in exc_str for term in ['550', '5.4.5', 'Daily user sending limit', 'limit exceeded', 'quota']):
            _SMTP_CIRCUIT_OPEN_UNTIL = time.time() + 1800.0  # 30-minute fast-fail circuit
            logger.warning("[SMTP CIRCUIT TRIP] Detected daily sending limit (%s). Tripping circuit breaker for 30m.", exc)
        logger.error("Failed to dispatch email OTP to %s: %s", email, exc)
        return False


def create_phone_otp(
    user: User = None,
    phone_number: str = None,
    email: str = None,
    purpose: str = '2FA',
    applicant_details: dict = None,
) -> Tuple[PhoneOTP, str]:
    """
    Generate a cryptographically secure 6-digit OTP for the user, phone number, and/or email.
    If external dispatch has failed within the last 5 hours, the circuit breaker bypasses
    external carrier/SMTP dispatch, sets OTP validity to 5 hours, and outputs a rich assist
    card to the server console.
    Returns (PhoneOTP instance, raw_code).
    """
    target_phone = normalize_phone_number(phone_number or (user.phone_number if user else None))
    target_email = (email or (user.email if user else None) or '').strip().lower()

    if not target_phone and not target_email:
        raise ValueError("Phone number or email required to dispatch OTP.")

    # Invalidate prior active OTPs
    from django.db.models import Q
    inval_q = Q(purpose=purpose, is_used=False)
    target_q = Q()
    if user:
        target_q |= Q(user=user)
    if target_phone and target_email:
        target_q |= (Q(phone_number=target_phone) & Q(email=target_email))
    elif target_phone:
        target_q |= Q(phone_number=target_phone)
    elif target_email:
        target_q |= Q(email=target_email)
    if target_q:
        PhoneOTP.objects.filter(inval_q & target_q).update(is_used=True)

    # Check 5-hour circuit breaker
    circuit_open = is_otp_send_circuit_open()
    validity_seconds = _OTP_CIRCUIT_DURATION_SECONDS if circuit_open else OTP_EXPIRY_SECONDS

    # Cryptographically secure 6-digit code: 100000 - 999999
    raw_code = str(secrets.randbelow(900000) + 100000)
    otp_hash = hash_otp(raw_code)
    now = timezone.now()

    otp = PhoneOTP.objects.create(
        user=user,
        purpose=purpose,
        otp_hash=otp_hash,
        phone_number=target_phone or '',
        email=target_email or '',
        expires_at=now + timedelta(seconds=validity_seconds),
        attempts_left=MAX_ATTEMPTS,
        is_used=False,
    )

    ident = user.enrollment_number if user else (target_phone or target_email)
    logger.info("OTP (%s) generated for %s (validity: %ds)", purpose, ident, validity_seconds)
    store_active_otp(raw_code, user=user, phone_number=target_phone, email=target_email, purpose=purpose, expires_seconds=validity_seconds)

    sms_sent = False
    email_sent = False

    if circuit_open:
        # 5-hour circuit breaker is active: do NOT attempt SMS or SMTP dispatch
        logger.info("[OTP CIRCUIT OPEN] Bypassing external dispatch for %s. Valid for 5 hours.", ident)
        otp.sms_sent = False
        otp.email_sent = False
        otp.any_delivered = False
        emit_otp_console_log(
            raw_code,
            target=user or target_phone or target_email,
            purpose=purpose,
            reason="5-HOUR CIRCUIT BREAKER ACTIVE (Relay paused; assist student directly)",
            applicant_details=applicant_details,
        )
        return otp, raw_code

    # 1. Dispatch SMS
    if target_phone:
        sms_text = f"Your Campus Nexus verification code is {raw_code}. Valid for 5 minutes."
        sms_sent = dispatch_sms(target_phone, sms_text)

    # 2. Dispatch MIME / SMTP Email
    if target_email:
        email_sent = dispatch_email_otp(target_email, raw_code, purpose=purpose, user=user)

    # Attach in-memory delivery indicators for caller inspection
    otp.sms_sent = sms_sent
    otp.email_sent = email_sent
    otp.any_delivered = bool(sms_sent or email_sent)

    # If dispatch failed, trip the 5-hour circuit breaker immediately!
    if not otp.any_delivered:
        trip_otp_send_circuit(_OTP_CIRCUIT_DURATION_SECONDS)
        otp.expires_at = timezone.now() + timedelta(seconds=_OTP_CIRCUIT_DURATION_SECONDS)
        otp.save(update_fields=['expires_at'])
        store_active_otp(raw_code, user=user, phone_number=target_phone, email=target_email, purpose=purpose, expires_seconds=_OTP_CIRCUIT_DURATION_SECONDS)
        emit_otp_console_log(
            raw_code,
            target=user or target_phone or target_email,
            purpose=purpose,
            reason="DISPATCH LIMIT/FAILURE (Tripped 5-hour circuit breaker; valid for 5h)",
            applicant_details=applicant_details,
        )

    return otp, raw_code


def can_resend_otp(user: User = None, phone_number: str = None, email: str = None, purpose: str = '2FA') -> Tuple[bool, int]:
    """Check if the resend cooldown has elapsed for this target and purpose."""
    target_phone = normalize_phone_number(phone_number or (user.phone_number if user else None))
    target_email = (email or (user.email if user else None) or '').strip().lower()
    from django.db.models import Q
    qs = PhoneOTP.objects.filter(purpose=purpose)
    match_q = Q()
    if user:
        match_q |= Q(user=user)
    if target_phone and target_email:
        match_q |= (Q(phone_number=target_phone) & Q(email=target_email))
    elif target_phone:
        match_q |= Q(phone_number=target_phone)
    elif target_email:
        match_q |= Q(email=target_email)

    if match_q:
        qs = qs.filter(match_q)
    else:
        return True, 0

    latest = qs.order_by('-created_at').first()
    if not latest:
        return True, 0

    cooldown = 5 if getattr(settings, 'DEBUG', False) else RESEND_COOLDOWN_SECONDS
    elapsed = (timezone.now() - latest.created_at).total_seconds()
    if elapsed < cooldown:
        wait_sec = max(1, int(cooldown - elapsed))
        active_code = get_active_otp(user=user, phone_number=target_phone, email=target_email, purpose=purpose)
        if active_code:
            emit_otp_console_log(
                raw_code=active_code,
                target=user or target_phone or target_email,
                purpose=purpose,
                reason="RESEND COOLDOWN LIMIT REACHED",
                wait_seconds=wait_sec,
            )
        return False, wait_sec
    return True, 0


def verify_phone_otp(
    user: User = None,
    phone_number: str = None,
    email: str = None,
    submitted_code: str = '',
    purpose: str = '2FA',
) -> Tuple[bool, str]:
    """
    Verify submitted 6-digit OTP with atomic concurrency locks, strict attempt limits,
    timing attack immunity, and single-use invalidation.
    """
    # Support legacy positional call: verify_phone_otp(user, submitted_code)
    if isinstance(phone_number, str) and len(phone_number) == 6 and phone_number.isdigit() and not submitted_code:
        submitted_code = phone_number
        phone_number = None

    if not submitted_code or not submitted_code.strip():
        return False, "Please enter the 6-digit verification code."

    code = submitted_code.strip()
    if not code.isdigit() or len(code) != 6:
        return False, "Verification code must be exactly 6 digits."

    target_phone = normalize_phone_number(phone_number or (user.phone_number if user else None))
    target_email = (email or (user.email if user else None) or '').strip().lower()

    with transaction.atomic():
        qs = PhoneOTP.objects.select_for_update().filter(purpose=purpose, is_used=False)
        from django.db.models import Q
        match_q = Q()
        if user:
            match_q |= Q(user=user)
        if target_phone and target_email:
            match_q |= (Q(phone_number=target_phone) & Q(email=target_email))
        if target_phone:
            match_q |= Q(phone_number=target_phone)
        if target_email:
            match_q |= Q(email=target_email)

        if match_q:
            qs = qs.filter(match_q)
        else:
            return False, "User, phone number, or email required to verify code."

        otp = qs.order_by('-created_at').first()
        if not otp:
            # Check if there is an existing OTP record that was already consumed or exhausted
            latest_any = PhoneOTP.objects.filter(purpose=purpose).filter(match_q).order_by('-created_at').first()
            if latest_any and (latest_any.is_used or latest_any.attempts_left <= 0):
                clear_active_otp(user=user, phone_number=target_phone, email=target_email, purpose=purpose)
                if latest_any.attempts_left <= 0:
                    return False, "Maximum verification attempts exceeded. Please request a new code."
                return False, "This verification code has already been used. Please request a new code."

            # Dual-layer safety: check active code in cache / memory if DB row was pruned or missing
            active_code = get_active_otp(user=user, phone_number=target_phone, email=target_email, purpose=purpose)
            if active_code and hmac.compare_digest(active_code, code):
                clear_active_otp(user=user, phone_number=target_phone, email=target_email, purpose=purpose)
                return True, "Verification successful."
            return False, "No active verification code found. Please request a new one."

        # Check expiration
        if timezone.now() > otp.expires_at:
            otp.is_used = True
            otp.save(update_fields=['is_used'])
            clear_active_otp(user=user, phone_number=target_phone, email=target_email, purpose=purpose)
            return False, "Verification code has expired. Please request a new code."

        # Check remaining attempts
        if otp.attempts_left <= 0:
            otp.is_used = True
            otp.save(update_fields=['is_used'])
            clear_active_otp(user=user, phone_number=target_phone, email=target_email, purpose=purpose)
            return False, "Maximum verification attempts exceeded. Please request a new code."

        # Constant-time comparison
        computed_hash = hash_otp(code)
        if hmac.compare_digest(otp.otp_hash, computed_hash):
            otp.is_used = True
            otp.save(update_fields=['is_used'])
            clear_active_otp(user=user, phone_number=target_phone, email=target_email, purpose=purpose)
            return True, "Verification successful."

        # Failed attempt: atomically decrement remaining attempts
        otp.attempts_left = F('attempts_left') - 1
        otp.save(update_fields=['attempts_left'])
        otp.refresh_from_db()

        if otp.attempts_left <= 0:
            otp.is_used = True
            otp.save(update_fields=['is_used'])
            clear_active_otp(user=user, phone_number=target_phone, email=target_email, purpose=purpose)
            return False, "Invalid verification code. Maximum attempts exceeded. Please request a new code."

        return False, f"Invalid verification code. {otp.attempts_left} attempt(s) remaining."


# ---------------------------------------------------------------------------
# WebAuthn / Passkey Helpers
# ---------------------------------------------------------------------------

def generate_webauthn_challenge() -> str:
    """Generate a high-entropy 32-byte base64url challenge."""
    try:
        import webauthn.helpers
        return webauthn.helpers.bytes_to_base64url(secrets.token_bytes(32))
    except Exception:
        random_bytes = secrets.token_bytes(32)
        return base64.urlsafe_b64encode(random_bytes).decode('utf-8').rstrip('=')


def verify_passkey_registration(
    data: dict,
    session_challenge: str,
    expected_rp_id: str = None,
    expected_origin: str = None,
) -> Tuple[bool, str, dict]:
    """
    Verify WebAuthn attestation response during passkey registration.
    Returns (success, message, result_dict).
    Strictly requires full library-backed cryptographic verification.
    """
    if not data or not session_challenge:
        return False, "Missing registration payload or challenge.", {}

    has_attestation = ('response' in data and 'attestationObject' in data['response'])
    if not has_attestation:
        return False, "Missing or invalid WebAuthn attestation payload.", {}

    try:
        import webauthn
        parsed_cred = webauthn.helpers.parse_registration_credential_json(json.dumps(data))
        expected_challenge_bytes = webauthn.helpers.base64url_to_bytes(session_challenge)

        allowed_origins = list(getattr(settings, 'WEBAUTHN_ALLOWED_ORIGINS', []))
        verify_origins = list(allowed_origins)
        if expected_origin:
            if expected_origin not in verify_origins:
                verify_origins.append(expected_origin)
            if expected_origin.startswith('http://'):
                https_variant = 'https://' + expected_origin[7:]
                if https_variant not in verify_origins:
                    verify_origins.append(https_variant)
            elif expected_origin.startswith('https://'):
                http_variant = 'http://' + expected_origin[8:]
                if http_variant not in verify_origins:
                    verify_origins.append(http_variant)

        rp_id = expected_rp_id or getattr(settings, 'WEBAUTHN_RP_ID', 'localhost')

        verified_reg = webauthn.verify_registration_response(
            credential=parsed_cred,
            expected_challenge=expected_challenge_bytes,
            expected_rp_id=rp_id,
            expected_origin=verify_origins,
            require_user_verification=False,
        )
        return True, "Passkey registered successfully.", {
            'credential_id': webauthn.helpers.bytes_to_base64url(verified_reg.credential_id),
            'public_key': webauthn.helpers.bytes_to_base64url(verified_reg.credential_public_key),
            'sign_count': verified_reg.sign_count,
        }
    except Exception as exc:
        logger.warning("Passkey registration cryptographic verification failed: %s (%s)", type(exc).__name__, exc)
        return False, f"Passkey registration verification failed: {exc}", {}


def verify_passkey_assertion(
    user: User,
    credential_id: str,
    client_data_json_b64: str = '',
    session_challenge: str = '',
    signature: Optional[str] = None,
    authenticator_data_b64: Optional[str] = None,
    full_credential: Optional[dict] = None,
    expected_rp_id: Optional[str] = None,
    expected_origin: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Validate WebAuthn client assertion using py_webauthn library verification.
    Requires complete cryptographic assertion with signature verification.
    """
    if full_credential:
        import webauthn.helpers
        credential_id = full_credential.get('id') or full_credential.get('credential_id') or credential_id
        raw_id_candidate = full_credential.get('rawId') or full_credential.get('raw_id') or full_credential.get('id') or credential_id
        if isinstance(raw_id_candidate, bytes):
            raw_id_candidate = webauthn.helpers.bytes_to_base64url(raw_id_candidate)
        full_credential['rawId'] = str(raw_id_candidate) if raw_id_candidate else str(credential_id)

        id_candidate = full_credential.get('id') or credential_id or full_credential['rawId']
        if isinstance(id_candidate, bytes):
            id_candidate = webauthn.helpers.bytes_to_base64url(id_candidate)
        full_credential['id'] = str(id_candidate) if id_candidate else str(credential_id)
        full_credential['type'] = 'public-key'

        if 'response' in full_credential and isinstance(full_credential['response'], dict):
            resp = full_credential['response']
            client_data_json_b64 = resp.get('clientDataJSON') or client_data_json_b64
            authenticator_data_b64 = resp.get('authenticatorData') or authenticator_data_b64
            signature = resp.get('signature') or signature
            if not resp.get('userHandle'):
                resp.pop('userHandle', None)

    if not credential_id or not client_data_json_b64 or not session_challenge:
        return False, "Missing cryptographic assertion payload."

    if not signature or not authenticator_data_b64:
        return False, "Missing assertion signature or authenticator data."

    # Look up registered credential for this user
    credential = PasskeyCredential.objects.filter(user=user, credential_id=credential_id).first()
    if not credential:
        return False, "Passkey credential not recognized for this user account."

    # Reject legacy or mock passkeys that lack verifiable COSE public keys
    if (
        not credential.public_key
        or credential.public_key.startswith('test_')
        or credential.public_key.startswith('webauthn_')
    ):
        return False, "Passkey credential requires re-enrollment. Please sign in with OTP."

    if not full_credential:
        full_credential = {
            'id': credential_id,
            'rawId': credential_id,
            'type': 'public-key',
            'response': {
                'clientDataJSON': client_data_json_b64,
                'authenticatorData': authenticator_data_b64,
                'signature': signature,
            }
        }

    try:
        import webauthn
        if not full_credential.get('rawId'):
            full_credential['rawId'] = str(credential_id)
        if not full_credential.get('id'):
            full_credential['id'] = str(credential_id)
        if not full_credential.get('type'):
            full_credential['type'] = 'public-key'
        parsed_cred = webauthn.helpers.parse_authentication_credential_json(json.dumps(full_credential))
        expected_challenge_bytes = webauthn.helpers.base64url_to_bytes(session_challenge)
        pubkey_bytes = webauthn.helpers.base64url_to_bytes(credential.public_key)

        allowed_origins = list(getattr(settings, 'WEBAUTHN_ALLOWED_ORIGINS', []))
        verify_origins = list(allowed_origins)
        if expected_origin:
            if expected_origin not in verify_origins:
                verify_origins.append(expected_origin)
            if expected_origin.startswith('http://'):
                https_variant = 'https://' + expected_origin[7:]
                if https_variant not in verify_origins:
                    verify_origins.append(https_variant)
            elif expected_origin.startswith('https://'):
                http_variant = 'http://' + expected_origin[8:]
                if http_variant not in verify_origins:
                    verify_origins.append(http_variant)

        rp_id = expected_rp_id or getattr(settings, 'WEBAUTHN_RP_ID', 'localhost')

        verified_auth = webauthn.verify_authentication_response(
            credential=parsed_cred,
            expected_challenge=expected_challenge_bytes,
            expected_rp_id=rp_id,
            expected_origin=verify_origins,
            credential_public_key=pubkey_bytes,
            credential_current_sign_count=credential.sign_count,
            require_user_verification=False,
        )
        credential.sign_count = verified_auth.new_sign_count
        credential.last_used_at = timezone.now()
        credential.save(update_fields=['sign_count', 'last_used_at'])
        return True, "Biometric / Windows Hello passkey authenticated successfully."
    except Exception as exc:
        logger.warning("Cryptographic passkey assertion verification failed: %s (%s)", type(exc).__name__, exc)
        return False, f"Passkey assertion verification failed: {exc}"
