/**
 * profile-utils.js — Utilitaires partagés pour affichage avatar/surnom
 * Inclure dans tous les templates après icons.js
 */

window.ProfileUtils = (() => {
  // Cache local des profils pour éviter N requêtes
  const _cache = {};

  /**
   * Retourne le nom affiché : nickname si défini, sinon username
   */
  function displayName(user) {
    if (!user) return '?';
    return user.nickname && user.nickname.trim() ? user.nickname.trim() : (user.username || '?');
  }

  /**
   * Retourne le contenu HTML complet d'un avatar (emoji, photo ou initiale)
   */
  function avatarHTML(user, size = 36) {
    if (!user) return '<span>?</span>';
    if (user.avatar_url) {
      return `<img src="${user.avatar_url}" style="width:${size}px;height:${size}px;border-radius:50%;object-fit:cover;" alt="">`;
    }
    if (user.avatar_preset) {
      return `<span style="font-size:${Math.round(size * 0.55)}px;line-height:1">${user.avatar_preset}</span>`;
    }
    const initial = (user.username || '?')[0].toUpperCase();
    return `<span style="font-size:${Math.round(size * 0.45)}px;font-weight:700">${initial}</span>`;
  }

  /**
   * Génère le HTML complet d'une "carte joueur" : avatar + surnom (+ username en dessous)
   * Usage dans lobby, live-score, classement…
   */
  function playerCardHTML(user, opts = {}) {
    const { size = 36, showUsername = true, compact = false } = opts;
    const name = displayName(user);
    const isNicknamed = user.nickname && user.nickname.trim();
    const av = avatarHTML(user, size);
    const sub = (showUsername && isNicknamed)
      ? `<span style="font-size:0.68rem;color:var(--text-muted);display:block;line-height:1.1">${user.username}</span>`
      : '';
    if (compact) {
      return `<span style="display:inline-flex;align-items:center;gap:6px">
        <span style="width:${size}px;height:${size}px;border-radius:50%;background:var(--bg-tertiary);display:inline-flex;align-items:center;justify-content:center;flex-shrink:0;overflow:hidden">${av}</span>
        <span><span style="font-weight:600;font-size:0.875rem">${name}</span>${sub}</span>
      </span>`;
    }
    return `<div style="display:flex;align-items:center;gap:10px">
      <div style="width:${size}px;height:${size}px;border-radius:50%;background:var(--bg-tertiary);display:flex;align-items:center;justify-content:center;flex-shrink:0;overflow:hidden">${av}</div>
      <div><div style="font-weight:600;font-size:0.9rem;line-height:1.2">${name}</div>${sub}</div>
    </div>`;
  }

  /**
   * Met à jour la nav (avatar + username) depuis les données user
   */
  function updateNav(user) {
    const navAv = document.getElementById('navAv');
    const navUsername = document.getElementById('navUsername');
    if (!navAv || !user) return;
    if (user.avatar_url) {
      navAv.innerHTML = `<img src="${user.avatar_url}" style="width:100%;height:100%;object-fit:cover;border-radius:50%" alt="">`;
    } else if (user.avatar_preset) {
      navAv.textContent = user.avatar_preset;
    } else {
      navAv.textContent = (user.username || '?')[0].toUpperCase();
    }
    if (navUsername) {
      navUsername.textContent = displayName(user);
    }
  }

  /**
   * Charge les infos d'un utilisateur (depuis /users_list ou cache)
   */
  async function fetchUserProfile(username) {
    if (_cache[username]) return _cache[username];
    try {
      const res = await fetch('/users_list');
      const list = await res.json();
      (Array.isArray(list) ? list : []).forEach(u => {
        if (u && u.username) _cache[u.username] = u;
      });
    } catch (e) {}
    return _cache[username] || { username };
  }

  /**
   * Pré-charge tous les profils et retourne un Map username->user
   */
  async function fetchAllProfiles() {
    try {
      const res = await fetch('/users_list');
      const list = await res.json();
      (Array.isArray(list) ? list : []).forEach(u => {
        if (u && u.username) _cache[u.username] = u;
      });
    } catch (e) {}
    return _cache;
  }

  function _cache_set(username, data) { _cache[username] = data; }

  return { displayName, avatarHTML, playerCardHTML, updateNav, fetchUserProfile, fetchAllProfiles, _cache_set };
})();
