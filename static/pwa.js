// ── PWA Registration — Baby-Foot Club ────────────────────────
// Enregistre le Service Worker et gère le prompt d'installation

(function() {
  'use strict';

  // ── Service Worker ──────────────────────────────────────────
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js', { scope: '/' })
        .then(reg => {
          reg.addEventListener('updatefound', () => {
            const newWorker = reg.installing;
            newWorker.addEventListener('statechange', () => {
              if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
                showUpdateToast();
              }
            });
          });
        })
        .catch(() => {/* silencieux */});
    });
  }

  // ── Détection iOS ───────────────────────────────────────────
  const isIos = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const isInStandaloneMode = window.navigator.standalone === true;

  // ── Install prompt (Android / Chrome) ───────────────────────
  let deferredPrompt = null;

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    injectMenuInstallButton();

    // N'afficher le bandeau que si pas déjà refusé récemment
    const dismissed = localStorage.getItem('pwa_dismissed');
    if (dismissed && Date.now() - parseInt(dismissed) < 7 * 24 * 3600 * 1000) return;
    setTimeout(() => showInstallBanner(), 3000);
  });

  // ── Sur iOS : injecter le bouton dès le chargement ──────────
  if (isIos && !isInStandaloneMode) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', injectMenuInstallButton);
    } else {
      injectMenuInstallButton();
    }
  }

  // ── Injection bouton dans le menu hamburger ─────────────────
  function injectMenuInstallButton() {
    const menu = document.getElementById('mobileMenu');
    if (!menu || document.getElementById('pwa-menu-btn')) return;

    const btn = document.createElement('button');
    btn.id = 'pwa-menu-btn';
    btn.innerHTML = '📲 Installer l\'app';
    btn.style.cssText = [
      'display:block',
      'width:100%',
      'text-align:left',
      'background:linear-gradient(135deg,rgba(205,127,50,0.15),rgba(205,127,50,0.05))',
      'border:1px solid rgba(205,127,50,0.4)',
      'color:#cd7f32',
      'font-weight:700',
      'font-size:1rem',
      'padding:0.9rem 1.25rem',
      'border-radius:10px',
      'cursor:pointer',
      'margin-top:0.5rem',
      'font-family:inherit',
    ].join(';');

    btn.addEventListener('click', () => {
      if (isIos) {
        showIosInstallModal();
      } else if (deferredPrompt) {
        deferredPrompt.prompt();
        deferredPrompt.userChoice.then(() => {
          deferredPrompt = null;
          btn.remove();
        });
      }
    });

    menu.appendChild(btn);
  }

  // ── Modal d'instruction iOS ─────────────────────────────────
  function showIosInstallModal() {
    if (document.getElementById('ios-install-modal')) return;

    const modal = document.createElement('div');
    modal.id = 'ios-install-modal';
    modal.style.cssText = 'position:fixed;inset:0;z-index:999999;background:rgba(0,0,0,0.75);display:flex;align-items:flex-end;justify-content:center;padding:1rem;';

    const box = document.createElement('div');
    box.style.cssText = 'background:#1a1a1a;border:1px solid rgba(205,127,50,0.4);border-radius:16px;padding:1.5rem;width:100%;max-width:400px;text-align:center;';

    box.innerHTML = [
      '<div style="font-size:2rem;margin-bottom:0.5rem">📲</div>',
      '<h3 style="color:#f5f5f5;font-size:1.1rem;margin:0 0 0.5rem">Installer l\'app</h3>',
      '<p style="color:#aaa;font-size:0.875rem;margin:0 0 1.25rem;line-height:1.5">Pour ajouter l\'app à votre écran d\'accueil :</p>',
      '<div style="background:rgba(255,255,255,0.05);border-radius:10px;padding:1rem;text-align:left;margin-bottom:1.25rem;">',
        '<p style="color:#f5f5f5;font-size:0.9rem;margin:0 0 0.6rem;display:flex;align-items:center;gap:0.5rem">',
          '<span>1️⃣</span> Appuyez sur <strong style="color:#cd7f32">Partager</strong> <span style="color:#cd7f32;font-size:1.1rem">⬆️</span>',
        '</p>',
        '<p style="color:#f5f5f5;font-size:0.9rem;margin:0 0 0.6rem;display:flex;align-items:center;gap:0.5rem">',
          '<span>2️⃣</span> Appuyez sur <strong style="color:#cd7f32">Sur l\'écran d\'accueil</strong>',
        '</p>',
        '<p style="color:#f5f5f5;font-size:0.9rem;margin:0;display:flex;align-items:center;gap:0.5rem">',
          '<span>3️⃣</span> Confirmez avec <strong style="color:#cd7f32">Ajouter</strong>',
        '</p>',
      '</div>',
      '<button id="ios-modal-close" style="background:linear-gradient(135deg,#cd7f32,#b8732f);color:white;border:none;padding:0.75rem 2rem;border-radius:10px;font-weight:700;font-size:0.95rem;cursor:pointer;width:100%;-webkit-appearance:none;">Compris !</button>',
    ].join('');

    modal.appendChild(box);
    document.body.appendChild(modal);

    document.getElementById('ios-modal-close').addEventListener('click', () => modal.remove());
    modal.addEventListener('click', (e) => { if (e.target === modal) modal.remove(); });
  }

  // ── Bandeau d'installation (Android) ───────────────────────
  function showInstallBanner() {
    if (!deferredPrompt) return;
    if (document.getElementById('pwa-banner')) return;

    const banner = document.createElement('div');
    banner.id = 'pwa-banner';
    banner.innerHTML = [
      '<div style="position:fixed;bottom:0;left:0;right:0;z-index:99999;',
        'background:linear-gradient(135deg,#1a1a1a,#111);',
        'border-top:1px solid rgba(205,127,50,0.4);',
        'padding:1rem 1.25rem;',
        'display:flex;align-items:center;gap:1rem;',
        'box-shadow:0 -4px 24px rgba(0,0,0,0.5);',
        'animation:slideUp 0.3s ease;">',
        '<img src="/static/images/logo.svg" style="width:36px;height:36px;flex-shrink:0" alt="">',
        '<div style="flex:1;min-width:0">',
          '<div style="font-weight:700;color:#f5f5f5;font-size:0.9rem">Installer l\'app</div>',
          '<div style="color:#888;font-size:0.8rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">Accès rapide depuis l\'écran d\'accueil</div>',
        '</div>',
        '<button id="pwa-install-btn" style="background:linear-gradient(135deg,#cd7f32,#b8732f);color:white;border:none;padding:0.6rem 1.1rem;border-radius:8px;font-weight:700;font-size:0.875rem;cursor:pointer;flex-shrink:0;-webkit-appearance:none;">Installer</button>',
        '<button id="pwa-dismiss-btn" style="background:transparent;border:none;color:#666;font-size:1.25rem;cursor:pointer;padding:0.25rem;flex-shrink:0;">✕</button>',
      '</div>',
      '<style>@keyframes slideUp{from{transform:translateY(100%)}to{transform:translateY(0)}}</style>',
    ].join('');

    document.body.appendChild(banner);

    document.getElementById('pwa-install-btn').addEventListener('click', async () => {
      banner.remove();
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      const { outcome } = await deferredPrompt.userChoice;
      deferredPrompt = null;
    });

    document.getElementById('pwa-dismiss-btn').addEventListener('click', () => {
      banner.remove();
      localStorage.setItem('pwa_dismissed', Date.now().toString());
    });
  }

  // ── Toast de mise à jour disponible ────────────────────────
  function showUpdateToast() {
    const t = document.createElement('div');
    t.innerHTML = [
      '<div style="position:fixed;top:80px;left:50%;transform:translateX(-50%);z-index:99999;',
        'background:#1a1a1a;border:1px solid rgba(205,127,50,0.4);',
        'border-radius:10px;padding:0.75rem 1.25rem;',
        'display:flex;align-items:center;gap:0.75rem;',
        'box-shadow:0 4px 20px rgba(0,0,0,0.5);white-space:nowrap;">',
        '<span style="font-size:0.875rem;color:#f5f5f5">Nouvelle version disponible</span>',
        '<button onclick="location.reload()" style="background:#cd7f32;color:white;border:none;padding:0.35rem 0.85rem;border-radius:6px;font-size:0.8125rem;font-weight:700;cursor:pointer;-webkit-appearance:none;">Recharger</button>',
      '</div>',
    ].join('');
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 10000);
  }

  // ── Indicateur de reconnexion Socket.IO ────────────────────
  function initReconnectIndicator(socket) {
    if (!socket || typeof socket.on !== 'function') return;

    const banner = document.createElement('div');
    banner.id = 'reconnect-banner';
    banner.innerHTML = [
      '<div style="position:fixed;top:0;left:0;right:0;z-index:999999;',
        'background:rgba(231,76,60,0.95);color:white;text-align:center;',
        'padding:0.5rem 1rem;font-size:0.8125rem;font-weight:600;',
        'transform:translateY(-100%);transition:transform 0.3s ease;',
        'display:flex;align-items:center;justify-content:center;gap:0.5rem;">',
        '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:white;animation:blink 1s infinite"></span>',
        'Connexion perdue — Reconnexion en cours…',
      '</div>',
      '<style>@keyframes blink{0%,100%{opacity:1}50%{opacity:0.3}}</style>',
    ].join('');
    document.body.appendChild(banner);
    const el = banner.firstElementChild;

    socket.on('disconnect', () => { el.style.transform = 'translateY(0)'; });
    socket.on('connect', () => { el.style.transform = 'translateY(-100%)'; });
    socket.on('reconnect', () => { el.style.transform = 'translateY(-100%)'; });
  }

  window.initReconnectIndicator = initReconnectIndicator;

})();
