'use strict';
/* Gráficos em SVG puro (sem bibliotecas, funciona offline). */
const Charts = (() => {
  const PALETTE = ['#6b7280', '#ff3b2f', '#f59e0b', '#0ea5e9', '#22c55e', '#8b5cf6', '#14b8a6', '#ec4899', '#84cc16', '#f97316'];
  const BRL = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });

  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const tipAttr = s => esc(s).replace(/\n/g, '&#10;');
  const money = n => BRL.format(n || 0);
  const short = n => {
    const a = Math.abs(n), s = n < 0 ? '-' : '';
    if (a >= 1e6) return s + (a / 1e6).toLocaleString('pt-BR', { maximumFractionDigits: 1 }) + ' mi';
    if (a >= 1e3) return s + (a / 1e3).toLocaleString('pt-BR', { maximumFractionDigits: 1 }) + ' mil';
    return s + a.toLocaleString('pt-BR', { maximumFractionDigits: 0 });
  };

  function niceScale(min, max, count = 4) {
    if (min === max) max = min + 1;
    const raw = (max - min) / count;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
    const lo = Math.floor(min / step + 1e-9) * step, hi = Math.ceil(max / step - 1e-9) * step;
    const ticks = [];
    for (let v = lo; v <= hi + step / 2; v += step) ticks.push(+v.toFixed(10));
    return { lo, hi, ticks };
  }

  function whenReady(el, fn, tries = 30) {
    if (el.isConnected && el.clientWidth > 0) fn();
    else if (tries > 0) requestAnimationFrame(() => whenReady(el, fn, tries - 1));
  }

  function register(el, draw) {
    el.dataset.chart = '1';
    el._redraw = draw;
    whenReady(el, draw);
  }

  function legendInline(series) {
    return `<div class="legend-inline">${series.map(s => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join('')}</div>`;
  }

  function frame(W, H, m, labels, scale, opts) {
    const iw = W - m.l - m.r, ih = H - m.t - m.b, n = labels.length;
    const y = v => m.t + ih * (1 - (v - scale.lo) / (scale.hi - scale.lo));
    const band = iw / n;
    const cx = i => m.l + band * (i + 0.5);
    let out = '';
    scale.ticks.forEach(t => {
      out += `<line class="${t === 0 ? 'zero-line' : 'grid-line'}" x1="${m.l}" x2="${W - m.r}" y1="${y(t)}" y2="${y(t)}"/>`;
      out += `<text class="ax-text" x="${m.l - 8}" y="${y(t) + 4}" text-anchor="end">${(opts.axis || short)(t)}</text>`;
    });
    const step = Math.ceil(n / (opts.maxLabels || 12));
    labels.forEach((l, i) => {
      if (i % step === 0) out += `<text class="ax-text" x="${cx(i)}" y="${H - 8}" text-anchor="middle">${esc(l)}</text>`;
    });
    return { out, y, band, cx, iw, ih };
  }

  /* ---------- barras (empilhadas; valores negativos descem do zero) ---------- */
  function bar(el, o) {
    const draw = () => {
      const W = Math.max(el.clientWidth, 260), H = o.height || 240;
      const m = { l: 48, r: 6, t: 8, b: 26 };
      const n = o.labels.length;
      const stacks = o.labels.map((_, i) => {
        let pos = 0, neg = 0;
        const segs = [];
        o.series.forEach(s => {
          const v = s.values[i];
          if (v == null || v === 0) return;
          const color = s.colorFn ? s.colorFn(v) : s.color;
          if (v > 0) { segs.push({ y0: pos, y1: pos + v, color, s, v }); pos += v; }
          else { segs.push({ y0: neg, y1: neg + v, color, s, v }); neg += v; }
        });
        return { segs, pos, neg };
      });
      const scale = niceScale(Math.min(0, ...stacks.map(s => s.neg)), Math.max(0, ...stacks.map(s => s.pos)));
      const f = frame(W, H, m, o.labels, scale, o);
      let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img">` + f.out;
      if (o.highlight != null && o.highlight >= 0 && o.highlight < n) {
        svg = svg.replace('<line', `<rect x="${m.l + f.band * o.highlight}" y="${m.t}" width="${f.band}" height="${f.ih}" rx="8" style="fill:var(--soft)"/><line`);
      }
      const bw = Math.min(44, f.band * 0.62);
      stacks.forEach((st, i) => {
        const x = f.cx(i) - bw / 2;
        const muted = o.muted && o.muted(i);
        st.segs.forEach(sg => {
          const top = f.y(Math.max(sg.y0, sg.y1)), h = Math.max(Math.abs(f.y(sg.y0) - f.y(sg.y1)), 1.5);
          svg += `<rect x="${x}" y="${top}" width="${bw}" height="${h}" rx="3" fill="${sg.color}" opacity="${muted ? 0.4 : 1}"/>`;
        });
        let tip = o.tip ? o.tip(i) : `${o.labels[i]}\n` + st.segs.map(sg => `${sg.s.name}: ${money(sg.v)}`).join('\n');
        if (!o.tip && o.series.length > 1) tip += `\nTotal: ${money(st.pos + st.neg)}`;
        svg += `<rect class="hit" data-tip="${tipAttr(tip)}" x="${m.l + f.band * i}" y="${m.t}" width="${f.band}" height="${f.ih}" fill="transparent"/>`;
      });
      svg += '</svg>';
      el.innerHTML = (o.series.length > 1 && o.legend !== false ? legendInline(o.series) : '') + svg;
    };
    register(el, draw);
  }

  /* ---------- linhas (valores nulos interrompem a linha) ---------- */
  function line(el, o) {
    const draw = () => {
      const W = Math.max(el.clientWidth, 260), H = o.height || 240;
      const m = { l: 48, r: 10, t: 10, b: 26 };
      const n = o.labels.length;
      const all = o.series.flatMap(s => s.values).filter(v => v != null);
      const scale = niceScale(Math.min(0, ...all), Math.max(0, ...all));
      const f = frame(W, H, m, o.labels, scale, o);
      let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img">` + f.out;
      const base = f.y(Math.max(scale.lo, Math.min(0, scale.hi)));
      o.series.forEach(s => {
        let seg = [];
        const flush = () => {
          if (seg.length > 1) {
            const d = seg.map((p, k) => `${k ? 'L' : 'M'}${p[0]} ${p[1]}`).join(' ');
            if (s.area) svg += `<path d="${d} L${seg[seg.length - 1][0]} ${base} L${seg[0][0]} ${base} Z" fill="${s.color}" opacity="0.13"/>`;
            svg += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2.6" stroke-linejoin="round" stroke-linecap="round"${s.dash ? ' stroke-dasharray="6 6"' : ''}/>`;
          }
          seg = [];
        };
        s.values.forEach((v, i) => { if (v == null) flush(); else seg.push([f.cx(i), f.y(v)]); });
        flush();
        if (n <= 14) s.values.forEach((v, i) => {
          if (v != null) svg += `<circle cx="${f.cx(i)}" cy="${f.y(v)}" r="3.6" fill="${s.color}" stroke="var(--card)" stroke-width="1.5"/>`;
        });
      });
      for (let i = 0; i < n; i++) {
        const tip = o.tip ? o.tip(i) : `${o.labels[i]}\n` + o.series.filter(s => s.values[i] != null).map(s => `${s.name}: ${money(s.values[i])}`).join('\n');
        svg += `<rect class="hit" data-tip="${tipAttr(tip)}" x="${m.l + f.band * i}" y="${m.t}" width="${f.band}" height="${f.ih}" fill="transparent"/>`;
      }
      svg += '</svg>';
      el.innerHTML = (o.series.length > 1 && o.legend !== false ? legendInline(o.series.map(s => ({ ...s }))) : '') + svg;
    };
    register(el, draw);
  }

  /* ---------- rosca com legenda ---------- */
  function donut(el, o) {
    const draw = () => {
      const items = (o.items || []).filter(i => i.value > 0).map((it, k) => ({ ...it, color: it.color || PALETTE[k % PALETTE.length] }));
      const total = items.reduce((a, i) => a + i.value, 0);
      const size = o.size || 190, sw = 26, r = (size - sw) / 2, c = 2 * Math.PI * r;
      let svg = `<svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" style="margin:0 auto" role="img">`;
      svg += `<circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--soft)" stroke-width="${sw}"/>`;
      let offset = 0;
      items.forEach(it => {
        const len = it.value / total * c, gap = items.length > 1 ? 2.5 : 0;
        const pct = (it.value / total * 100).toLocaleString('pt-BR', { maximumFractionDigits: 1 });
        svg += `<circle class="hit" data-tip="${tipAttr(`${it.label}\n${money(it.value)} (${pct}%)`)}" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${it.color}" stroke-width="${sw}" stroke-dasharray="${Math.max(len - gap, 0.5)} ${c}" stroke-dashoffset="${-offset}" transform="rotate(-90 ${size / 2} ${size / 2})"/>`;
        offset += len;
      });
      if (o.centerTop) svg += `<text class="ax-text" x="${size / 2}" y="${size / 2 - 6}" text-anchor="middle">${esc(o.centerTop)}</text>`;
      svg += `<text class="ax-center" x="${size / 2}" y="${size / 2 + 14}" text-anchor="middle" font-size="${o.centerSize || 16}">${esc(total ? (o.centerValue || money(total)) : 'Sem dados')}</text></svg>`;
      const legend = items.map(it => `<div class="legend-item"><i style="background:${it.color}"></i><span>${esc(it.label)}</span><b>${money(it.value)}</b><em>${(it.value / total * 100).toLocaleString('pt-BR', { maximumFractionDigits: 0 })}%</em></div>`).join('');
      el.innerHTML = svg + `<div class="legend">${legend}</div>`;
    };
    register(el, draw);
  }

  /* ---------- tooltip global e redimensionamento ---------- */
  document.addEventListener('mousemove', e => {
    const tp = document.getElementById('tooltip');
    if (!tp) return;
    const t = e.target.closest && e.target.closest('[data-tip]');
    if (!t) { tp.classList.remove('show'); return; }
    tp.textContent = t.getAttribute('data-tip');
    tp.classList.add('show');
    const r = tp.getBoundingClientRect();
    let x = e.clientX + 14, y = e.clientY + 14;
    if (x + r.width > window.innerWidth - 8) x = e.clientX - r.width - 14;
    if (y + r.height > window.innerHeight - 8) y = e.clientY - r.height - 14;
    tp.style.left = x + 'px';
    tp.style.top = y + 'px';
  });
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => document.querySelectorAll('[data-chart]').forEach(el => el._redraw && el._redraw()), 120);
  });

  return { bar, line, donut, money, short, esc, PALETTE };
})();
