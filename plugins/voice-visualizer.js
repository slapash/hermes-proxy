/**
 * Voice Visualizer Plugin for Hermes Proxy
 *
 * Hooks into voice events dispatched by app.js and renders
 * a basic blob/bars animation into the voice canvas.
 *
 * Plugin contract: listens to HermesProxy events:
 *   voice:start   — recording started
 *   voice:result  — interim/final transcript
 *   voice:end     — recording stopped
 *   voice:error   — recording error
 *
 * The plugin enhances the built-in canvas animation with
 * more elaborate visuals. If the plugin is absent, app.js
 * still provides basic blob animation as a fallback.
 */
(() => {
  const canvas = document.getElementById('voice-canvas');
  const viz = document.getElementById('voice-viz');
  if (!canvas || !viz) return;

  const ctx = canvas.getContext('2d');
  let animId = null;
  let isRecording = false;

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { w, h };
  }

  function makeBlobs(count, w, h) {
    return Array.from({ length: count }, (_, i) => ({
      x: (w / (count + 1)) * (i + 1),
      y: h / 2,
      baseR: 3 + Math.random() * 5,
      phase: Math.random() * Math.PI * 2,
      speed: 0.6 + Math.random() * 1.4,
      color: '#00ff88',
    }));
  }

  let blobs = [];
  let t = 0;

  function draw() {
    const { w, h } = resize();
    if (!blobs.length) blobs = makeBlobs(7, w, h);

    ctx.clearRect(0, 0, w, h);

    blobs.forEach(b => {
      const r = b.baseR + Math.sin(t * b.speed + b.phase) * (b.baseR * 0.8);
      ctx.beginPath();
      ctx.arc(b.x, b.y, Math.max(1.5, r), 0, Math.PI * 2);
      ctx.fillStyle = b.color;
      ctx.globalAlpha = 0.55;
      ctx.fill();
    });

    ctx.globalAlpha = 1;
    t += 0.04;
    animId = requestAnimationFrame(draw);
  }

  function start() {
    if (animId) cancelAnimationFrame(animId);
    isRecording = true;
    blobs = [];
    t = 0;
    draw();
  }

  function stop() {
    isRecording = false;
    if (animId) cancelAnimationFrame(animId);
    animId = null;
    if (canvas) {
      resize();
      ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
    }
  }

  // Hook into HermesProxy event bus
  const proxy = window.HermesProxy;
  if (!proxy) return;

  proxy.on('voice:start', start);
  proxy.on('voice:end', stop);
  proxy.on('voice:error', () => {
    // Show a brief red flash then stop
    if (!canvas) return;
    const { w, h } = resize();
    ctx.fillStyle = '#ff4466';
    ctx.globalAlpha = 0.2;
    ctx.fillRect(0, 0, w, h);
    ctx.globalAlpha = 1;
    setTimeout(stop, 400);
  });
})();
