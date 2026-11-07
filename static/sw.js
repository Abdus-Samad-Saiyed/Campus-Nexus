// Campus Nexus - Service Worker for Desktop and Mobile Notifications

self.addEventListener('install', function(event) {
    self.skipWaiting();
});

self.addEventListener('activate', function(event) {
    event.waitUntil(self.clients.claim());
});

self.addEventListener('push', function(event) {
    if (!event.data) return;
    try {
        const payload = event.data.json();
        const title = payload.title || 'Campus Nexus';
        const options = {
            body: payload.body || payload.content || '',
            icon: payload.icon || '/static/img/nexus-icon.png',
            badge: '/static/img/nexus-icon.png',
            vibrate: [200, 100, 200],
            tag: payload.tag || 'nexus-notification',
            renotify: true,
            data: {
                url: payload.url || '/dashboard/',
            },
        };
        event.waitUntil(self.registration.showNotification(title, options));
    } catch (e) {
        console.error('Service Worker push error:', e);
    }
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    const targetUrl = (event.notification.data && event.notification.data.url) ? event.notification.data.url : '/dashboard/';

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(clientList) {
            for (let i = 0; i < clientList.length; i++) {
                const client = clientList[i];
                if (client.url.includes(targetUrl) && 'focus' in client) {
                    return client.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow(targetUrl);
            }
        })
    );
});
