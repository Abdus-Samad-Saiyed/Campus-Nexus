"""Campus Nexus Educational & Technical Diagnostic Knowledge Base.

Provides student-friendly quick answers paired with deep technical explanations
for authentication, passkeys, telecom OTP limits, anti-lockout protection, and security policies.
"""

from typing import Any, Dict, Optional
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render

LEARN_MORE_TOPICS: Dict[str, Dict[str, Any]] = {
    "passkeys": {
        "slug": "passkeys",
        "title": "Why Campus Nexus Uses Biometric Passkeys",
        "badge": "Security & Speed",
        "badge_class": "badge-open",
        "read_time": "2 min read",
        "icon": "bi-fingerprint",
        "simple_summary": "Passkeys let you sign in with your phone or laptop's fingerprint, face unlock, or screen PIN instead of waiting for an SMS code that might get delayed or blocked by telecom operators.",
        "why_matters": "Every semester during high-traffic cab booking windows, hundreds of students try to log in simultaneously. SMS carriers throttle OTP messages, leaving students stranded. Passkeys work 100% locally on your device with zero carrier dependency.",
        "how_it_works_technical": (
            "Campus Nexus implements the W3C WebAuthn / FIDO2 public-key cryptography standard. "
            "When you register a passkey, your browser asks your device's Secure Enclave or Trusted Platform Module (TPM) "
            "to generate an asymmetric cryptographic key pair. The private key never leaves your physical device. "
            "Only the public key is sent to Campus Nexus. During sign-in, the server issues a high-entropy random challenge (32 bytes), "
            "which your device signs using its private key upon biometric confirmation. "
            "Because no shared secret or password ever travels across the network, passkeys are mathematically immune to phishing, SIM swapping, and credential stuffing."
        ),
        "steps_to_resolve": [
            "Open your personal Dashboard or Profile settings.",
            "Click the 'Set up fingerprint / face unlock' button.",
            "Touch your fingerprint sensor or verify with Windows Hello / Touch ID when prompted by your browser.",
            "Your passkey is saved. Future sign-ins will take under a second with zero SMS waiting.",
        ],
        "faq": [
            {
                "q": "Does Campus Nexus store my actual fingerprint or face scan?",
                "a": "No. Your biometric data never leaves your device's hardware chip (Apple Secure Enclave, Android Keystore, or Windows TPM). The browser only tells Campus Nexus whether your local biometric check passed or failed."
            },
            {
                "q": "What if I get a new phone or laptop?",
                "a": "You can register multiple passkeys across all your devices (Mac, Windows PC, iPhone, Android). If you switch devices, you can use your password or another registered passkey to sign in and add the new device."
            }
        ]
    },
    "otp-delay": {
        "slug": "otp-delay",
        "title": "What Happened to My SMS Verification Code?",
        "badge": "Telecom Delivery & Fallback",
        "badge_class": "badge-warning",
        "read_time": "2 min read",
        "icon": "bi-chat-left-dots",
        "simple_summary": "If your SMS verification code did not arrive within 30 seconds, telecom gateways may be congested or have reached their daily broadcast limit. Our campus server has automatically backed up your code.",
        "why_matters": "Telecom networks in Jammu & Kashmir operate under strict TRAI Distributed Ledger Technology (DLT) regulations and commercial bulk-SMS quotas. When peak demand hits, SMS queues back up or reject delivery.",
        "how_it_works_technical": (
            "Campus Nexus features an automated 5-Hour Circuit Breaker on telecom relays. "
            "When an SMS or SMTP relay reports a delivery failure, quota error (e.g. SMTP 550 5.4.5), or rate-limit (HTTP 429), "
            "the server opens a circuit breaker for 5 hours to prevent thread starvation and further gateway penalties. "
            "During this period, generated OTPs are given an extended 5-hour validity window and printed directly to the secure "
            "server console alongside your application details. This enables platform administrators at NIT Srinagar to manually "
            "verify your identity and convey your code directly via WhatsApp, official email, or phone call."
        ),
        "steps_to_resolve": [
            "Wait 60 seconds and check your institute webmail inbox as well (dual-channel delivery sends codes to email too).",
            "If neither SMS nor email arrives, our campus engineering team sees your active verification code on the system console.",
            "Send a quick WhatsApp message to the Student Operations Desk (+91 9149812345) or visit the Main Gate Desk to receive your code instantly.",
            "Once signed in, immediately register a Biometric Passkey so you never have to depend on SMS gateways again.",
        ],
        "faq": [
            {
                "q": "Does my code expire while I wait?",
                "a": "No. During telecom fallback mode, your verification code's validity is extended from 5 minutes to 5 hours specifically so you have time to receive manual assistance."
            }
        ]
    },
    "passkey-lockout": {
        "slug": "passkey-lockout",
        "title": "Anti-Lockout Protection: Why You Need at Least One Passkey",
        "badge": "Account Safeguard",
        "badge_class": "badge-info",
        "read_time": "1 min read",
        "icon": "bi-shield-lock",
        "simple_summary": "The system blocked you from deleting your passkey because it is your only one. You must add a replacement passkey before deleting your current one.",
        "why_matters": "Verified permanent accounts require biometric authentication for high-security operations. If you delete your only passkey, you could accidentally lock yourself out of your account if SMS delivery is down.",
        "how_it_works_technical": (
            "Campus Nexus enforces a strict Anti-Lockout Invariant at the database and API level. "
            "For permanent student accounts with two-factor authentication enabled, the system maintains a credential floor "
            "of at least one registered FIDO2 credential. Deletion requests that would reduce your passkey count to zero are "
            "atomically rejected with an HTTP 400 error. To swap devices or hardware keys, simply register your new device's "
            "passkey first. Once two or more passkeys exist on your account, you can freely remove the older one."
        ),
        "steps_to_resolve": [
            "In your Profile under 'Registered Biometric Keys', click 'Register Passkey'.",
            "Verify your identity and save the new device.",
            "Once the new passkey appears in the list, you can safely delete the old one.",
        ],
        "faq": [
            {
                "q": "Can I have passkeys on both my phone and laptop?",
                "a": "Yes! We strongly recommend having at least two passkeys registered: one on your primary laptop and one on your mobile phone."
            }
        ]
    },
    "profile-security": {
        "slug": "profile-security",
        "title": "Security Verification for Profile Edits",
        "badge": "Privacy Protection",
        "badge_class": "badge-open",
        "read_time": "2 min read",
        "icon": "bi-person-check",
        "simple_summary": "Before updating your phone number, password, or security toggles, Campus Nexus asks you to confirm it is really you using your fingerprint, face, or a quick code.",
        "why_matters": "If you leave your laptop open in the library or common room, another person could change your phone number or password and take over your account. Step-up verification stops unauthorized changes.",
        "how_it_works_technical": (
            "Campus Nexus implements Step-Up Re-Authentication with a sliding 15-minute security grace window. "
            "Modifications to sensitive attributes (email, phone number, password, or 2FA states) require session elevation. "
            "Students with a registered biometric passkey can satisfy this challenge in one tap via WebAuthn assertion. "
            "Upon successful assertion, a cryptographically signed grace timestamp is stored in your session "
            "(security_otp_verified_until), allowing you to make updates for 15 minutes without repeated prompts."
        ),
        "steps_to_resolve": [
            "In the profile edit window, click 'Verify with Passkey / Biometrics'.",
            "Touch your fingerprint sensor or verify with Windows Hello / Touch ID.",
            "The security section turns green, indicating your session is unlocked.",
            "Update your details and click 'Save Profile'.",
        ],
        "faq": [
            {
                "q": "Do I need to verify every time I edit my bio or photo?",
                "a": "No. Non-sensitive edits like updating your short bio or changing your profile photo do not require security verification."
            }
        ]
    },
    "single-session": {
        "slug": "single-session",
        "title": "Simultaneous Multi-Platform Sessions & Device Limits",
        "badge": "Platform Policy",
        "badge_class": "badge-info",
        "read_time": "2 min read",
        "icon": "bi-laptop",
        "simple_summary": "Campus Nexus allows you to be logged in on up to 3 distinct platforms (such as your phone app, your laptop, and a browser) at the same time.",
        "why_matters": "Sharing student accounts or leaving public lab machines logged in compromises peer safety during cab coordination. If someone logs in from another device on the same platform, the older session is closed.",
        "how_it_works_technical": (
            "Campus Nexus enforces a multi-platform concurrent session policy managed by UserPlatformSession: "
            "1. Each distinct platform tier (Web Browser, Standalone Desktop PWA, and Mobile PWA) is tracked separately. "
            "2. If you sign in to a new browser while already logged in on a browser, your older browser session is terminated. "
            "3. If a 4th distinct platform authenticates, the least-recently-active session is automatically evicted. "
            "This ensures that verified student identities in ride coordination rooms always reflect the authentic single student."
        ),
        "steps_to_resolve": [
            "If your session was closed because you signed in elsewhere, simply sign in again.",
            "To stay permanently logged in on your personal laptop or phone without being logged out, install the Campus Nexus PWA App via the 'Install' button on the top navbar.",
        ],
        "faq": [
            {
                "q": "How long do sessions last?",
                "a": "Standard web sessions expire after 30 minutes of inactivity. Downloaded PWA apps on personal laptops and phones remain active for 14 days."
            }
        ]
    }
}


def learn_more_view(request: HttpRequest, topic: Optional[str] = None) -> HttpResponse:
    """Educational help and diagnostic page explaining platform security and operational policies."""
    if not topic:
        topic = "passkeys"

    slug = topic.strip().lower()
    topic_data = LEARN_MORE_TOPICS.get(slug)
    if not topic_data:
        raise Http404(f"Help topic '{slug}' not found.")

    return render(
        request,
        "nexus/learn_more.html",
        {
            "topic": topic_data,
            "all_topics": LEARN_MORE_TOPICS.values(),
            "current_slug": slug,
        },
    )
