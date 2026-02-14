/**
 * ANIMATIONS GLOBALES - Injectées automatiquement sur toutes les pages
 * Ce script ajoute les formes flottantes et l'effet de glow partout
 */

(function() {
  'use strict';
  
  // Créer le conteneur de formes flottantes
  function createFloatingShapes() {
    // Vérifier si déjà créé
    if (document.querySelector('.floating-shapes-global')) {
      return;
    }
    
    // Créer le conteneur principal
    const container = document.createElement('div');
    container.className = 'floating-shapes-global';
    
    // Créer les 5 formes flottantes
    for (let i = 1; i <= 5; i++) {
      const shape = document.createElement('div');
      shape.className = `floating-shape-global shape-global-${i}`;
      container.appendChild(shape);
    }
    
    // Ajouter au début du body
    document.body.insertBefore(container, document.body.firstChild);
    console.log('✨ Formes flottantes ajoutées');
  }
  
  // Créer l'effet de glow pulsant
  function createGlowPulse() {
    // Vérifier si déjà créé
    if (document.querySelector('.glow-pulse-global')) {
      return;
    }
    
    const glow = document.createElement('div');
    glow.className = 'glow-pulse-global';
    document.body.insertBefore(glow, document.body.firstChild);
    console.log('💫 Effet glow ajouté');
  }
  
  // Initialiser quand le DOM est prêt
  function init() {
    createFloatingShapes();
    createGlowPulse();
  }
  
  // Lancer au chargement
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
