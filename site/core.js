'use strict';
/* Utilitários: DOM, formatação, API, formulários e janelas. */

// ------------------------------------------------------------------ DOM
function append(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k == null || k === false) continue;
    el.append(k.nodeType ? k : document.createTextNode(String(k)));
  }
}
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else if (k === 'html') el.innerHTML = v; // somente texto confiável (ícones/SVG)
      else if (v === true) el.setAttribute(k, '');
      else el.setAttribute(k, v);
    }
  }
  append(el, kids);
  return el;
}
const $ = (sel, root = document) => root.querySelector(sel);

const ICONS = {
  home: '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
  repeat: '<polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>',
  trending: '<polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/>',
  sliders: '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>',
  trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
  x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  left: '<polyline points="15 18 9 12 15 6"/>',
  right: '<polyline points="9 18 15 12 9 6"/>',
  down: '<line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/>',
  up: '<line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
  clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  check: '<polyline points="20 6 9 17 4 12"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  search: '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
  card: '<rect x="1" y="4" width="22" height="16" rx="2"/><line x1="1" y1="10" x2="23" y2="10"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
  chart: '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
  printer: '<polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/>',
  power: '<path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.8 0"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  key: '<circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6"/><path d="m15.5 7.5 3 3L22 7l-3-3"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
};
function icon(name) {
  return h('span', { style: { display: 'inline-flex' }, html: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="18" height="18">${ICONS[name]}</svg>` }).firstChild;
}
const iconBtn = (name, title, onclick, cls = '') => h('button', { class: `icon-btn ${cls}`, type: 'button', title, 'aria-label': title, onclick }, icon(name));
const btn = (label, cls, onclick, iconName) => h('button', { class: `btn ${cls || ''}`, type: 'button', onclick }, iconName ? icon(iconName) : null, label);
const chip = (text, tone = '') => h('span', { class: `chip ${tone}` }, text);

// ------------------------------------------------------------------ formatação
const BRL = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
const fmt = n => BRL.format(n ?? 0);
const signed = n => (n > 0 ? '+ ' : n < 0 ? '− ' : '') + BRL.format(Math.abs(n ?? 0));
const num = (n, d = 2) => (n ?? 0).toLocaleString('pt-BR', { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = n => (n == null ? '—' : num(n) + '%');
const qty = n => (n ?? 0).toLocaleString('pt-BR', { maximumFractionDigits: 4 });
const price4 = n => (n == null ? '—' : n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL', minimumFractionDigits: 2, maximumFractionDigits: 4 }));
const toneOf = n => (n > 0.004 ? 'pos' : n < -0.004 ? 'neg' : '');

const MS = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
const pad = n => String(n).padStart(2, '0');
const todayISO = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
const ymNow = () => todayISO().slice(0, 7);
function ymAdd(ym, n) {
  const idx = parseInt(ym.slice(0, 4), 10) * 12 + parseInt(ym.slice(5, 7), 10) - 1 + n;
  return `${Math.floor(idx / 12)}-${pad((idx % 12) + 1)}`;
}
const ymLabel = ym => new Date(+ym.slice(0, 4), +ym.slice(5, 7) - 1, 1).toLocaleDateString('pt-BR', { month: 'long', year: 'numeric' });
const ymShort = ym => `${MS[+ym.slice(5, 7) - 1]}/${ym.slice(2, 4)}`;
const ymBR = ym => `${ym.slice(5, 7)}/${ym.slice(0, 4)}`;
const dateBR = iso => `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}`;
function dayLabel(iso) {
  const s = new Date(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)).toLocaleDateString('pt-BR', { weekday: 'long', day: 'numeric', month: 'long' });
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function parseBR(raw) {
  let s = String(raw).replace(/R\$/g, '').replace(/\s/g, '');
  if (!s) return NaN;
  if (s.includes(',')) s = s.replace(/\./g, '').replace(',', '.');
  else if (/^[1-9]\d{0,2}(\.\d{3})+$/.test(s)) s = s.replace(/\./g, '');
  return /^-?\d*\.?\d+$/.test(s) ? parseFloat(s) : NaN;
}
const inputNum = n => (n == null ? '' : n.toLocaleString('pt-BR', { useGrouping: false, maximumFractionDigits: 4 }));

// ------------------------------------------------------------------ API (o Python roda dentro do aparelho; ver backend.js)
async function api(method, path, body) {
  const res = await Backend.request(method, '/api' + path, body);
  if (res.status < 200 || res.status >= 300) {
    throw new Error((res.body && res.body.error) || `Erro ${res.status}`);
  }
  return res.body;
}

const newRef = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`);

/** Salva um lançamento. O código único (client_ref) impede gravar duas vezes se o botão for tocado em dobro. */
async function sendTransaction(body) {
  return api('POST', '/transactions', { ...body, client_ref: body.client_ref || newRef() });
}

// ------------------------------------------------------------------ arquivos (backup, relatórios)
const LAST_EXPORT_KEY = 'mc_last_export';
function lastExport() { try { return localStorage.getItem(LAST_EXPORT_KEY) || ''; } catch { return ''; } }
function markExported() { try { localStorage.setItem(LAST_EXPORT_KEY, new Date().toISOString()); } catch { /* sem armazenamento */ } }

/** Entrega um arquivo ao usuário: no celular abre "Compartilhar" (Salvar em Arquivos, iCloud Drive, e-mail…); no computador baixa direto. */
async function saveFile({ name, ctype, bytes }) {
  const file = new File([bytes], name, { type: ctype || 'application/octet-stream' });
  const touch = window.matchMedia && matchMedia('(pointer: coarse)').matches;
  if (touch && navigator.canShare && navigator.canShare({ files: [file] })) {
    try { await navigator.share({ files: [file], title: name }); return true; }
    catch (e) { if (e && e.name === 'AbortError') return false; /* o iPhone recusou compartilhar agora: baixa pelo link */ }
  }
  const url = URL.createObjectURL(file);
  const a = h('a', { href: url, download: name, style: { display: 'none' } });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
  return true;
}

/** Pede um arquivo à API (ex.: /backup, /report/.../xlsx) e entrega ao usuário. Devolve true se foi salvo/compartilhado. */
async function downloadFrom(path) {
  const res = await Backend.request('GET', '/api' + path);
  if (res.status !== 200 || !res.file) throw new Error((res.body && res.body.error) || 'Não foi possível gerar o arquivo.');
  return saveFile(res.file);
}

/** Baixa o backup completo e marca a data (usada no aviso "faça uma cópia"). */
async function exportBackup() {
  try {
    if (await downloadFrom('/backup')) { markExported(); toast('Cópia dos dados pronta. Guarde-a fora do celular.'); return true; }
  } catch (e) { toast(e.message, 'error'); }
  return false;
}

function toast(msg, type = '') {
  const t = h('div', { class: `toast ${type}`, role: 'status' }, msg);
  $('#toasts').append(t);
  setTimeout(() => t.remove(), type === 'error' ? 5000 : 2800);
}

// ------------------------------------------------------------------ janelas
function modal({ title, wide, build, onClose, backdropClose = false }) {
  const overlay = h('div', { class: 'overlay' });
  function close() {
    if (!overlay.isConnected) return;
    overlay.remove();
    document.removeEventListener('keydown', onKey);
    if (onClose) onClose();
  }
  function onKey(e) {
    const all = document.querySelectorAll('.overlay');
    if (e.key === 'Escape' && all[all.length - 1] === overlay) close(); // só a janela do topo
  }
  const box = h('div', { class: 'modal' + (wide ? ' wide' : ''), role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    h('div', { class: 'modal-head' }, h('h2', null, title), iconBtn('x', 'Fechar', close)));
  box.append(build(close));
  overlay.append(box);
  // clicar fora NÃO fecha (evita perder o que estava sendo feito); só confirmações simples fecham assim
  if (backdropClose) {
    let pressedOnBackdrop = false;
    overlay.addEventListener('mousedown', e => { pressedOnBackdrop = e.target === overlay && e.clientX < overlay.clientWidth && e.clientY < overlay.clientHeight; });
    overlay.addEventListener('mouseup', e => { if (pressedOnBackdrop && e.target === overlay) close(); pressedOnBackdrop = false; });
  }
  // a roda do mouse fora da janela rola o conteúdo da janela, em vez da página que está por trás
  overlay.addEventListener('wheel', e => {
    if (e.target !== overlay) return;
    e.preventDefault();
    const area = box.querySelector('.modal-body');
    if (area) area.scrollTop += e.deltaY;
  }, { passive: false });
  document.addEventListener('keydown', onKey);
  document.body.append(overlay);
  const first = overlay.querySelector('input[type="text"], input[type="date"], select');
  if (first) first.focus();
  return close;
}

function confirmDialog({ title = 'Confirmar', message, confirmLabel = 'Excluir', danger = true }) {
  return new Promise(resolve => {
    modal({
      title, backdropClose: true,
      onClose: () => resolve(false), // fechar pelo X, ESC ou fundo = cancelar (sem efeito se já respondeu)
      build: close => h('div', null,
        h('div', { class: 'modal-body' }, h('p', null, message)),
        h('div', { class: 'modal-foot' },
          btn('Cancelar', '', close),
          btn(confirmLabel, danger ? 'danger' : 'primary', () => { resolve(true); close(); }))),
    });
  });
}

const DEFAULT_CATEGORIES = ['Moradia', 'Alimentação', 'Mercado', 'Transporte', 'Saúde', 'Educação', 'Lazer', 'Assinaturas', 'Impostos', 'Salário', 'Outros'];
async function categoryList() {
  try {
    const used = await api('GET', '/categories');
    return [...new Set([...used, ...DEFAULT_CATEGORIES])].sort((a, b) => a.localeCompare(b, 'pt-BR'));
  } catch { return DEFAULT_CATEGORIES; }
}

/**
 * Formulário em janela.
 * fields: [{name,label,type:'text'|'money'|'number'|'date'|'month'|'select'|'segmented'|'check',
 *           options,placeholder,hint,half,list,showIf(values),required}]
 */
function openForm({ title, fields, initial = {}, submitLabel = 'Salvar', onSubmit, datalists = {}, intro, onChange, danger = false, onClose }) {
  const controls = {};
  const errorBox = h('div', { class: 'form-error', hidden: true });
  const grid = h('div', { class: 'form-grid' });

  const collect = (strict) => {
    const values = {};
    for (const f of fields) {
      const c = controls[f.name];
      if (c.wrap.hidden) continue;
      let v = c.get();
      if (f.type === 'money' || f.type === 'number') {
        if (v === '') v = null;
        else {
          const parsed = parseBR(v);
          if (Number.isNaN(parsed)) {
            if (strict) throw new Error(`Valor inválido em "${f.label}".`);
            v = null;
          } else v = parsed;
        }
      }
      values[f.name] = v;
    }
    return values;
  };
  const refreshVisibility = () => {
    const values = collect(false);
    for (const f of fields) if (f.showIf) controls[f.name].wrap.hidden = !f.showIf({ ...initial, ...values });
  };
  // permite preencher campos automaticamente (ex.: sugestão ao escolher uma descrição já usada)
  const setValue = (name, value) => { const c = controls[name]; if (c && c.set) c.set(value); };
  const changed = (e) => { if (onChange) onChange(collect(false), setValue, e && e.target && e.target.id); refreshVisibility(); };

  for (const f of fields) {
    const id = `f-${f.name}`;
    const start = initial[f.name] ?? f.value ?? (f.type === 'check' ? false : '');
    let input, get, set;
    if (f.type === 'select') {
      input = h('select', { id }, f.options.map(o => h('option', { value: o.value }, o.label)));
      input.value = start;
      get = () => input.value;
      set = val => { input.value = val; };
    } else if (f.type === 'segmented') {
      let current = start || f.options[0].value;
      input = h('div', { class: 'segmented', role: 'group' });
      const paint = () => [...input.children].forEach((b, i) => { b.className = (f.options[i].value === current ? 'on ' : '') + (f.options[i].cls || ''); });
      f.options.forEach(o => input.append(h('button', { type: 'button', onclick: () => { current = o.value; paint(); changed(); } }, o.label)));
      paint();
      get = () => current;
      set = val => { current = val; paint(); };
    } else if (f.type === 'check') {
      input = h('input', { type: 'checkbox', id });
      input.checked = !!start;
      get = () => input.checked;
      set = val => { input.checked = !!val; };
    } else {
      const kind = f.type === 'date' ? 'date' : f.type === 'month' ? 'month' : 'text';
      input = h('input', { type: kind, id, autocomplete: 'off', list: f.list ? `dl-${f.list}` : null,
        inputmode: f.type === 'money' || f.type === 'number' ? 'decimal' : null, placeholder: f.placeholder || '' });
      input.value = (f.type === 'money' || f.type === 'number') && typeof start === 'number' ? inputNum(start) : start;
      get = () => input.value.trim();
      set = val => { input.value = (f.type === 'money' || f.type === 'number') && typeof val === 'number' ? inputNum(val) : (val ?? ''); };
    }
    input.addEventListener('input', changed);
    input.addEventListener('change', changed);
    const wrap = f.type === 'check'
      ? h('div', { class: 'field check' }, input, h('label', { for: id }, f.label, f.hint ? h('span', { class: 'hint' }, f.hint) : null))
      : h('div', { class: 'field' + (f.half ? ' half' : '') },
        h('label', { for: id }, f.label), input, f.hint ? h('div', { class: 'hint' }, f.hint) : null);
    controls[f.name] = { wrap, get, set };
    grid.append(wrap);
  }
  Object.entries(datalists).forEach(([name, items]) => grid.append(h('datalist', { id: `dl-${name}` }, items.map(i => h('option', { value: i })))));

  modal({
    title,
    onClose,
    build: close => {
      const submit = h('button', { class: danger ? 'btn danger' : 'btn primary', type: 'submit' }, submitLabel);
      const form = h('form', { novalidate: true, onsubmit: async e => {
        e.preventDefault();
        errorBox.hidden = true;
        submit.disabled = true;
        try {
          await onSubmit(collect(true));
          close();
        } catch (err) {
          errorBox.textContent = err.message;
          errorBox.hidden = false;
          submit.disabled = false;
        }
      } },
      h('div', { class: 'modal-body' }, intro ? h('p', { class: 'muted', style: { marginBottom: '12px' } }, intro) : null, grid, errorBox),
      h('div', { class: 'modal-foot' }, btn('Cancelar', '', close), submit));
      refreshVisibility();
      return form;
    },
  });
}
