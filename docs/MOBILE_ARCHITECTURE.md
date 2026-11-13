# Mobile and PWA architecture

Reviewed 2026-11-12 against `static/manifest.json`, `static/sw.js`, `templates/base.html`, and session settings. The mobile experience is the responsive Django web application with optional browser PWA installation; this repository does not contain a separate React Native app.

## App shell and install metadata

The manifest is served at `/manifest.json`, has root scope, uses standalone display mode, and points to the dashboard as its start URL. Install prompts and install guidance are implemented in `templates/base.html`. Browser install availability varies by browser and operating system; users may need to install from the browser menu.

## Service worker and offline limits

The service worker is served at `/sw.js` with root scope. It:

- precaches the offline page and selected PWA icons;
- uses network-first navigation with the offline page as a fallback;
- uses stale-while-revalidate for same-origin static files;
- handles manifest requests with a network-first strategy and cached fallback;
- leaves non-GET requests alone and uses network-first with cache fallback for other same-origin GET requests;
- handles push notifications when the browser supports the Push API.

This provides limited offline presentation and cached assets. It does not make ride creation, joining, chat, student search, or account changes work offline. Cached GET pages may contain authenticated content, so review privacy and cache behavior whenever the service worker changes.

## Session behavior

The normal configured absolute session lifetime is one day and idle timeout is 30 minutes. The application applies a longer idle timeout to requests identified as standalone apps. These are session rules, not a guarantee that passkey-only login lasts a fixed two weeks on every platform. Browser storage, authentication challenges, and server session settings all affect observed behavior.

## Mobile directory and ride browsing

The student directory fetches results as the user types and keeps partial matching, with first-name prefix matches ranked first. Results are grouped by batch; each batch can be expanded or collapsed independently. Browse Rides keeps detailed filters in a collapsed disclosure so ride cards remain easier to reach on small screens.

## Compatibility and verification

PWA install, push, WebAuthn, and offline behavior depend on browser support and secure-origin configuration. Verify on the target Android and iOS browser versions, including slow networks, install and update flows, session expiry, and lost connectivity. No mobile browser QA was run for this documentation update.
