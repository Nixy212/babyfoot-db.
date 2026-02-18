// ── PWA Registration — Baby-Foot Club ────────────────────────

(function () {
  'use strict';

  // ── Détection iOS (Safari ne supporte pas beforeinstallprompt) ──
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const isInStandaloneMode = window.navigator.standalone === true
    || window.matchMedia('(display-mode: standalone)').matches;

  // ── Service Worker ──────────────────────────────────────────
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js', { scope: '/' })
        .then(reg => {
          reg.addEventListener('updatefound', () => {
            const w = reg.installing;
            w.addEventListener('statechange', () => {
              if (w.state === 'installed' && navigator.serviceWorker.controller) {
                showUpdateToast();
              }
            });
          });
        })
        .catch(() => { });
    });
  }

  // ── Install prompt (Android / Chrome / Edge) ────────────────
  let deferredPrompt = null;

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    showMenuInstallBtn();
  });

  window.addEventListener('appinstalled', () => {
    deferredPrompt = null;
    hideMenuInstallBtn();
  });

  // ── Bouton menu hamburger ───────────────────────────────────
  function showMenuInstallBtn() {
    if (isInStandaloneMode) return;
    const btn = document.getElementById('pwa-menu-btn');
    if (btn) btn.style.display = 'flex';
  }

  function hideMenuInstallBtn() {
    const btn = document.getElementById('pwa-menu-btn');
    if (btn) btn.style.display = 'none';
  }

  // ── Fonction globale déclenchée par le bouton ──────────────
  window.triggerPWAInstall = async function () {
    // Fermer le menu
    const menu = document.getElementById('mobileMenu');
    const burger = document.getElementById('burger');
    if (menu) menu.classList.remove('open');
    if (burger) burger.classList.remove('open');
    document.body.style.overflow = '';

    if (isIOS) {
      showIOSInstructions();
      return;
    }

    if (!deferredPrompt) {
      showAlreadyInstalledToast();
      return;
    }

    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    deferredPrompt = null;
    if (outcome === 'accepted') hideMenuInstallBtn();
  };

  // ── Modal instructions iOS ──────────────────────────────────
  function showIOSInstructions() {
    if (document.getElementById('ios-install-modal')) return;
    const modal = document.createElement('div');
    modal.id = 'ios-install-modal';
    modal.innerHTML = `
      <div style="position:fixed;inset:0;z-index:999999;background:rgba(0,0,0,0.7);display:flex;align-items:flex-end;justify-content:center;padding:1rem;animation:pwaFadeIn 0.2s ease" onclick="this.parentElement.remove()">
        <div style="background:#1a1a1a;border-radius:16px;padding:1.5rem;max-width:400px;width:100%;border:1px solid rgba(205,127,50,0.3);animation:pwaSlideUp 0.3s ease" onclick="event.stopPropagation()">
          <div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:1.25rem">
            <img src="/static/images/logo.svg" style="width:40px;height:40px" alt="">
            <div>
              <div style="font-weight:700;color:#f5f5f5;font-size:1rem">Installer l'app</div>
              <div style="color:#888;font-size:0.8125rem">Sur votre écran d'accueil</div>
            </div>
            <button onclick="document.getElementById('ios-install-modal').remove()" style="margin-left:auto;background:transparent;border:none;color:#666;font-size:1.5rem;cursor:pointer;padding:0;line-height:1">✕</button>
          </div>
          <div style="display:flex;flex-direction:column;gap:1rem">
            <div style="display:flex;align-items:center;gap:1rem">
              <div style="width:36px;height:36px;border-radius:8px;flex-shrink:0;background:rgba(205,127,50,0.15);border:1px solid rgba(205,127,50,0.3);display:flex;align-items:center;justify-content:center;color:#cd7f32;font-weight:800">1</div>
              <div style="color:#d0d0d0;font-size:0.9rem">Appuyez sur <strong style="color:#f5f5f5">Partager</strong> <span style="font-size:1.1rem">⎙</span> en bas de Safari</div>
            </div>
            <div style="display:flex;align-items:center;gap:1rem">
              <div style="width:36px;height:36px;border-radius:8px;flex-shrink:0;background:rgba(205,127,50,0.15);border:1px solid rgba(205,127,50,0.3);display:flex;align-items:center;justify-content:center;color:#cd7f32;font-weight:800">2</div>
              <div style="color:#d0d0d0;font-size:0.9rem">Faites défiler puis appuyez sur <strong style="color:#f5f5f5">« Sur l'écran d'accueil »</strong></div>
            </div>
            <div style="display:flex;align-items:center;gap:1rem">
              <div style="width:36px;height:36px;border-radius:8px;flex-shrink:0;background:rgba(205,127,50,0.15);border:1px solid rgba(205,127,50,0.3);display:flex;align-items:center;justify-content:center;color:#cd7f32;font-weight:800">3</div>
              <div style="color:#d0d0d0;font-size:0.9rem">Appuyez sur <strong style="color:#f5f5f5">Ajouter</strong> en haut à droite</div>
            </div>
          </div>
          <button onclick="document.getElementById('ios-install-modal').remove()" style="width:100%;margin-top:1.5rem;padding:0.875rem;background:linear-gradient(135deg,#cd7f32,#b8732f);color:white;border:none;border-radius:10px;font-size:1rem;font-weight:700;cursor:pointer;-webkit-appearance:none">Compris !</button>
        </div>
      </div>
      <style>
        @keyframes pwaFadeIn{from{opacity:0}to{opacity:1}}
        @keyframes pwaSlideUp{from{transform:translateY(40px);opacity:0}to{transform:translateY(0);opacity:1}}
      </style>
    `;
    document.body.appendChild(modal);
  }

  function showAlreadyInstalledToast() {
    const t = document.createElement('div');
    t.style.cssText = 'position:fixed;bottom:1.5rem;left:50%;transform:translateX(-50%);z-index:99999;background:#1a1a1a;border:1px solid rgba(205,127,50,0.4);border-radius:10px;padding:0.75rem 1.25rem;color:#f5f5f5;font-size:0.875rem;font-weight:600;white-space:nowrap;box-shadow:0 4px 20px rgba(0,0,0,0.5);';
    t.textContent = '✓ Application déjà installée';
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 3000);
  }

  // Sur iOS : afficher le bouton d'emblée (pas d'event beforeinstallprompt)
  document.addEventListener('DOMContentLoaded', () => {
    if (isIOS && !isInStandaloneMode) showMenuInstallBtn();
  });

  // ── Toast mise à jour ───────────────────────────────────────
  function showUpdateToast() {
    const t = document.createElement('div');
    t.innerHTML = `<div style="position:fixed;top:80px;left:50%;transform:translateX(-50%);z-index:99999;background:#1a1a1a;border:1px solid rgba(205,127,50,0.4);border-radius:10px;padding:0.75rem 1.25rem;display:flex;align-items:center;gap:0.75rem;box-shadow:0 4px 20px rgba(0,0,0,0.5);white-space:nowrap"><span style="font-size:0.875rem;color:#f5f5f5">Nouvelle version disponible</span><button onclick="location.reload()" style="background:#cd7f32;color:white;border:none;padding:0.35rem 0.85rem;border-radius:6px;font-size:0.8125rem;font-weight:700;cursor:pointer;-webkit-appearance:none">Recharger</button></div>`;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 10000);
  }

  // ── Indicateur reconnexion Socket.IO ───────────────────────
  function initReconnectIndicator(socket) {
    if (!socket || typeof socket.on !== 'function') return;
    const banner = document.createElement('div');
    banner.id = 'reconnect-banner';
    banner.innerHTML = `<div style="position:fixed;top:0;left:0;right:0;z-index:999999;background:rgba(231,76,60,0.95);color:white;text-align:center;padding:0.5rem 1rem;font-size:0.8125rem;font-weight:600;transform:translateY(-100%);transition:transform 0.3s ease;display:flex;align-items:center;justify-content:center;gap:0.5rem"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:white;animation:blink 1s infinite"></span>Connexion perdue — Reconnexion en cours…</div><style>@keyframes blink{0%,100%{opacity:1}50%{opacity:0.3}}</style>`;
    document.body.appendChild(banner);
    const el = banner.firstElementChild;
    socket.on('disconnect', () => { el.style.transform = 'translateY(0)'; });
    socket.on('connect',    () => { el.style.transform = 'translateY(-100%)'; });
    socket.on('reconnect',  () => { el.style.transform = 'translateY(-100%)'; });
  }

  window.initReconnectIndicator = initReconnectIndicator;

})();
