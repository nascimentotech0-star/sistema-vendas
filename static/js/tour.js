/**
 * tour.js — Sistema de tutorial passo a passo
 * Uso: Tour.start(steps) ou Tour.autoStart(steps, key)
 * key: chave única por perfil para o localStorage
 */
const Tour = (() => {

  let steps = [], idx = 0, overlay = null, box = null, spotlight = null;
  let _storageKey = 'nt_tour_done';

  function $(sel) { return document.querySelector(sel); }

  /* ── Overlay + caixa de dica ────────────────────────────────────────────── */
  function createOverlay() {
    overlay = document.createElement('div');
    overlay.id = 'tourOverlay';
    overlay.style.cssText = `
      position:fixed; inset:0; z-index:99990;
      background:rgba(0,0,0,.55);
      pointer-events:none;
      transition:background .3s;
    `;

    spotlight = document.createElement('div');
    spotlight.id = 'tourSpotlight';
    spotlight.style.cssText = `
      position:fixed; border-radius:12px; z-index:99991;
      box-shadow:0 0 0 9999px rgba(0,0,0,.55);
      transition:all .35s cubic-bezier(.4,0,.2,1);
      pointer-events:none;
    `;

    box = document.createElement('div');
    box.id = 'tourBox';
    box.style.cssText = `
      position:fixed; z-index:99992;
      background:#1a1a35; border:1px solid rgba(124,58,237,.5);
      border-radius:14px; padding:20px 22px; width:310px; max-width:90vw;
      box-shadow:0 8px 40px rgba(0,0,0,.6);
      transition:all .3s cubic-bezier(.4,0,.2,1);
      pointer-events:auto;
    `;

    document.body.appendChild(overlay);
    document.body.appendChild(spotlight);
    document.body.appendChild(box);
  }

  function destroyOverlay() {
    [overlay, spotlight, box].forEach(el => el && el.remove());
    overlay = spotlight = box = null;
  }

  /* ── Posicionar caixa perto do elemento ─────────────────────────────────── */
  function positionBox(el) {
    const margin = 14;
    if (!el) {
      // Centralizado
      spotlight.style.cssText += 'width:0;height:0;top:50%;left:50%;';
      box.style.top    = '50%';
      box.style.left   = '50%';
      box.style.transform = 'translate(-50%,-50%)';
      return;
    }

    const r = el.getBoundingClientRect();
    const pad = 8;

    // Spotlight sobre o elemento
    spotlight.style.top    = (r.top  - pad) + 'px';
    spotlight.style.left   = (r.left - pad) + 'px';
    spotlight.style.width  = (r.width  + pad*2) + 'px';
    spotlight.style.height = (r.height + pad*2) + 'px';

    // Caixa: abaixo ou acima dependendo do espaço
    const boxH = 200;
    const boxW = 310;
    box.style.transform = '';

    let top, left;

    if (r.bottom + margin + boxH < window.innerHeight) {
      top = r.bottom + margin;           // abaixo
    } else {
      top = Math.max(10, r.top - margin - boxH); // acima
    }

    left = r.left;
    if (left + boxW > window.innerWidth - 10) {
      left = window.innerWidth - boxW - 10;
    }
    if (left < 10) left = 10;

    box.style.top  = top  + 'px';
    box.style.left = left + 'px';

    // Scroll para que o elemento fique visível
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  /* ── Renderizar passo ────────────────────────────────────────────────────── */
  function render() {
    const step = steps[idx];
    const el   = step.selector ? $(step.selector) : null;
    const total = steps.length;
    const isLast = idx === total - 1;

    positionBox(el);

    const dots = steps.map((_, i) =>
      `<span style="display:inline-block;width:7px;height:7px;border-radius:50%;margin:0 3px;
        background:${i===idx ? '#a78bfa' : 'rgba(255,255,255,.2)'}"></span>`
    ).join('');

    box.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;">
        <div style="display:flex;align-items:center;gap:8px;">
          <div style="width:30px;height:30px;border-radius:8px;background:rgba(124,58,237,.3);
               display:flex;align-items:center;justify-content:center;font-size:1rem;">
            ${step.icon || '💡'}
          </div>
          <span style="font-size:.7rem;color:#a78bfa;font-weight:700;text-transform:uppercase;letter-spacing:.06em;">
            Passo ${idx+1} de ${total}
          </span>
        </div>
        <button id="tourClose" style="background:none;border:none;color:#7b7b9e;font-size:1.1rem;cursor:pointer;padding:2px 6px;border-radius:6px;line-height:1;"
                title="Fechar tutorial">✕</button>
      </div>

      <h6 style="margin:0 0 8px;font-size:.95rem;font-weight:700;color:#e0e0f0;">
        ${step.title}
      </h6>
      <p style="margin:0 0 16px;font-size:.83rem;color:#94a3b8;line-height:1.55;">
        ${step.text}
      </p>

      <div style="display:flex;align-items:center;justify-content:space-between;">
        <div>${dots}</div>
        <div style="display:flex;gap:8px;">
          ${idx > 0 ? `<button id="tourPrev" style="background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.1);color:#94a3b8;border-radius:8px;padding:6px 14px;cursor:pointer;font-size:.78rem;">← Anterior</button>` : ''}
          <button id="tourNext" style="background:linear-gradient(135deg,#7c3aed,#06b6d4);border:none;color:#fff;border-radius:8px;padding:6px 16px;cursor:pointer;font-size:.82rem;font-weight:600;">
            ${isLast ? '✓ Concluir' : 'Próximo →'}
          </button>
        </div>
      </div>
    `;

    document.getElementById('tourNext').onclick  = next;
    document.getElementById('tourClose').onclick = finish;
    const prev = document.getElementById('tourPrev');
    if (prev) prev.onclick = back;
  }

  function next()   { if (idx < steps.length - 1) { idx++; render(); } else finish(); }
  function back()   { if (idx > 0) { idx--; render(); } }
  function finish() {
    destroyOverlay();
    localStorage.setItem(_storageKey, '1');
  }

  /* ── API pública ─────────────────────────────────────────────────────────── */
  function start(tourSteps) {
    if (overlay) destroyOverlay();
    steps = tourSteps;
    idx   = 0;
    createOverlay();
    render();
  }

  function autoStart(tourSteps, key) {
    if (key) _storageKey = key;
    if (!localStorage.getItem(_storageKey)) {
      // Aguarda o DOM estar estável
      setTimeout(() => start(tourSteps), 800);
    }
  }

  function reset() {
    localStorage.removeItem(_storageKey);
  }

  return { start, autoStart, reset };
})();
