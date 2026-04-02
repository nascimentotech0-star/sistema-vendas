/**
 * chart-futuristic.js — Nascimento Tech
 * Configuração global Chart.js estilo trader/ECG.
 * Carregado uma vez via base.html — aplica-se a todos os gráficos.
 */

(function () {

  /* ── Paleta neon ── */
  var N = {
    purple:  '#a78bfa',
    cyan:    '#22d3ee',
    green:   '#34d399',
    pink:    '#f472b6',
    yellow:  '#fbbf24',
    blue:    '#60a5fa',
    gridLine:'rgba(167,139,250,.07)',
    tick:    '#475569',
  };

  /* ── Utilidade: hex → rgba ── */
  function hexAlpha(hex, a) {
    var c = hex.replace('#','');
    var r = parseInt(c.slice(0,2),16);
    var g = parseInt(c.slice(2,4),16);
    var b = parseInt(c.slice(4,6),16);
    return 'rgba('+r+','+g+','+b+','+a+')';
  }

  /* ── Gradiente vertical ── */
  window.ntGrad = function (ctx, colorTop, colorBot, h) {
    var g = ctx.createLinearGradient(0, 0, 0, h || 200);
    g.addColorStop(0, colorTop);
    g.addColorStop(1, colorBot);
    return g;
  };

  /* ── Gradiente para barras ── */
  window.ntBarGrad = function (ctx, color, alpha1, alpha2, height) {
    var h = height || 200;
    var g = ctx.createLinearGradient(0, 0, 0, h);
    g.addColorStop(0, hexAlpha(color, alpha1 !== undefined ? alpha1 : .85));
    g.addColorStop(1, hexAlpha(color, alpha2 !== undefined ? alpha2 : .15));
    return g;
  };

  /* ── Plugin: fundo escuro com grade de pontos ── */
  var bgPlugin = {
    id: 'nt-bg',
    beforeDraw: function(chart) {
      var ctx = chart.ctx, w = chart.width, h = chart.height;
      ctx.save();
      ctx.fillStyle = 'rgba(6,9,20,.6)';
      if (ctx.roundRect) ctx.roundRect(0, 0, w, h, 10);
      else ctx.rect(0, 0, w, h);
      ctx.fill();
      // grade de pontos roxa
      var step = 22;
      ctx.fillStyle = 'rgba(99,102,241,.06)';
      for (var x = step; x < w; x += step) {
        for (var y = step; y < h; y += step) {
          ctx.beginPath();
          ctx.arc(x, y, .7, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.restore();
    },
  };

  /* ── Plugin: glow real na linha (sombra colorida) ── */
  var lineGlowPlugin = {
    id: 'nt-line-glow',
    beforeDatasetDraw: function(chart, args) {
      var ds = chart.data.datasets[args.index];
      if (!ds || ds.type === 'bar') return;
      var ctx = chart.ctx;
      ctx.save();
      ctx.shadowColor  = ds.borderColor || '#22d3ee';
      ctx.shadowBlur   = 14;
      ctx.shadowOffsetX = 0;
      ctx.shadowOffsetY = 0;
    },
    afterDatasetDraw: function(chart, args) {
      var ds = chart.data.datasets[args.index];
      if (!ds || ds.type === 'bar') return;
      chart.ctx.restore();
    },
  };

  Chart.register(bgPlugin, lineGlowPlugin);

  /* ── Defaults globais ── */
  Chart.defaults.color               = N.tick;
  Chart.defaults.font.family         = "'JetBrains Mono','Fira Code',monospace";
  Chart.defaults.font.size           = 10;
  Chart.defaults.borderColor         = N.gridLine;
  Chart.defaults.animation.duration  = 1100;
  Chart.defaults.animation.easing    = 'easeOutQuart';

  Chart.defaults.scale.grid.color        = N.gridLine;
  Chart.defaults.scale.grid.borderColor  = 'transparent';
  Chart.defaults.scale.ticks.color       = N.tick;
  Chart.defaults.scale.ticks.padding     = 6;

  /* Tooltip glassmorphism */
  Chart.defaults.plugins.tooltip.backgroundColor = 'rgba(6,9,20,.92)';
  Chart.defaults.plugins.tooltip.borderColor      = 'rgba(167,139,250,.4)';
  Chart.defaults.plugins.tooltip.borderWidth      = 1;
  Chart.defaults.plugins.tooltip.titleColor       = '#c4b5fd';
  Chart.defaults.plugins.tooltip.bodyColor        = '#94a3b8';
  Chart.defaults.plugins.tooltip.padding          = 10;
  Chart.defaults.plugins.tooltip.cornerRadius     = 10;
  Chart.defaults.plugins.tooltip.displayColors    = false;
  Chart.defaults.plugins.tooltip.titleFont        = { family:"'Inter',sans-serif", weight:'600', size:11 };
  Chart.defaults.plugins.tooltip.bodyFont         = { family:"'JetBrains Mono',monospace", size:10 };
  Chart.defaults.plugins.tooltip.callbacks.label  = function(ctx) {
    var v = ctx.parsed.y;
    if (typeof v === 'number') {
      return '  R$ ' + v.toLocaleString('pt-BR', {minimumFractionDigits:2, maximumFractionDigits:2});
    }
    return '  ' + v;
  };

  /* ── Dataset trader/ECG — linha angular sem pontos ── */
  window.ntLineDataset = function (label, data, color, ctx, fillHeight, opts) {
    opts = opts || {};
    var fill = ctx
      ? ntGrad(ctx, hexAlpha(color, opts.fillAlpha || .22), hexAlpha(color, .0), fillHeight || 200)
      : false;
    return {
      label:                     label,
      data:                      data,
      borderColor:               color,
      borderWidth:               opts.lineWidth || 1.8,
      backgroundColor:           fill,
      fill:                      !!ctx,
      tension:                   opts.tension !== undefined ? opts.tension : 0,
      pointRadius:               0,
      pointHoverRadius:          5,
      pointHoverBackgroundColor: color,
      pointHoverBorderColor:     '#fff',
      pointHoverBorderWidth:     2,
    };
  };

  /* ── Dataset barras neon ── */
  window.ntBarDataset = function (label, data, color, ctx, fillHeight) {
    var bg = ctx ? ntBarGrad(ctx, color, .85, .2, fillHeight) : color;
    return {
      label:               label,
      data:                data,
      backgroundColor:     bg,
      borderColor:         color,
      borderWidth:         1,
      borderRadius:        9,
      borderSkipped:       false,
      hoverBackgroundColor:color,
      hoverBorderWidth:    0,
    };
  };

  /* ── Escalas padrão ── */
  window.ntScales = function (yCallback) {
    var cb = yCallback || function(v) {
      return 'R$' + (v >= 1000 ? (v/1000).toFixed(0)+'k' : v.toLocaleString('pt-BR'));
    };
    return {
      x: { grid:{ color:N.gridLine, lineWidth:1 }, ticks:{ color:N.tick }, border:{ display:false } },
      y: { grid:{ color:N.gridLine, lineWidth:1 }, ticks:{ color:N.tick, callback:cb }, border:{ display:false } },
    };
  };

  /* ── Opções base ── */
  window.ntOptions = function (extra) {
    var base = {
      responsive:          true,
      maintainAspectRatio: false,
      interaction:         { mode:'index', intersect:false },
      plugins:             { legend:{ display:false } },
      scales:              ntScales(),
      animation:           { duration:1100, easing:'easeOutQuart' },
    };
    if (!extra) return base;
    // merge profundo apenas em plugins e scales
    if (extra.scales)  base.scales  = Object.assign(base.scales,  extra.scales);
    if (extra.plugins) base.plugins = Object.assign(base.plugins, extra.plugins);
    return Object.assign(base, extra);
  };

  /* Expor paleta */
  window.NT_COLORS = N;

})();
