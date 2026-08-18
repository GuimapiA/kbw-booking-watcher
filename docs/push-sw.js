// Service worker dédié UNIQUEMENT aux notifications push. Pas de
// manifest.json associé volontairement, pour éviter de redéclencher
// la tentative d'installation "app autonome" qui plantait sur
// certains téléphones.

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  self.clients.claim();
});

self.addEventListener("push", (event) => {
  let data = { title: "🎓 KBW Booking Watcher", body: "Une place vient de s'ouvrir !" };
  try {
    if (event.data) data = event.data.json();
  } catch (e) {
    // ignore, on garde le message par défaut
  }

  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: "icons/android-chrome-192x192.png",
      badge: "icons/favicon-32x32.png",
      data: { url: data.url || "./" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data && event.notification.data.url ? event.notification.data.url : "./";
  event.waitUntil(clients.openWindow(url));
});
