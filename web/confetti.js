// Vanilla canvas confetti burst (~40 lines, no CDN, no library).
// Call launchConfetti() once, e.g. on a Results screen of 80% or better.
(function () {
  'use strict';
  // Editorial palette only: terracotta, gold, green, amber, ink, warm border.
  var COLORS = ['#B44A1F', '#A87B00', '#2F7D4F', '#D97706', '#1D2A30', '#E4DCCC'];
  function launchConfetti() {
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    var canvas = document.createElement('canvas');
    canvas.className = 'confetti-canvas';
    canvas.setAttribute('aria-hidden', 'true');
    document.body.appendChild(canvas);
    var ctx = canvas.getContext('2d');
    function resize() { canvas.width = window.innerWidth; canvas.height = window.innerHeight; }
    resize();
    window.addEventListener('resize', resize);
    var pieces = [];
    for (var i = 0; i < 120; i++) {
      pieces.push({
        x: window.innerWidth / 2 + (Math.random() - 0.5) * 220,
        y: window.innerHeight * 0.3,
        vx: (Math.random() - 0.5) * 11, vy: -Math.random() * 11 - 3,
        w: 6 + Math.random() * 6, h: 8 + Math.random() * 8,
        color: COLORS[i % COLORS.length], rot: Math.random() * Math.PI, vr: (Math.random() - 0.5) * 0.3
      });
    }
    var start = performance.now();
    function frame(now) {
      var t = (now - start) / 1000;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      pieces.forEach(function (p) {
        p.vy += 0.22; p.x += p.vx; p.y += p.vy; p.rot += p.vr;
        ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot);
        ctx.fillStyle = p.color; ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h); ctx.restore();
      });
      if (t < 3) requestAnimationFrame(frame);
      else { window.removeEventListener('resize', resize); canvas.remove(); }
    }
    requestAnimationFrame(frame);
  }
  window.launchConfetti = launchConfetti;
})();
