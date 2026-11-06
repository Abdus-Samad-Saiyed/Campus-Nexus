"""
Cryptographically secure Two-Factor Authentication (2FA) & WebAuthn / Passkey Engine.
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
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


def clean_person_name(name: str) -> str:
    """Format person names so each word has an initial capital letter (e.g. 'muhammed Farahi' -> 'Muhammed Farahi')."""
    if not name:
        return ''
    words = str(name).strip().split()
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

    import sys
    is_testing = is_test_environment() or 'test' in sys.argv or getattr(settings, 'TESTING', False)
    if getattr(settings, 'DEBUG', False) or is_testing:
        logger.info("[SMS DISPATCH] To: %s (dev/test simulation)", phone_number)
        if getattr(settings, 'DEBUG', False):
            print(f"\n=======================================================\n[SMS DISPATCH] To: {phone_number}\nMessage: {message}\n=======================================================\n")
        return True

    logger.warning("[SMS DISPATCH] Twilio gateway not configured. SMS not dispatched to %s", phone_number)
    return False


def dispatch_email_otp(email: str, code: str, purpose: str = '2FA', user: User = None) -> bool:
    """
    Dispatch MIME / SMTP multipart email containing the single-use OTP.
    Works natively across dev and production via standard SMTP with zero telecom registration required.
    """
    if not email or '@' not in email:
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
        logger.error("Failed to dispatch email OTP to %s: %s", email, exc)
        return False


def create_phone_otp(
    user: User = None,
    phone_number: str = None,
    email: str = None,
    purpose: str = '2FA',
) -> Tuple[PhoneOTP, str]:
    """
    Generate a cryptographically secure 6-digit OTP for the user, phone number, and/or email.
    Dispatches to both SMS carrier and MIME/SMTP email channels simultaneously.
    Invalidates any existing unused OTPs for the same target and purpose to prevent replay attacks.
    Returns (PhoneOTP instance, raw_code).
    """
    target_phone = normalize_phone_number(phone_number or (user.phone_number if user else None))
    target_email = (email or (user.email if user else None) or '').strip().lower()
    if not target_phone and not target_email:
        raise ValueError("Phone number or email required to dispatch OTP.")

    # Invalidate prior active OTPs
    if user:
        PhoneOTP.objects.filter(user=user, purpose=purpose, is_used=False).update(is_used=True)
    elif target_phone and target_email and purpose == 'SIGNUP':
        PhoneOTP.objects.filter(phone_number=target_phone, email=target_email, purpose=purpose, is_used=False).update(is_used=True)
    elif target_phone:
        PhoneOTP.objects.filter(phone_number=target_phone, purpose=purpose, is_used=False).update(is_used=True)
    elif target_email:
        PhoneOTP.objects.filter(email=target_email, purpose=purpose, is_used=False).update(is_used=True)

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
        expires_at=now + timedelta(seconds=OTP_EXPIRY_SECONDS),
        attempts_left=MAX_ATTEMPTS,
        is_used=False,
    )

    ident = user.enrollment_number if user else (target_phone or target_email)
    logger.info("OTP (%s) generated for %s", purpose, ident)

    # 1. Dispatch SMS
    sms_sent = False
    if target_phone:
        sms_text = f"Your Campus Nexus verification code is {raw_code}. Valid for 5 minutes."
        sms_sent = dispatch_sms(target_phone, sms_text)

    # 2. Dispatch MIME / SMTP Email
    email_sent = False
    if target_email:
        email_sent = dispatch_email_otp(target_email, raw_code, purpose=purpose, user=user)

    # Attach in-memory delivery indicators for caller inspection
    otp.sms_sent = sms_sent
    otp.email_sent = email_sent
    otp.any_delivered = bool(sms_sent or email_sent)

    # Purge undelivered OTP rows so failed deliveries do not block cooldowns
    if not otp.any_delivered:
        otp.delete()

    return otp, raw_code


def can_resend_otp(user: User = None, phone_number: str = None, email: str = None, purpose: str = '2FA') -> Tuple[bool, int]:
    """Check if the resend cooldown has elapsed for this target and purpose."""
    target_phone = normalize_phone_number(phone_number or (user.phone_number if user else None))
    target_email = (email or (user.email if user else None) or '').strip().lower()
    qs = PhoneOTP.objects.filter(purpose=purpose)
    if user:
        qs = qs.filter(user=user)
    elif target_phone and target_email and purpose == 'SIGNUP':
        qs = qs.filter(phone_number=target_phone, email=target_email)
    elif target_phone:
        qs = qs.filter(phone_number=target_phone)
    elif target_email:
        qs = qs.filter(email=target_email)
    else:
        return True, 0

    latest = qs.order_by('-created_at').first()
    if not latest:
        return True, 0

    cooldown = 5 if getattr(settings, 'DEBUG', False) else RESEND_COOLDOWN_SECONDS
    elapsed = (timezone.now() - latest.created_at).total_seconds()
    if elapsed < cooldown:
        return False, max(1, int(cooldown - elapsed))
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
        if user:
            qs = qs.filter(user=user)
        elif target_phone and target_email and purpose == 'SIGNUP':
            # Bind both channels to prevent phone hijacking
            qs = qs.filter(phone_number=target_phone, email=target_email)
        elif target_phone and target_email:
            from django.db.models import Q
            qs = qs.filter(Q(phone_number=target_phone) | Q(email=target_email))
        elif target_phone:
            qs = qs.filter(phone_number=target_phone)
        elif target_email:
            qs = qs.filter(email=target_email)
        else:
            return False, "User, phone number, or email required to verify code."

        otp = qs.order_by('-created_at').first()
        if not otp:
            return False, "No active verification code found. Please request a new one."

        # Check expiration
        if timezone.now() > otp.expires_at:
            otp.is_used = True
            otp.save(update_fields=['is_used'])
            return False, "Verification code has expired. Please request a new code."

        # Check remaining attempts
        if otp.attempts_left <= 0:
            otp.is_used = True
            otp.save(update_fields=['is_used'])
            return False, "Maximum verification attempts exceeded. Please request a new code."

        # Constant-time comparison
        computed_hash = hash_otp(code)
        if hmac.compare_digest(otp.otp_hash, computed_hash):
            otp.is_used = True
            otp.save(update_fields=['is_used'])
            return True, "Verification successful."

        # Failed attempt: atomically decrement remaining attempts
        otp.attempts_left = F('attempts_left') - 1
        otp.save(update_fields=['attempts_left'])
        otp.refresh_from_db()

        if otp.attempts_left <= 0:
            otp.is_used = True
            otp.save(update_fields=['is_used'])
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
        credential_id = full_credential.get('id') or credential_id
        if 'response' in full_credential:
            client_data_json_b64 = full_credential['response'].get('clientDataJSON') or client_data_json_b64
            authenticator_data_b64 = full_credential['response'].get('authenticatorData') or authenticator_data_b64
            signature = full_credential['response'].get('signature') or signature

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
