// Campus Nexus - Progressive Web App Service Worker
// Provides Network-First navigation with offline fallback, asset caching, and Web Push notifications

const CACHE_NAME = 'campus-nexus-v2';
const PRECACHE_URLS = [
    '/offline/',
    '/static/img/pwa/icon-192x192.png',
    '/static/img/pwa/icon-512x512.png'
];

self.addEventListener('install', function(event) {
    event.waitUntil(
        caches.open(CACHE_NAME).then(function(cache) {
            return cache.addAll(PRECACHE_URLS).catch(function(err) {
                console.warn('[SW] Pre-cache warning:', err);
            });
        }).then(function() {
            return self.skipWaiting();
        })
    );
});

self.addEventListener('activate', function(event) {
    event.waitUntil(
        caches.keys().then(function(cacheNames) {
            return Promise.all(
                cacheNames.map(function(cacheName) {
                    if (cacheName !== CACHE_NAME) {
                        return caches.delete(cacheName);
                    }
                })
            );
        }).then(function() {
            // Purge stale CSS/JS entries inside current cache so existing PWAs pick up UI updates
            return caches.open(CACHE_NAME).then(function(cache) {
                return cache.keys().then(function(requests) {
                    return Promise.all(
                        requests.map(function(req) {
                            try {
                                const pathname = new URL(req.url).pathname;
                                if (pathname.startsWith('/static/css/') || pathname.startsWith('/static/js/')) {
                                    return cache.delete(req);
                                }
                            } catch (e) {}
                        })
                    );
                });
            });
        }).then(function() {
            return self.clients.claim();
        })
    );
});

self.addEventListener('fetch', function(event) {
    const request = event.request;
    if (request.method !== 'GET') {
        return;
    }

    const url = new URL(request.url);

    // Bypass WebSockets and cross-origin analytics/CDN calls from strict caching
    if (url.protocol.startsWith('ws')) {
        return;
    }

    // 1. Navigation requests (HTML page loads): Network-First with Offline Fallback
    if (request.mode === 'navigate') {
        event.respondWith(
            fetch(request).catch(function() {
                return caches.match('/offline/').then(function(offlineResponse) {
                    return offlineResponse || new Response(
                        '<h1>Campus Nexus is Offline</h1><p>Please check your internet connection.</p>',
                        { headers: { 'Content-Type': 'text/html; charset=utf-8' } }
                    );
                });
            })
        );
        return;
    }

    // 2. Web App Manifest: Network-First so PWA config/theme updates always propagate
    if (url.origin === self.location.origin && url.pathname === '/manifest.json') {
        event.respondWith(
            fetch(request).then(function(networkResponse) {
                if (networkResponse && networkResponse.status === 200) {
                    const responseClone = networkResponse.clone();
                    caches.open(CACHE_NAME).then(function(cache) {
                        cache.put(request, responseClone);
                    });
                }
                return networkResponse;
            }).catch(function() {
                return caches.match(request);
            })
        );
        return;
    }

    // 3. Static application assets: Stale-While-Revalidate for instant load + automatic freshness
    if (url.origin === self.location.origin && url.pathname.startsWith('/static/')) {
        event.respondWith(
            caches.match(request).then(function(cachedResponse) {
                const fetchPromise = fetch(request).then(function(networkResponse) {
                    if (networkResponse && networkResponse.status === 200 && networkResponse.type === 'basic') {
                        const responseClone = networkResponse.clone();
                        caches.open(CACHE_NAME).then(function(cache) {
                            cache.put(request, responseClone);
                        });
                    }
                    return networkResponse;
                }).catch(function() {
                    return cachedResponse;
                });
                return cachedResponse || fetchPromise;
            })
        );
        return;
    }

    // 3. All other local GET requests: Network-First
    if (url.origin === self.location.origin) {
        event.respondWith(
            fetch(request).catch(function() {
                return caches.match(request);
            })
        );
    }
});

self.addEventListener('push', function(event) {
    if (!event.data) return;
    try {
        const payload = event.data.json();
        const title = payload.title || 'Campus Nexus';
        const rawUrl = payload.url || '/dashboard/';
        const iconPath = payload.icon || '/static/img/pwa/icon-192x192.png';
        const badgePath = payload.badge || '/static/img/pwa/icon-192x192.png';
        const resolvedIcon = new URL(iconPath, self.location.origin).href;
        const resolvedBadge = new URL(badgePath, self.location.origin).href;
        const pushTag = payload.tag || 'nexus-notification';
        const targetPath = new URL(rawUrl, self.location.origin).pathname.replace(/\/+$/, '').toLowerCase();
        const isLiveChatTag = pushTag.startsWith('dm-') || pushTag.startsWith('rchat-');

        const options = {
            body: payload.body || payload.content || '',
            icon: resolvedIcon,
            badge: resolvedBadge,
            vibrate: [200, 100, 200],
            tag: pushTag,
            renotify: true,
            requireInteraction: true,
            silent: false,
            data: {
                url: rawUrl,
                tag: pushTag,
            },
        };

        event.waitUntil(
            Promise.all([
                clients.matchAll({ type: 'window', includeUncontrolled: true }),
                self.registration.getNotifications({ tag: pushTag }).catch(function() { return []; }),
            ]).then(function(results) {
                const clientList = results[0] || [];
                const existingWithTag = results[1] || [];

                // Avoid double-buzzing if the WebSocket layer just showed this exact event tag
                const now = Date.now();
                if (pushTag !== 'nexus-notification' && existingWithTag.some(function(n) {
                    return !n.timestamp || (now - n.timestamp) < 15000;
                })) {
                    return;
                }

                // Suppress OS popup ONLY if the user is actively focused inside that exact chat conversation
                if (isLiveChatTag) {
                    const activelyViewingChat = clientList.some(function(client) {
                        try {
                            const clientPath = new URL(client.url).pathname.replace(/\/+$/, '').toLowerCase();
                            return client.visibilityState === 'visible' && client.focused && clientPath === targetPath;
                        } catch (err) {
                            return false;
                        }
                    });
                    if (activelyViewingChat) {
                        return;
                    }
                }

                return self.registration.showNotification(title, options);
            }).catch(function() {
                return self.registration.showNotification(title, options);
            })
        );
    } catch (e) {
        console.error('Service Worker push error:', e);
    }
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    const rawUrl = (event.notification.data && event.notification.data.url) ? event.notification.data.url : '/dashboard/';
    const targetUrl = new URL(rawUrl, self.location.origin).href;

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(clientList) {
            // 1. Is there an open window already on this exact target URL?
            for (let i = 0; i < clientList.length; i++) {
                const client = clientList[i];
                if (client.url === targetUrl && 'focus' in client) {
                    return client.focus();
                }
            }

            // 2. If any window from our origin is open, bring it to focus and navigate it straight to the target URL!
            let targetClient = null;
            for (let i = 0; i < clientList.length; i++) {
                const client = clientList[i];
                if (client.url.startsWith(self.location.origin)) {
                    if (client.focused) {
                        targetClient = client;
                        break;
                    }
                    if (!targetClient) {
                        targetClient = client;
                    }
                }
            }

            if (targetClient) {
                if ('focus' in targetClient) {
                    targetClient.focus();
                }
                if ('navigate' in targetClient) {
                    return targetClient.navigate(targetUrl).then(function(navigatedClient) {
                        return (navigatedClient || targetClient).focus();
                    }).catch(function() {
                        targetClient.postMessage({ action: 'navigate', url: targetUrl });
                        return targetClient.focus();
                    });
                } else {
                    targetClient.postMessage({ action: 'navigate', url: targetUrl });
                    return targetClient.focus();
                }
            }

            // 3. Only open a new window if NO window is open
            if (clients.openWindow) {
                return clients.openWindow(targetUrl);
            }
        })
    );
});
