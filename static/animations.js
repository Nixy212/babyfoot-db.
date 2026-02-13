// ============================================
// BABY-FOOT CLUB - Animations Vivantes
// ============================================

document.addEventListener('DOMContentLoaded', function() {
  
  // ======== NAVBAR SCROLL EFFECT ========
  const nav = document.querySelector('.nav');
  
  window.addEventListener('scroll', () => {
    if (window.pageYOffset > 50) {
      nav.classList.add('scrolled');
    } else {
      nav.classList.remove('scrolled');
    }
  });
  
  // ======== SMOOTH SCROLL ========
  document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
      const href = this.getAttribute('href');
      if (href !== '#' && href.length > 1) {
        const target = document.querySelector(href);
        if (target) {
          e.preventDefault();
          target.scrollIntoView({
            behavior: 'smooth',
            block: 'start'
          });
        }
      }
    });
  });
  
  // ======== PARALLAX SUR LES ORBES ========
  const orbs = document.querySelectorAll('.glow-orb');
  if (orbs.length > 0) {
    let ticking = false;
    
    window.addEventListener('mousemove', (e) => {
      if (!ticking) {
        window.requestAnimationFrame(() => {
          const mouseX = e.clientX / window.innerWidth;
          const mouseY = e.clientY / window.innerHeight;
          
          orbs.forEach((orb, index) => {
            const speed = (index + 1) * 20;
            const x = (mouseX - 0.5) * speed;
            const y = (mouseY - 0.5) * speed;
            
            orb.style.transform = `translate(${x}px, ${y}px)`;
          });
          
          ticking = false;
        });
        
        ticking = true;
      }
    });
  }
  
  // ======== RIPPLE EFFECT SUR BOUTONS ========
  document.querySelectorAll('.btn').forEach(button => {
    button.addEventListener('click', function(e) {
      const ripple = document.createElement('span');
      const rect = this.getBoundingClientRect();
      const size = Math.max(rect.width, rect.height);
      const x = e.clientX - rect.left - size / 2;
      const y = e.clientY - rect.top - size / 2;
      
      ripple.style.cssText = `
        position: absolute;
        width: ${size}px;
        height: ${size}px;
        left: ${x}px;
        top: ${y}px;
        border-radius: 50%;
        background: rgba(255, 255, 255, 0.5);
        transform: scale(0);
        animation: ripple-effect 0.6s ease-out;
        pointer-events: none;
      `;
      
      this.appendChild(ripple);
      
      setTimeout(() => {
        ripple.remove();
      }, 600);
    });
  });
  
  // Ajouter keyframe pour ripple
  if (!document.querySelector('#ripple-keyframes')) {
    const style = document.createElement('style');
    style.id = 'ripple-keyframes';
    style.textContent = `
      @keyframes ripple-effect {
        to {
          transform: scale(4);
          opacity: 0;
        }
      }
    `;
    document.head.appendChild(style);
  }
  
  // ======== COMPTEURS ANIMÉS ========
  function animateCounter(element, target, duration) {
    let startTimestamp = null;
    const start = 0;
    const originalText = element.textContent;
    const suffix = originalText.replace(/[0-9]/g, '');
    
    const step = (timestamp) => {
      if (!startTimestamp) startTimestamp = timestamp;
      const progress = Math.min((timestamp - startTimestamp) / duration, 1);
      const value = Math.floor(progress * target);
      element.textContent = value + suffix;
      
      if (progress < 1) {
        window.requestAnimationFrame(step);
      } else {
        element.textContent = originalText;
      }
    };
    
    window.requestAnimationFrame(step);
  }
  
  // Observer pour les stats
  const statsObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting && !entry.target.dataset.animated) {
        entry.target.dataset.animated = 'true';
        
        const statValues = entry.target.querySelectorAll('.stat-value');
        statValues.forEach((stat, index) => {
          const text = stat.textContent;
          const number = parseInt(text.replace(/\D/g, ''));
          
          if (!isNaN(number)) {
            setTimeout(() => {
              animateCounter(stat, number, 2000);
            }, index * 200);
          }
        });
      }
    });
  }, { threshold: 0.5 });
  
  const heroStats = document.querySelector('.hero-stats');
  if (heroStats) {
    statsObserver.observe(heroStats);
  }
  
  // ======== INTERSECTION OBSERVER POUR FADE-IN ========
  const fadeObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.style.opacity = '1';
        entry.target.style.transform = 'translateY(0)';
      }
    });
  }, {
    threshold: 0.1,
    rootMargin: '0px 0px -100px 0px'
  });
  
  // Observer les feature cells
  document.querySelectorAll('.feat-cell').forEach((cell, index) => {
    cell.style.opacity = '0';
    cell.style.transform = 'translateY(40px)';
    cell.style.transition = `opacity 0.8s ease ${index * 0.1}s, transform 0.8s ease ${index * 0.1}s`;
    fadeObserver.observe(cell);
  });
  
  // ======== HOVER 3D SUR CARDS ========
  document.querySelectorAll('.feat-cell').forEach(cell => {
    cell.addEventListener('mousemove', function(e) {
      const rect = this.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      
      const centerX = rect.width / 2;
      const centerY = rect.height / 2;
      
      const rotateX = (y - centerY) / 30;
      const rotateY = (centerX - x) / 30;
      
      this.style.transform = `perspective(1000px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) translateY(-12px) scale(1.02)`;
    });
    
    cell.addEventListener('mouseleave', function() {
      this.style.transform = 'perspective(1000px) rotateX(0deg) rotateY(0deg) translateY(0) scale(1)';
    });
  });
  
  // ======== CURSOR CUSTOM (Desktop only) ========
  if (window.innerWidth > 768) {
    const cursor = document.createElement('div');
    cursor.className = 'custom-cursor';
    document.body.appendChild(cursor);
    
    let cursorX = 0;
    let cursorY = 0;
    let targetX = 0;
    let targetY = 0;
    
    document.addEventListener('mousemove', (e) => {
      targetX = e.clientX;
      targetY = e.clientY;
    });
    
    function animateCursor() {
      cursorX += (targetX - cursorX) * 0.15;
      cursorY += (targetY - cursorY) * 0.15;
      
      cursor.style.left = cursorX + 'px';
      cursor.style.top = cursorY + 'px';
      
      requestAnimationFrame(animateCursor);
    }
    
    animateCursor();
    
    // Style du cursor
    const cursorStyle = document.createElement('style');
    cursorStyle.textContent = `
      .custom-cursor {
        position: fixed;
        width: 40px;
        height: 40px;
        border: 2px solid rgba(205, 127, 50, 0.5);
        border-radius: 50%;
        pointer-events: none;
        z-index: 9999;
        transform: translate(-50%, -50%);
        transition: width 0.3s, height 0.3s, border-color 0.3s;
      }
      
      .custom-cursor.active {
        width: 60px;
        height: 60px;
        border-color: rgba(255, 215, 0, 0.8);
      }
    `;
    document.head.appendChild(cursorStyle);
    
    // Agrandir au hover sur liens et boutons
    document.querySelectorAll('a, button, .btn').forEach(el => {
      el.addEventListener('mouseenter', () => cursor.classList.add('active'));
      el.addEventListener('mouseleave', () => cursor.classList.remove('active'));
    });
  }
  
  // ======== PARTICULES FLOTTANTES (subtiles) ========
  function createParticle() {
    if (window.innerWidth <= 768) return; // Pas sur mobile
    
    const particle = document.createElement('div');
    particle.className = 'floating-particle';
    
    const size = Math.random() * 4 + 2;
    const startX = Math.random() * window.innerWidth;
    const duration = Math.random() * 20 + 20;
    const delay = Math.random() * 5;
    
    particle.style.cssText = `
      position: fixed;
      width: ${size}px;
      height: ${size}px;
      background: radial-gradient(circle, rgba(205, 127, 50, 0.6), transparent);
      border-radius: 50%;
      pointer-events: none;
      z-index: 1;
      left: ${startX}px;
      bottom: -20px;
      animation: float-up ${duration}s linear ${delay}s;
      opacity: 0;
    `;
    
    document.body.appendChild(particle);
    
    setTimeout(() => {
      particle.remove();
    }, (duration + delay) * 1000);
  }
  
  // Style des particules
  const particleStyle = document.createElement('style');
  particleStyle.textContent = `
    @keyframes float-up {
      0% {
        transform: translateY(0) translateX(0);
        opacity: 0;
      }
      10% {
        opacity: 0.6;
      }
      90% {
        opacity: 0.3;
      }
      100% {
        transform: translateY(-100vh) translateX(${Math.random() * 100 - 50}px);
        opacity: 0;
      }
    }
  `;
  document.head.appendChild(particleStyle);
  
  // Créer des particules périodiquement
  if (window.innerWidth > 768) {
    setInterval(createParticle, 3000);
  }
  
  // ======== LOADING ANIMATION ========
  window.addEventListener('load', () => {
    document.body.style.opacity = '0';
    document.body.style.transition = 'opacity 0.5s';
    
    setTimeout(() => {
      document.body.style.opacity = '1';
    }, 100);
  });
  
  // ======== CONSOLE MESSAGE ========
  console.log('%c⚽ Baby-Foot Club', 'font-size: 24px; font-weight: bold; color: #cd7f32; text-shadow: 2px 2px 4px rgba(0,0,0,0.3);');
  console.log('%c✨ Version Vivante Chargée!', 'font-size: 14px; color: #ffd700;');
  
});
