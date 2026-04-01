/**
 * chart-futuristic.js — Nascimento Tech
 * Configuração global do Chart.js com visual neon/futurista.
 * Carregado uma vez via base.html, aplica-se a todos os gráficos.
 */

(function () {
  /* ── Paleta neon ── */
  const N = {
    purple:  '#a78bfa',
    cyan:    '#22d3ee',
    green:   '#34d399',
    pink:    '#f472b6',
    yellow:  '#fbbf24',
    blue:    '#60a5fa',
    gridLine:'rgba(167,139,250,.08)',
    gridDot: 'rgba(34,211,238,.06)',
    tick:    '#475569',
    bg:      'rgba(8,10,20,.0)',
  };

  /* ── Gradiente vertical helper ── */
  window.ntGrad = function (ctx, colorTop, colorBot, h) {
    const g = ctx.createLinearGradient(0, 0, 0, h || 200);
    g.addColorStop(0, colorTop);
    g.addColorStop(1, colorBot);
    return g;
  };

  /* ── Plugin: glow nos elementos desenhados ── */
  const glowPlugin = {
    id: 'nt-glow',
    beforeDatasetsDraw(chart) {
      chart.ctx.save();
    },
    afterDatasetsDraw(chart) {
      chart.ctx.restore();
    },
    /* Aplica sombra colorida em cada dataset */
    beforeDraw(chart) {
      const ctx = chart.ctx;
      ctx.save();
      ctx.shadowBlur   = 18;
      ctx.shadowOffsetX = 0;
      ctx.shadowOffsetY = 0;
    },
    afterDraw(chart) {
      chart.ctx.restore();
    },
  };

  /* ── Plugin: fundo escuro com grid de pontos no canvas ── */
  const bgPlugin = {
    id: 'nt-bg',
    beforeDraw(chart) {
      const { ctx, width, height } = chart;
      ctx.save();
      ctx.fillStyle = 'rgba(8,10,22,.55)';
      ctx.roundRect(0, 0, width, height, 12);
      ctx.fill();

      /* Grade de pontos */
      const step = 22;
      ctx.fillStyle = 'rgba(99,102,241,.07)';
      for (let x = step; x < width; x += step) {
        for (let y = step; y < height; y += step) {
          ctx.beginPath();
          ctx.arc(x, y, .8, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.restore();
    },
  };

  Chart.register(bgPlugin);

  /* ── Defaults globais ── */
  Chart.defaults.color                      = N.tick;
  Chart.defaults.font.family                = "'JetBrains Mono','Fira Code',monospace";
  Chart.defaults.font.size                  = 10;
  Chart.defaults.borderColor                = N.gridLine;
  Chart.defaults.animation.duration         = 900;
  Chart.defaults.animation.easing           = 'easeOutQuart';

  /* Escala padrão */
  Chart.defaults.scale.grid.color           = N.gridLine;
  Chart.defaults.scale.grid.borderColor     = 'transparent';
  Chart.defaults.scale.ticks.color          = N.tick;
  Chart.defaults.scale.ticks.padding        = 6;

  /* Tooltip glassmorphism */
  Chart.defaults.plugins.tooltip.backgroundColor = 'rgba(8,10,24,.92)';
  Chart.defaults.plugins.tooltip.borderColor      = 'rgba(167,139,250,.35)';
  Chart.defaults.plugins.tooltip.borderWidth      = 1;
  Chart.defaults.plugins.tooltip.titleColor       = '#c4b5fd';
  Chart.defaults.plugins.tooltip.bodyColor        = '#94a3b8';
  Chart.defaults.plugins.tooltip.padding          = 10;
  Chart.defaults.plugins.tooltip.cornerRadius     = 10;
  Chart.defaults.plugins.tooltip.titleFont        = { family: "'Inter',sans-serif", weight: '600', size: 11 };
  Chart.defaults.plugins.tooltip.bodyFont         = { family: "'JetBrains Mono',monospace", size: 10 };
  Chart.defaults.plugins.tooltip.callbacks = {
    ...Chart.defaults.plugins.tooltip.callbacks,
    label: function (ctx) {
      const v = ctx.parsed.y ?? ctx.parsed;
      if (typeof v === 'number' && v > 10) {
        return '  R$ ' + v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      }
      return '  ' + v;
    },
  };

  /* ── Helpers públicos para criar gráficos futuristas ── */

  /** Gradiente neon para barras */
  window.ntBarGrad = function (ctx, color, alpha1, alpha2, height) {
    const h = height || 200;
    const g = ctx.createLinearGradient(0, 0, 0, h);
    const c = color.replace('#', '');
    const r = parseInt(c.slice(0,2),16), gb = parseInt(c.slice(2,4),16), b = parseInt(c.slice(4,6),16);
    g.addColorStop(0, `rgba(${r},${gb},${b},${alpha1 ?? .9})`);
    g.addColorStop(1, `rgba(${r},${gb},${b},${alpha2 ?? .2})`);
    return g;
  };

  /** Dataset de linha neon padrão */
  window.ntLineDataset = function (label, data, color, ctx, fillHeight) {
    const fill = ctx ? ntGrad(ctx, hexAlpha(color, .35), hexAlpha(color, .02), fillHeight) : false;
    return {
      label,
      data,
      borderColor:          color,
      borderWidth:          2,
      backgroundColor:      fill,
      fill:                 !!ctx,
      tension:              0.45,
      pointBackgroundColor: color,
      pointBorderColor:     'rgba(8,10,22,.8)',
      pointBorderWidth:     1.5,
      pointRadius:          4,
      pointHoverRadius:     7,
      pointHoverBackgroundColor: color,
      pointHoverBorderColor:     '#fff',
      pointHoverBorderWidth:     2,
      shadowColor:          color,
      shadowBlur:           10,
    };
  };

  /** Dataset de barras neon padrão */
  window.ntBarDataset = function (label, data, color, ctx, fillHeight) {
    const bg = ctx ? ntBarGrad(ctx, color, .85, .25, fillHeight) : color;
    return {
      label,
      data,
      backgroundColor:      bg,
      borderColor:          color,
      borderWidth:          1,
      borderRadius:         8,
      borderSkipped:        false,
      hoverBackgroundColor: color,
      hoverBorderWidth:     0,
    };
  };

  /** Opções de escala padrão futurista */
  window.ntScales = function (yCallback) {
    const cb = yCallback || (v => 'R$' + (v >= 1000 ? (v/1000).toFixed(0)+'k' : v.toLocaleString('pt-BR')));
    return {
      x: {
        grid:  { color: N.gridLine, lineWidth: 1 },
        ticks: { color: N.tick },
        border: { display: false },
      },
      y: {
        grid:  { color: N.gridLine, lineWidth: 1 },
        ticks: { color: N.tick, callback: cb },
        border: { display: false },
      },
    };
  };

  /** Opções base para todos os gráficos */
  window.ntOptions = function (extra) {
    return Object.assign({
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
      },
      scales: ntScales(),
      animation: { duration: 1000, easing: 'easeOutQuart' },
    }, extra || {});
  };

  /* Utilidade interna */
  function hexAlpha(hex, a) {
    const c = hex.replace('#','');
    const r = parseInt(c.slice(0,2),16), g = parseInt(c.slice(2,4),16), b = parseInt(c.slice(4,6),16);
    return `rgba(${r},${g},${b},${a})`;
  }

  /* Expor paleta */
  window.NT_COLORS = N;
})();
