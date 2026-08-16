// Service worker minimal — suffit à satisfaire les critères
// d'installation "app standalone" de Chrome/Android. Ne met rien en
// cache pour l'instant (le tableau de bord doit toujours afficher les
// données les plus récentes).

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  // Laisse passer toutes les requêtes normalement (pas de cache).
  event.respondWith(fetch(event.request));
});
