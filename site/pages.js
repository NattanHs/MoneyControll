'use strict';
/* Páginas do aplicativo e roteamento. */

const state = { ym: ymNow(), investTab: 'resumo', bankSel: new Set(), account: '' };

// contas (corrente, poupança, dinheiro…): carregadas a cada página e usadas pelos formulários
let accountsInfo = { items: [], default_id: null, total: 0 };
async function loadAccounts() { try { accountsInfo = await api('GET', '/accounts'); } catch (e) { if (e.message === 'Entre com o PIN.') throw e; } return accountsInfo; }
const multiAccount = () => accountsInfo.items.length > 1;
const accountName = id => { const a = accountsInfo.items.find(x => x.id === id); return a ? a.name : ''; };
const accountInitial = existing => String((existing && existing.account_id) || state.account || accountsInfo.default_id || '');
const accountField = (label = 'Conta', extra = {}) => (multiAccount()
  ? [{ name: 'account_id', label, type: 'select', options: accountsInfo.items.map(a => ({ value: String(a.id), label: a.name })), ...extra }] : []);
const accountVal = v => (v.account_id ? Number(v.account_id) : null);
let investData = null; // último painel de investimentos carregado (usado pelos formulários)

const NAV = [
  { id: 'visao', label: 'Visão geral', short: 'Início', icon: 'home' },
  { id: 'mes', label: 'Mês a mês', short: 'Mês', icon: 'calendar' },
  { id: 'conc', label: 'Conciliação', short: 'Extrato', icon: 'link' },
  { id: 'fixas', label: 'Fixas e parcelas', short: 'Fixas', icon: 'repeat' },
  { id: 'invest', label: 'Investimentos', short: 'Invest.', icon: 'trending' },
  { id: 'relat', label: 'Relatórios', short: 'Relat.', icon: 'chart' },
  { id: 'config', label: 'Configurações', short: 'Ajustes', icon: 'sliders' },
];

// ------------------------------------------------------------------ componentes
const pageHead = (title, sub, ...actions) => h('div', { class: 'page-head' },
  h('div', null, h('h1', null, title), sub ? h('div', { class: 'sub' }, sub) : null),
  h('div', { class: 'actions' }, actions));

function kpi(label, value, { tone = '', sub = '', dot = '', highlight = '' } = {}) {
  return h('div', { class: `card kpi ${highlight}` },
    h('div', { class: 'label' }, dot ? h('span', { class: 'dot', style: { background: dot } }) : null, label),
    h('div', { class: `value num ${tone}` }, value),
    sub ? h('div', { class: 'sub' }, sub) : null);
}
const metric = (label, value, tone = '', big = false) =>
  h('div', { class: 'metric' }, h('small', null, label), h('b', { class: `num ${tone} ${big ? 'big' : ''}` }, value));
const emptyState = (title, text, ...actions) => h('div', { class: 'empty' }, h('b', null, title), text, h('div', null, actions));
const chartBox = () => h('div', { class: 'chart' });

async function remove(path, message, after = refresh) {
  if (!(await confirmDialog({ message }))) return;
  try { await api('DELETE', path); toast('Excluído'); after(); } catch (e) { toast(e.message, 'error'); }
}

// ------------------------------------------------------------------ orçamento (barras)
function budgetBars(list) {
  return list.map(b => h('div', { class: 'budget-row' },
    h('div', { class: 'top' }, h('span', null, b.category, b.state === 'over' ? ' ' : '', b.state === 'over' ? chip('acima do limite', 'red') : b.state === 'warn' ? chip('perto do limite', 'amber') : null),
      h('span', null, `${fmt(b.spent)} de ${fmt(b.limit)} · ${num(b.pct, 0)}%`)),
    h('div', { class: `progress ${b.state === 'ok' ? '' : b.state}` }, h('span', { style: { width: Math.min(100, b.pct) + '%' } }))));
}

// ------------------------------------------------------------------ visão geral
async function pageOverview() {
  const o = await api('GET', '/overview');
  const cm = o.current_month;
  const root = h('div');
  root.append(pageHead('Visão geral', `Hoje, ${dateBR(o.today)}`,
    btn('Nova entrada', 'success', () => (isPhone() ? quickForm('entrada') : transactionForm('entrada', null, { today: true, stay: true })), 'plus'),
    btn('Nova saída', 'danger-soft', () => (isPhone() ? quickForm('saida') : transactionForm('saida', null, { today: true, stay: true })), 'plus')));

  if (!o.has_data) {
    root.append(h('div', { class: 'card', style: { marginBottom: '18px' } },
      h('div', { class: 'section-title' }, 'Bem-vindo! Comece por aqui'),
      h('div', { class: 'grid-eq', style: { gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))' } },
        h('div', { class: 'note' }, h('b', null, '1. Saldo inicial'), h('br'), 'Se você já tem dinheiro em conta, informe em Configurações.', h('br'), h('a', { href: '#/config' }, 'Abrir configurações')),
        h('div', { class: 'note' }, h('b', null, '2. Fixas e parcelas'), h('br'), 'Cadastre salário, aluguel, assinaturas e compras parceladas uma única vez.', h('br'), h('a', { href: '#/fixas' }, 'Cadastrar')),
        h('div', { class: 'note' }, h('b', null, '3. Lançamentos'), h('br'), 'Registre entradas e saídas do dia a dia no mês.', h('br'), h('a', { href: '#/mes' }, 'Ir para o mês')))));
  }

  try {
    const hide = Number(localStorage.getItem('mc_ext_hide') || 0) > Date.now();
    const days = exportDays();
    if (o.has_data && !hide && (days === null || days >= 7)) {
      root.append(h('div', { class: 'card alert-card', style: { marginBottom: '18px' } },
        h('div', { class: 'section-title', style: { marginBottom: '4px' } }, 'Seus dados só estão neste aparelho'),
        h('p', { class: 'muted', style: { marginBottom: '10px' } }, days === null
          ? 'Se o celular for perdido, quebrar ou o navegador limpar os dados do app, tudo se perde. Salve uma cópia no iCloud Drive (ou mande para o seu e-mail): leva um minuto.'
          : `A última cópia salva fora do app foi há ${days} dia(s).`),
        h('div', { class: 'actions' }, btn('Salvar uma cópia agora', 'primary small', async () => { if (await exportBackup()) refresh(); }, 'shield'),
          btn('Lembrar depois', 'small', () => { try { localStorage.setItem('mc_ext_hide', String(Date.now() + 3 * 86400000)); } catch { /* ignorar */ } refresh(); }))));
    }
  } catch { /* sem armazenamento */ }

  const part = (label, value) => h('div', { class: 'part' }, h('small', null, label), h('b', { class: 'num' }, value));
  root.append(h('div', { class: 'card hero' },
    h('div', null, h('div', { class: 'label' }, 'Patrimônio total'), h('div', { class: 'value num' }, fmt(o.patrimonio_total))),
    h('div', { class: 'parts' }, part('Saldo em conta', fmt(o.conta)), part('Renda fixa', fmt(o.renda_fixa)), part('Renda variável', fmt(o.renda_variavel)),
      cm.a_receber > 0 ? part('A receber este mês', fmt(cm.a_receber)) : null,
      cm.a_pagar > 0 ? part('A pagar este mês', fmt(cm.a_pagar)) : null)));

  if (o.accounts && o.accounts.length > 1) {
    root.append(h('div', { class: 'card', style: { marginBottom: '18px' } },
      h('div', { class: 'section-title' }, 'Suas contas', btn('Gerenciar', 'ghost small', () => { location.hash = '#/config'; })),
      h('div', { class: 'kpis', style: { marginBottom: 0 } }, o.accounts.map(a => kpi(a.name, fmt(a.balance), {
        tone: toneOf(a.balance), sub: a.kind_label + (Math.abs(a.forecast - a.balance) > 0.004 ? ` · previsto ${fmt(a.forecast)}` : '') })))));
  }
  root.append(h('div', { class: 'kpis cols3' },
    kpi('Entradas do mês', fmt(cm.entradas), { tone: 'pos', dot: '#22c55e', sub: 'já recebidas' }),
    kpi('Proventos a receber', fmt(cm.a_receber), { tone: 'amber', dot: '#f59e0b', sub: 'entradas com data futura neste mês' }),
    kpi('Saídas pagas', fmt(cm.saidas), { tone: 'neg', dot: '#ef4444' }),
    kpi('A pagar no mês', fmt(cm.a_pagar), { tone: 'amber', dot: '#f59e0b', sub: 'ainda não pagas' }),
    kpi('Aportes no mês', fmt(cm.aportes), { tone: 'inv', dot: '#8b5cf6' }),
    kpi('Resultado do mês', signed(cm.resultado), { tone: toneOf(cm.resultado), sub: `Saldo anterior ${fmt(cm.saldo_anterior)}` })));

  if (o.budget_alerts && o.budget_alerts.length) {
    root.append(h('div', { class: 'card alert-card', style: { marginBottom: '18px' } },
      h('div', { class: 'section-title' }, 'Orçamento do mês: atenção', h('small', null, 'categorias perto ou acima do limite')),
      budgetBars(o.budget_alerts)));
  }

  const evo = chartBox(), alloc = chartBox();
  root.append(h('div', { class: 'grid-2' },
    h('div', { class: 'card' }, h('div', { class: 'section-title' }, 'Evolução do patrimônio', h('small', null, 'últimos 12 meses')), evo,
      h('div', { class: 'muted', style: { fontSize: '12px', marginTop: '8px' } }, 'Renda variável a preço de custo nos meses anteriores; o mês atual usa a cotação informada.')),
    h('div', { class: 'card' }, h('div', { class: 'section-title' }, 'Onde está seu dinheiro'), alloc)));

  Charts.bar(evo, {
    labels: o.evolution.map(e => ymShort(e.ym)), height: 250, highlight: o.evolution.length - 1,
    series: [
      { name: 'Conta', color: '#6b7280', values: o.evolution.map(e => e.conta) },
      { name: 'Renda fixa', color: '#0ea5e9', values: o.evolution.map(e => e.rf) },
      { name: 'Renda variável', color: '#8b5cf6', values: o.evolution.map(e => e.rv) },
    ],
  });
  Charts.donut(alloc, { items: o.allocation, centerTop: 'Total', centerValue: fmt(o.allocation.reduce((a, i) => a + i.value, 0)), centerSize: 15 });

  if (o.upcoming.length) {
    root.append(h('div', { class: 'card', style: { marginTop: '18px' } },
      h('div', { class: 'section-title' }, 'Saldo previsto nos próximos meses', h('small', null, 'inclui entradas a receber, contas a pagar, fixas, parcelas e faturas')),
      h('div', { class: 'kpis', style: { marginBottom: 0 } }, o.upcoming.map(m => kpi(ymLabel(m.ym), fmt(m.saldo_previsto), {
        tone: toneOf(m.saldo_previsto), sub: `Resultado previsto ${signed(m.resultado_previsto)}` })))));
  }
  return root;
}

// ------------------------------------------------------------------ mês a mês
const norm = s => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
const maxYm = (a, b) => (a > b ? a : b);

// mês em que vence a fatura que recebe uma compra feita em `iso` (mesma regra do servidor)
function cardDueYm(card, iso) {
  const closing = +iso.slice(8, 10) <= card.closing_day ? iso.slice(0, 7) : ymAdd(iso.slice(0, 7), 1);
  return card.due_day > card.closing_day ? closing : ymAdd(closing, 1);
}


// ------------------------------------------------------------------ lançamento rápido (celular)
const isPhone = () => window.matchMedia('(max-width: 640px)').matches;

async function quickForm(kind0 = 'saida') {
  const [sugg, cats] = await Promise.all([api('GET', '/suggestions').catch(() => []), categoryList()]);
  let kind = kind0, category = '';
  const count = {};
  sugg.forEach(s => { if (s.category) { const k = `${s.kind}|${s.category}`; count[k] = (count[k] || 0) + 1; } });
  const topFor = k => {
    const used = Object.entries(count).filter(([key]) => key.startsWith(k + '|')).sort((a, b) => b[1] - a[1]).map(([key]) => key.slice(k.length + 1));
    return [...new Set([...used, ...cats])].slice(0, 8);
  };
  const byDesc = new Map(sugg.map(s => [`${s.kind}|${norm(s.description)}`, s]));
  modal({
    title: 'Lançamento rápido',
    build: close => {
      const amount = h('input', { type: 'text', id: 'q-amount', class: 'quick-amount', inputmode: 'decimal', placeholder: '0,00', autocomplete: 'off', 'aria-label': 'Valor em reais' });
      const desc = h('input', { type: 'text', id: 'q-desc', list: 'q-dl', placeholder: 'Descrição (opcional)', autocomplete: 'off', 'aria-label': 'Descrição' });
      const dl = h('datalist', { id: 'q-dl' });
      const date = h('input', { type: 'date', id: 'q-date', value: todayISO(), 'aria-label': 'Data' });
      const acct = multiAccount() ? h('select', { id: 'q-acct', 'aria-label': 'Conta' }, accountsInfo.items.map(a => h('option', { value: a.id }, a.name))) : null;
      if (acct) acct.value = accountInitial();
      const error = h('div', { class: 'form-error', hidden: true });
      const seg = h('div', { class: 'segmented', role: 'group' });
      const chips = h('div', { class: 'quick-chips', role: 'group', 'aria-label': 'Categoria' });
      const paint = () => {
        [...seg.children].forEach((b, i) => { b.className = (['saida', 'entrada'][i] === kind ? 'on ' : '') + (i === 0 ? 'out' : 'in'); });
        dl.replaceChildren(...[...new Set(sugg.filter(s => s.kind === kind).map(s => s.description))].slice(0, 60).map(d => h('option', { value: d })));
        chips.replaceChildren(...topFor(kind).map(c => h('button', { type: 'button', class: 'qchip' + (c === category ? ' on' : ''), onclick: () => { category = category === c ? '' : c; paint(); } }, c)));
      };
      [['saida', 'Saída'], ['entrada', 'Entrada']].forEach(([v, l]) => seg.append(h('button', { type: 'button', onclick: () => { kind = v; category = ''; paint(); } }, l)));
      desc.addEventListener('input', () => {
        const hit = byDesc.get(`${kind}|${norm(desc.value)}`);
        if (!hit) return;
        if (!amount.value.trim()) amount.value = inputNum(hit.amount);
        if (!category && hit.category) { category = hit.category; paint(); }
      });
      const save = async (again) => {
        error.hidden = true;
        const value = parseBR(amount.value);
        if (Number.isNaN(value) || value <= 0) { error.textContent = 'Informe o valor.'; error.hidden = false; amount.focus(); return; }
        if (!date.value) { error.textContent = 'Informe a data.'; error.hidden = false; return; }
        const body = { kind, description: desc.value.trim() || category || (kind === 'saida' ? 'Gasto' : 'Recebimento'), amount: value, date: date.value,
          category, card_id: null, repeat: 0, account_id: acct ? Number(acct.value) : null };
        try {
          await sendTransaction(body);
          toast('Lançamento salvo');
          if (again) { amount.value = ''; desc.value = ''; category = ''; paint(); amount.focus(); refresh(); } else { close(); refresh(); }
        } catch (e) { error.textContent = e.message; error.hidden = false; }
      };
      paint();
      return h('form', { novalidate: true, onsubmit: e => { e.preventDefault(); save(false); } },
        h('div', { class: 'modal-body quick' }, seg, amount, desc, dl, chips,
          h('div', { class: 'quick-row' }, date, acct), error),
        h('div', { class: 'modal-foot' }, btn('Salvar e novo', '', () => save(true)), h('button', { class: 'btn primary', type: 'submit' }, 'Salvar')));
    },
  });
}

async function transactionForm(kind, existing, opts = {}) {
  const [cats, cardList, sugg] = await Promise.all([
    categoryList(),
    api('GET', '/cards').catch(() => []),
    api('GET', '/suggestions').catch(() => []),
  ]);
  const isNew = !existing || opts.duplicate;
  let initial;
  if (existing && !opts.duplicate) initial = { ...existing };
  else if (existing) { // duplicar: mesmo lançamento, no mesmo dia do mês seguinte
    const d = existing.date;
    const next = ymAdd(d.slice(0, 7), 1);
    const last = new Date(+next.slice(0, 4), +next.slice(5, 7), 0).getDate();
    initial = { ...existing, date: `${next}-${pad(Math.min(+d.slice(8, 10), last))}` };
  } else {
    initial = { kind, date: opts.today || todayISO().startsWith(state.ym) ? todayISO() : `${state.ym}-01`, card_id: opts.card_id || '' };
  }
  if (initial.card_id == null) initial.card_id = '';
  initial.account_id = accountInitial(initial);
  const byDesc = new Map(sugg.map(s => [`${s.kind}|${norm(s.description)}`, s]));
  openForm({
    title: existing && !opts.duplicate ? 'Editar lançamento' : opts.duplicate ? 'Duplicar lançamento' : (kind === 'entrada' ? 'Nova entrada' : 'Nova saída'),
    initial,
    datalists: { category: cats, desc: [...new Set(sugg.map(s => s.description))] },
    onChange: (v, set, id) => {
      if (!isNew || id !== 'f-description') return;
      const hit = byDesc.get(`${v.kind}|${norm(v.description)}`);
      if (!hit) return;
      if (v.amount == null) set('amount', hit.amount);
      if (!v.category) set('category', hit.category);
      if (v.kind === 'saida' && !v.card_id && hit.card_id) set('card_id', String(hit.card_id));
    },
    fields: [
      { name: 'kind', label: 'Tipo', type: 'segmented', options: [{ value: 'entrada', label: 'Entrada', cls: 'in' }, { value: 'saida', label: 'Saída', cls: 'out' }] },
      { name: 'description', label: 'Descrição', type: 'text', list: 'desc', placeholder: 'Ex.: Salário, mercado, conta de luz', hint: isNew ? 'Ao escolher uma descrição já usada, categoria e valor são preenchidos.' : undefined },
      { name: 'amount', label: 'Valor (R$)', type: 'money', half: true, placeholder: '0,00' },
      { name: 'date', label: 'Data', type: 'date', half: true, hint: 'Saída com data futura fica em "A pagar" até o dia (ou até você marcar como paga).' },
      { name: 'category', label: 'Categoria (opcional)', type: 'text', list: 'category', placeholder: 'Ex.: Moradia' },
      { name: 'card_id', label: 'Pago no cartão de crédito?', type: 'select', options: [{ value: '', label: 'Não, sai direto da conta' }, ...cardList.map(c => ({ value: String(c.id), label: c.name }))],
        showIf: v => v.kind === 'saida' && cardList.length > 0 },
      ...accountField('Conta', { showIf: v => !(v.kind === 'saida' && v.card_id), hint: 'De qual conta o dinheiro sai ou entra.' }),
      { name: 'repeat', label: 'Repetir nos próximos meses (opcional)', type: 'number', placeholder: '0', hint: 'Cria cópias nos meses seguintes, no mesmo dia. Ex.: 5 = este mês + mais 5. Para algo que não acaba, use Fixas e parcelas.',
        showIf: () => isNew },
    ],
    onSubmit: async v => {
      const body = { ...v, card_id: v.kind === 'saida' && v.card_id ? Number(v.card_id) : null, repeat: v.repeat || 0, account_id: accountVal(v) };
      await api(existing && !opts.duplicate ? 'PUT' : 'POST', existing && !opts.duplicate ? `/transactions/${existing.ref_id}` : '/transactions', body);
      const card = body.card_id ? cardList.find(c => c.id === body.card_id) : null;
      if (!opts.stay) state.ym = card ? cardDueYm(card, v.date) : v.date.slice(0, 7);
      toast(card ? `Lançado na fatura de ${card.name} (vence em ${ymBR(cardDueYm(card, v.date))})`
        : v.kind === 'entrada' && v.date > todayISO() ? 'Salvo em "Proventos a receber" — entra no saldo na data'
          : v.kind === 'saida' && v.date > todayISO() ? 'Salvo em "A pagar" — sai do saldo na data ou quando você marcar como paga' : 'Lançamento salvo');
      (opts.after || refresh)();
    },
  });
}

function transferForm(existing) {
  const opts = accountsInfo.items.map(a => ({ value: String(a.id), label: a.name }));
  const other = state.account ? accountsInfo.items.find(a => String(a.id) !== String(state.account)) : accountsInfo.items[1];
  openForm({
    title: existing ? 'Editar transferência' : 'Transferência entre contas',
    intro: 'Move dinheiro de uma conta para outra. O total do patrimônio não muda: só sai de uma e entra na outra.',
    initial: existing
      ? { from_account: String(existing.from_account), to_account: String(existing.to_account), amount: existing.amount, date: existing.date, description: existing.note || '' }
      : { from_account: String(state.account || accountsInfo.default_id), to_account: String(other ? other.id : ''), date: todayISO().startsWith(state.ym) ? todayISO() : `${state.ym}-01`, description: '' },
    fields: [
      { name: 'from_account', label: 'Sai de', type: 'select', options: opts, half: true },
      { name: 'to_account', label: 'Entra em', type: 'select', options: opts, half: true },
      { name: 'amount', label: 'Valor (R$)', type: 'money', half: true, placeholder: '0,00' },
      { name: 'date', label: 'Data', type: 'date', half: true },
      { name: 'description', label: 'Observação (opcional)', type: 'text', placeholder: 'Ex.: Reserva do mês' },
    ],
    onSubmit: async v => {
      await api(existing ? 'PUT' : 'POST', existing ? `/transfers/${existing.ref_id}` : '/transfers',
        { ...v, from_account: Number(v.from_account), to_account: Number(v.to_account) });
      state.ym = v.date.slice(0, 7);
      toast('Transferência salva');
      refresh();
    },
  });
}

async function togglePaid(it, ym = state.ym, after = refresh) {
  try {
    await api('POST', '/payments', { source: it.source, ref_id: it.ref_id, ym, paid: !it.paid });
    toast(it.paid ? 'Marcada como não paga' : 'Marcada como paga');
    after();
  } catch (e) { toast(e.message, 'error'); }
}

function itemRow(it) {
  const cls = it.kind === 'transfer' ? 'blue' : it.kind === 'entrada' ? 'in' : it.kind === 'saida' ? 'out' : 'inv';
  const ic = it.kind === 'transfer' ? 'repeat' : it.kind === 'entrada' ? 'down' : it.kind === 'saida' ? 'up' : 'trending';
  let amount, tone;
  if (it.kind === 'transfer') { amount = fmt(it.amount); tone = ''; }
  else if (it.kind === 'entrada') { amount = '+ ' + fmt(it.amount); tone = 'pos'; }
  else if (it.kind === 'saida') { amount = '− ' + fmt(it.amount); tone = 'neg'; }
  else { amount = (it.amount >= 0 ? '− ' : '+ ') + fmt(Math.abs(it.amount)); tone = 'inv'; }
  const tagChip = it.source === 'fixa' ? chip('Fixa', 'blue')
    : it.source === 'parcela' ? chip(`Parcela ${it.tag}`, 'amber')
      : it.source === 'fatura' ? chip(`Cartão · ${it.tag}`, 'blue')
        : it.source === 'dividendo' ? chip(it.tag, 'green')
          : it.source === 'invest' ? chip('Investimento', 'purple')
            : it.transfer ? chip('Transferência', 'blue') : null;
  let tools = null;
  if (it.transfer) {
    tools = h('div', { class: 'tools' },
      iconBtn('edit', 'Editar transferência', () => transferForm(it)),
      iconBtn('trash', 'Excluir transferência', () => remove(`/transfers/${it.ref_id}`, 'Excluir esta transferência? O dinheiro volta para a conta de origem.'), 'del'));
  } else if (it.source === 'lancamento') {
    tools = h('div', { class: 'tools' },
      iconBtn('copy', 'Duplicar', () => transactionForm(it.kind, it, { duplicate: true })),
      iconBtn('edit', 'Editar', () => transactionForm(it.kind, it)),
      iconBtn('trash', 'Excluir', () => remove(`/transactions/${it.ref_id}`, `Excluir "${it.description}"?`), 'del'));
  } else if (it.source === 'fatura') {
    tools = h('div', { class: 'tools' }, iconBtn('card', 'Ver compras da fatura', () => openStatement(it.ref_id, state.ym)));
  }
  const payBtn = it.kind === 'saida' && !it.transfer
    ? h('button', { class: 'pay-btn' + (it.paid ? ' on' : ''), type: 'button', 'aria-pressed': String(!!it.paid),
      title: it.paid ? 'Paga. Clique para marcar como não paga' : 'A pagar. Clique para marcar como paga',
      'aria-label': it.paid ? 'Marcar como não paga' : 'Marcar como paga', onclick: () => togglePaid(it) }, icon('check'))
    : null;
  const acct = multiAccount() && !state.account && !it.transfer && it.account_id ? chip(accountName(it.account_id)) : null;
  const status = it.reconciled ? chip('Conciliado', 'green') : it.transfer ? null : it.kind === 'entrada' ? (it.pending ? chip('A receber', 'amber') : null) : it.kind === 'saida' ? (it.pending ? chip('A pagar', 'amber') : null) : null;
  return h('div', { class: 'row' + (it.pending ? ' pending' : '') },
    h('div', { class: `ico ${cls}` }, icon(ic)),
    h('div', { class: 'main-col' }, h('div', { class: 'title' }, it.description),
      h('div', { class: 'meta' }, status, tagChip, acct, it.category && !['invest', 'dividendo', 'fatura', 'transferencia', 'transf_saida', 'transf_entrada'].includes(it.source) ? chip(it.category) : null)),
    h('div', { class: `amount ${tone}` }, amount), h('div', { class: 'pay-slot' }, payBtn), tools || h('div', { class: 'tools-spacer' }));
}

// fatura do cartão (janela)
function openStatement(cardId, ym0) {
  let ym = ym0;
  modal({
    title: 'Fatura do cartão', wide: true,
    build: close => {
      const body = h('div', { class: 'modal-body' });
      const load = async () => {
        let st;
        try { st = await api('GET', `/cards/${cardId}/statement/${ym}`); } catch (e) { toast(e.message, 'error'); close(); return; }
        const reload = () => { load(); refresh(); };
        const nav = h('div', { class: 'stmt-nav' }, iconBtn('left', 'Fatura anterior', () => { ym = ymAdd(ym, -1); load(); }),
          h('span', null, `${st.card.name} · ${ymLabel(ym)}`), iconBtn('right', 'Próxima fatura', () => { ym = ymAdd(ym, 1); load(); }));
        const head = h('div', { class: 'stmt-head' },
          metric('Total da fatura', fmt(st.total), 'neg', true), metric('Fecha em', dateBR(st.closing_date)), metric('Vence em', dateBR(st.due_date)),
          h('div', { class: 'metric' }, h('small', null, 'Situação'),
            btn(st.paid ? 'Paga ✓' : 'Marcar como paga', st.paid ? 'success small' : 'small', () => togglePaid({ source: 'fatura', ref_id: cardId, paid: st.paid }, ym, reload))));
        const lines = st.lines.map(l => h('div', { class: 'row' },
          h('div', { class: 'ico out' }, icon('up')),
          h('div', { class: 'main-col' }, h('div', { class: 'title' }, l.description),
            h('div', { class: 'meta' }, dateBR(l.date), l.source === 'parcela' ? chip(`Parcela ${l.tag}`, 'amber') : null, l.category ? chip(l.category) : null)),
          h('div', { class: 'amount neg' }, '− ' + fmt(l.amount)),
          l.source === 'lancamento'
            ? h('div', { class: 'tools', style: { opacity: 1 } },
              iconBtn('edit', 'Editar', () => transactionForm('saida', { ref_id: l.ref_id, kind: 'saida', description: l.description, category: l.category, amount: l.amount, date: l.date, card_id: cardId }, { stay: true, after: reload })),
              iconBtn('trash', 'Excluir', async () => {
                if (!(await confirmDialog({ message: `Excluir "${l.description}"?` }))) return;
                try { await api('DELETE', `/transactions/${l.ref_id}`); toast('Excluído'); reload(); } catch (e) { toast(e.message, 'error'); }
              }, 'del'))
            : h('div', { class: 'tools-spacer' })));
        body.replaceChildren(nav, head, ...(lines.length ? lines : [h('div', { class: 'muted', style: { padding: '14px 4px' } }, 'Nenhuma compra nesta fatura.')]));
      };
      load();
      return h('div', null, body, h('div', { class: 'modal-foot' },
        btn('Nova compra neste cartão', '', () => { close(); transactionForm('saida', null, { card_id: String(cardId) }); }, 'plus'),
        h('span', { style: { flex: 1 } }), btn('Fechar', '', close)));
    },
  });
}

const filt = { q: '', cat: '', type: '' };

function matchesFilter(it) {
  if (filt.type === 'pendente') { if (!it.pending) return false; }
  else if (filt.type === 'transfer') { if (!it.transfer) return false; }
  else if (filt.type && it.kind !== filt.type) return false;
  if (filt.cat) {
    const cats = (it.parts && it.parts.length ? it.parts.map(p => p.category) : [it.category]).map(c => c || 'Sem categoria');
    if (!cats.includes(filt.cat)) return false;
  }
  const q = norm(filt.q).trim();
  if (q) {
    const hay = norm(`${it.description} ${it.category} ${it.tag} ${(it.parts || []).map(p => p.category).join(' ')}`);
    const digits = q.replace(/[r$\s.]/g, '');
    const amountHit = /^[\d,]+$/.test(digits) && it.amount.toFixed(2).replace('.', ',').includes(digits);
    if (!hay.includes(q) && !amountHit) return false;
  }
  return true;
}

async function pageMonth() {
  const ym = state.ym;
  const d = await api('GET', state.account ? `/month/${ym}/account/${state.account}` : `/month/${ym}`);
  const s = d.summary;
  const go = n => { state.ym = ymAdd(ym, n); refresh(); };
  const monthInput = h('input', { type: 'month', value: ym, 'aria-label': 'Escolher mês',
    onchange: e => { if (e.target.value) { state.ym = e.target.value; refresh(); } },
    onclick: e => { try { e.target.showPicker(); } catch { /* navegador sem showPicker */ } } });
  const picker = h('div', { class: 'month-pick' }, ymLabel(ym).charAt(0).toUpperCase() + ymLabel(ym).slice(1), monthInput);
  const root = h('div');
  root.append(pageHead('Mês a mês', null,
    ym !== ymNow() ? btn('Hoje', 'ghost small', () => { state.ym = ymNow(); refresh(); }) : null,
    h('div', { class: 'month-nav' }, iconBtn('left', 'Mês anterior', () => go(-1)), picker, iconBtn('right', 'Próximo mês', () => go(1))),
    btn('Entrada', 'success', () => transactionForm('entrada'), 'plus'),
    btn('Saída', 'danger-soft', () => transactionForm('saida'), 'plus'),
    multiAccount() ? btn('Transferência', 'small', () => transferForm(), 'repeat') : null));

  if (multiAccount()) {
    const pick = (id) => { state.account = id; refresh(); };
    root.append(h('div', { class: 'acct-chips', role: 'group', 'aria-label': 'Filtrar por conta' },
      h('button', { type: 'button', class: 'acct-chip' + (!state.account ? ' on' : ''), onclick: () => pick('') }, 'Todas as contas'),
      accountsInfo.items.map(a => h('button', { type: 'button', class: 'acct-chip' + (String(state.account) === String(a.id) ? ' on' : ''), onclick: () => pick(String(a.id)) },
        a.name, h('small', { class: 'num' }, fmt(a.balance))))));
  }

  if (d.outside_control) {
    root.append(h('div', { class: 'callout' }, `Este mês é anterior ao início do controle (${ymLabel(d.start_month)}), por isso não entra no cálculo do saldo. Para incluí-lo, ajuste o mês inicial em Configurações.`));
  } else {
    root.append(h('div', { class: 'kpis cols3' },
      kpi('Saldo anterior', fmt(s.saldo_anterior), { tone: toneOf(s.saldo_anterior), sub: 'transportado do mês anterior' }),
      kpi('Entradas recebidas', fmt(s.entradas), { tone: 'pos', dot: '#22c55e', sub: 'com data até hoje' }),
      kpi('Proventos a receber', fmt(s.a_receber), { tone: 'amber', dot: '#f59e0b', sub: 'data futura: ainda não compõe o saldo' }),
      kpi('Saídas pagas', fmt(s.saidas), { tone: 'neg', dot: '#ef4444' }),
      kpi('A pagar', fmt(s.a_pagar), { tone: 'amber', dot: '#f59e0b', sub: 'ainda não pagas: não saíram do saldo' }),
      kpi('Aportes', fmt(s.aportes), { tone: 'inv', dot: '#8b5cf6', sub: 'investimentos' }),
      kpi('Saldo final', fmt(s.saldo_final), { tone: toneOf(s.saldo_final), highlight: s.saldo_final >= 0 ? 'highlight pos-bg' : 'highlight neg-bg', sub: 'só o que já entrou e saiu' }),
      kpi('Saldo previsto', fmt(s.saldo_previsto), { tone: toneOf(s.saldo_previsto), sub: 'com tudo recebido e pago' }),
      kpi('Resultado do mês', signed(s.resultado), { tone: toneOf(s.resultado), sub: `Previsto: ${signed(s.resultado_previsto)}` })));
  }

  const list = h('div');
  const listTitle = h('small');
  const sumLine = h('div', { class: 'muted', style: { fontSize: '12px', margin: '0 4px 8px' }, hidden: true });
  const paintList = () => {
    const shown = d.items.filter(matchesFilter);
    const active = !!(filt.q || filt.cat || filt.type);
    listTitle.textContent = active ? `${shown.length} de ${d.items.length}` : `${d.items.length} no mês`;
    sumLine.hidden = !active || !shown.length;
    if (active) {
      const tot = k => shown.filter(i => i.kind === k).reduce((a, i) => a + i.amount, 0);
      sumLine.textContent = `Filtrado: entradas ${fmt(tot('entrada'))} · saídas ${fmt(tot('saida'))}`;
    }
    const nodes = [];
    if (!d.items.length) {
      nodes.push(emptyState('Nenhuma movimentação neste mês', 'Registre uma entrada ou saída, ou cadastre despesas fixas e parcelas.',
        btn('Nova entrada', 'success', () => transactionForm('entrada'), 'plus'), ' ', btn('Nova saída', 'danger-soft', () => transactionForm('saida'), 'plus')));
    } else if (!shown.length) {
      nodes.push(emptyState('Nada encontrado', 'Nenhuma movimentação deste mês combina com a busca.',
        btn('Limpar filtros', '', () => { filt.q = ''; filt.cat = ''; filt.type = ''; refresh(); })));
    } else {
      let last = '';
      shown.forEach(it => {
        if (it.date !== last) { nodes.push(h('div', { class: 'day-head' }, dayLabel(it.date))); last = it.date; }
        nodes.push(itemRow(it));
      });
    }
    list.replaceChildren(...nodes);
  };

  const catSet = new Set();
  d.items.forEach(i => (i.parts && i.parts.length ? i.parts.map(p => p.category) : [i.category]).forEach(c => { if (i.kind === 'saida') catSet.add(c || 'Sem categoria'); }));
  if (filt.cat && !catSet.has(filt.cat)) filt.cat = '';
  const search = h('input', { type: 'text', placeholder: 'Buscar…', title: 'Busque por descrição, categoria ou valor', value: filt.q, 'aria-label': 'Buscar nas movimentações',
    oninput: e => { filt.q = e.target.value; paintList(); } });
  const catSel = h('select', { 'aria-label': 'Filtrar por categoria', onchange: e => { filt.cat = e.target.value; paintList(); } },
    h('option', { value: '' }, 'Todas as categorias'), [...catSet].sort((a, b) => a.localeCompare(b, 'pt-BR')).map(c => h('option', { value: c }, c)));
  catSel.value = filt.cat;
  const typeSel = h('select', { 'aria-label': 'Filtrar por tipo', onchange: e => { filt.type = e.target.value; paintList(); } },
    [['', 'Tudo'], ['entrada', 'Entradas'], ['saida', 'Saídas'], ['pendente', 'Pendentes'], ['invest', 'Investimentos'], ...(multiAccount() ? [['transfer', 'Transferências']] : [])].map(([v, l]) => h('option', { value: v }, l)));
  typeSel.value = filt.type;
  const filters = h('div', { class: 'filters' }, h('div', { class: 'search' }, icon('search'), search), catSel, typeSel);
  paintList();

  const donut = chartBox(), bars = chartBox();
  const catTotal = d.by_category.reduce((a, c) => a + c.total, 0);
  const rightStack = [h('div', { class: 'card' }, h('div', { class: 'section-title' }, 'Saídas por categoria', btn('Limites', 'ghost small', () => { location.hash = '#/config'; })), donut)];
  if (d.budgets.length) {
    rightStack.unshift(h('div', { class: 'card' + (d.budgets.some(b => b.state !== 'ok') ? ' alert-card' : '') },
      h('div', { class: 'section-title' }, 'Orçamento do mês', h('small', null, 'saídas pagas e a pagar')), budgetBars(d.budgets)));
  }
  rightStack.push(h('div', { class: 'card' }, h('div', { class: 'section-title' }, `Saldo final em ${ym.slice(0, 4)}`, h('small', null, 'futuros = previsto')), bars));
  root.append(h('div', { class: 'grid-2' },
    h('div', { class: 'card' }, h('div', { class: 'section-title' }, 'Movimentações', listTitle), filters, sumLine, list),
    h('div', { class: 'stack' }, rightStack)));

  Charts.donut(donut, { items: d.by_category.map(c => ({ label: c.category, value: c.total })), centerTop: 'Saídas', centerValue: fmt(catTotal), centerSize: 15 });
  const months = d.year_series;
  Charts.bar(bars, {
    labels: months.map(m => MS[+m.ym.slice(5, 7) - 1]), height: 200, highlight: +ym.slice(5, 7) - 1,
    muted: i => months[i].ym > ymNow(),
    series: [{ name: 'Saldo final', values: months.map(m => (m.ym > ymNow() ? m.saldo_previsto : m.saldo_final)), colorFn: v => (v >= 0 ? '#22c55e' : '#ef4444') }],
    tip: i => `${ymLabel(months[i].ym)}\n` + (months[i].saldo_final == null ? 'Fora do controle'
      : months[i].ym > ymNow() ? `Saldo previsto: ${fmt(months[i].saldo_previsto)}` : `Saldo final: ${fmt(months[i].saldo_final)}`),
  });
  return root;
}

// ------------------------------------------------------------------ fixas e parcelas
const changeChips = (changes) => (changes || []).map(c => h('span', { class: 'chip blue chg' },
  `→ ${fmt(c.amount)} desde ${ymBR(c.from_month)}`,
  h('button', { type: 'button', title: 'Remover este reajuste', 'aria-label': 'Remover este reajuste',
    onclick: () => remove(`/amount-changes/${c.id}`, `Remover o reajuste para ${fmt(c.amount)} a partir de ${ymBR(c.from_month)}? O valor anterior volta a valer desse mês em diante.`) }, '×')));

async function fixedForm(existing) {
  const cats = await categoryList();
  const changed = v => !!existing && v.amount != null && Math.abs(v.amount - existing.amount) > 0.004;
  openForm({
    title: existing ? 'Editar item fixo' : 'Novo item fixo',
    intro: 'Valores que se repetem todo mês: salário, aluguel, assinaturas, mensalidades.',
    initial: existing ? { ...existing, amount_from: maxYm(ymNow(), existing.start_month), account_id: accountInitial(existing) } : { kind: 'saida', day: 5, start_month: ymNow(), account_id: accountInitial() },
    datalists: { category: cats },
    fields: [
      { name: 'kind', label: 'Tipo', type: 'segmented', options: [{ value: 'saida', label: 'Despesa fixa', cls: 'out' }, { value: 'entrada', label: 'Receita fixa', cls: 'in' }] },
      { name: 'description', label: 'Descrição', type: 'text', placeholder: 'Ex.: Aluguel, internet, salário' },
      { name: 'amount', label: existing ? 'Valor mensal atual (R$)' : 'Valor mensal (R$)', type: 'money', half: true, placeholder: '0,00' },
      { name: 'day', label: 'Dia do mês', type: 'number', half: true, hint: 'Em meses mais curtos, cai no último dia.' },
      { name: 'amount_from', label: 'O novo valor vale a partir de', type: 'month', showIf: changed,
        hint: 'Reajuste: os meses anteriores continuam com o valor antigo. Para corrigir desde o começo, escolha o mês de início.' },
      { name: 'start_month', label: 'Começa em', type: 'month', half: true },
      { name: 'end_month', label: 'Termina em (opcional)', type: 'month', half: true, hint: 'Deixe vazio se não tiver fim.' },
      { name: 'category', label: 'Categoria (opcional)', type: 'text', list: 'category' },
      ...accountField('Conta'),
    ],
    onSubmit: async v => {
      await api(existing ? 'PUT' : 'POST', existing ? `/fixed/${existing.id}` : '/fixed', { ...v, account_id: accountVal(v) });
      toast(existing && v.amount_from ? `Novo valor vale a partir de ${ymBR(v.amount_from)}; meses anteriores preservados` : 'Item fixo salvo');
      refresh();
    },
  });
}

async function installmentForm(existing) {
  const [cats, cardList] = await Promise.all([categoryList(), api('GET', '/cards').catch(() => [])]);
  const changed = v => !!existing && v.installment_amount != null && Math.abs(v.installment_amount - existing.installment_amount) > 0.004;
  openForm({
    title: existing ? 'Editar compra parcelada' : 'Nova compra parcelada',
    intro: 'Informe o valor de cada parcela. A primeira cai no mês da data escolhida; as demais, nos meses seguintes.',
    initial: existing ? { ...existing, card_id: existing.card_id ? String(existing.card_id) : '', amount_from: maxYm(ymNow(), existing.first_date.slice(0, 7)), account_id: accountInitial(existing) }
      : { first_date: todayISO(), card_id: '', account_id: accountInitial() },
    datalists: { category: cats },
    fields: [
      { name: 'description', label: 'O que foi comprado', type: 'text', placeholder: 'Ex.: Notebook, geladeira' },
      { name: 'installment_amount', label: existing ? 'Valor da parcela atual (R$)' : 'Valor da parcela (R$)', type: 'money', half: true, placeholder: '0,00' },
      { name: 'n_installments', label: 'Número de parcelas', type: 'number', half: true },
      { name: 'amount_from', label: 'O novo valor vale a partir de', type: 'month', showIf: changed,
        hint: 'As parcelas de meses anteriores continuam com o valor antigo.' },
      { name: 'first_date', label: 'Data da 1ª parcela', type: 'date', half: true },
      { name: 'category', label: 'Categoria (opcional)', type: 'text', list: 'category', half: true },
      { name: 'card_id', label: 'No cartão de crédito?', type: 'select', options: [{ value: '', label: 'Não, sai direto da conta' }, ...cardList.map(c => ({ value: String(c.id), label: c.name }))],
        showIf: () => cardList.length > 0, hint: 'No cartão, as parcelas entram na fatura de cada mês.' },
      ...accountField('Conta', { showIf: v => !v.card_id }),
    ],
    onSubmit: async v => {
      await api(existing ? 'PUT' : 'POST', existing ? `/installments/${existing.id}` : '/installments', { ...v, card_id: v.card_id ? Number(v.card_id) : null, account_id: accountVal(v) });
      toast(existing && v.amount_from ? `Novo valor vale a partir de ${ymBR(v.amount_from)}; parcelas anteriores preservadas` : 'Compra parcelada salva');
      refresh();
    },
  });
}

function cardForm(existing) {
  openForm({
    title: existing ? 'Editar cartão' : 'Novo cartão de crédito',
    intro: 'As compras no cartão não saem da conta na hora: entram numa fatura única, descontada no vencimento. Compra feita até o dia do fechamento cai na fatura que fecha neste mês; depois dele, na seguinte.',
    initial: existing ? { ...existing, pay_account_id: String(existing.pay_account_id || accountsInfo.default_id || '') } : { closing_day: 10, due_day: 17, pay_account_id: String(accountsInfo.default_id || '') },
    fields: [
      { name: 'name', label: 'Nome do cartão', type: 'text', placeholder: 'Ex.: Nubank, Itaú Visa' },
      { name: 'closing_day', label: 'Dia do fechamento da fatura', type: 'number', half: true },
      { name: 'due_day', label: 'Dia do vencimento', type: 'number', half: true, hint: 'Se for antes do fechamento, a fatura vence no mês seguinte.' },
      ...(multiAccount() ? [{ name: 'pay_account_id', label: 'Conta que paga a fatura', type: 'select', options: accountsInfo.items.map(a => ({ value: String(a.id), label: a.name })) }] : []),
    ],
    onSubmit: async v => { await api(existing ? 'PUT' : 'POST', existing ? `/cards/${existing.id}` : '/cards', { ...v, pay_account_id: v.pay_account_id ? Number(v.pay_account_id) : null }); toast('Cartão salvo'); refresh(); },
  });
}

function fixedRow(f) {
  const out = f.kind === 'saida';
  const when = `desde ${ymBR(f.start_month)}` + (f.end_month ? ` · até ${ymBR(f.end_month)}` : ' · sem data final');
  return h('div', { class: 'row' + (f.status === 'encerrada' ? ' dim' : '') },
    h('div', { class: `ico ${out ? 'out' : 'in'}` }, icon(out ? 'up' : 'down')),
    h('div', { class: 'main-col' }, h('div', { class: 'title' }, f.description),
      h('div', { class: 'meta' }, f.category ? chip(f.category) : null, chip(`dia ${f.day}`), f.status !== 'ativa' ? chip(f.status === 'futura' ? 'Ainda não começou' : 'Encerrada', 'amber') : null, h('span', null, when)),
      f.changes.length ? h('div', { class: 'meta' }, h('span', null, `começou em ${fmt(f.base_amount)}`), changeChips(f.changes)) : null),
    h('div', { class: `amount ${out ? 'neg' : 'pos'}` }, (out ? '− ' : '+ ') + fmt(f.amount)),
    h('div', { class: 'tools' }, iconBtn('edit', 'Editar', () => fixedForm(f)), iconBtn('trash', 'Excluir', () => remove(`/fixed/${f.id}`, `Excluir "${f.description}"? Ele deixa de aparecer em todos os meses.`), 'del')));
}

function installmentRow(i) {
  const pctDone = Math.round(i.paid / i.n_installments * 100);
  const status = i.status === 'quitada' ? chip('Quitada', 'green') : i.status === 'futura' ? chip('Ainda não começou', 'amber') : null;
  return h('div', { class: 'row' + (i.status === 'quitada' ? ' dim' : '') },
    h('div', { class: 'ico blue' }, icon('repeat')),
    h('div', { class: 'main-col' }, h('div', { class: 'title' }, i.description),
      h('div', { class: 'meta' }, i.category ? chip(i.category) : null, i.card_name ? chip(`Cartão ${i.card_name}`, 'purple') : null, status,
        h('span', null, `${i.paid} de ${i.n_installments} parcelas · restam ${fmt(i.remaining_amount)} · última em ${ymBR(i.last_month)}`)),
      i.changes.length ? h('div', { class: 'meta' }, h('span', null, `começou em ${fmt(i.base_amount)}`), changeChips(i.changes)) : null,
      h('div', { class: 'progress' + (i.status === 'quitada' ? ' green' : '') }, h('span', { style: { width: pctDone + '%' } }))),
    h('div', { style: { textAlign: 'right' } }, h('div', { class: 'amount neg' }, fmt(i.installment_amount)),
      h('div', { class: 'muted', style: { fontSize: '12px' } }, `total ${fmt(i.total)}`)),
    h('div', { class: 'tools' }, iconBtn('edit', 'Editar', () => installmentForm(i)), iconBtn('trash', 'Excluir', () => remove(`/installments/${i.id}`, `Excluir "${i.description}"?`), 'del')));
}

function cardRow(c) {
  const stm = c.statements.map(st => h('button', { type: 'button', class: `chip btnlike ${st.paid ? 'green' : st.open ? 'blue' : 'amber'}`,
    title: `Vence em ${dateBR(st.due_date)}`, onclick: () => openStatement(c.id, st.ym) },
  `${st.open ? 'Aberta' : st.paid ? 'Paga' : 'A pagar'} · ${ymShort(st.ym)} · ${fmt(st.total)}`));
  return h('div', { class: 'list-row' },
    h('div', { class: 'ico blue', style: { width: '38px', height: '38px', borderRadius: '12px', display: 'grid', placeItems: 'center', flex: 'none' } }, icon('card')),
    h('div', { class: 'grow' }, h('b', null, c.name), h('small', null, `fecha dia ${c.closing_day} · vence dia ${c.due_day}`), h('div', { class: 'stmt-chips' }, stm)),
    btn('Nova compra', 'success small', () => transactionForm('saida', null, { card_id: String(c.id) }), 'plus'),
    iconBtn('edit', 'Editar cartão', () => cardForm(c)),
    iconBtn('trash', 'Excluir cartão', () => remove(`/cards/${c.id}`, `Excluir o cartão "${c.name}"?`), 'del'));
}

async function pageRecurring() {
  const d = await api('GET', '/recurring');
  const order = { ativa: 0, futura: 1, encerrada: 2 };
  const fixed = [...d.fixed].sort((a, b) => order[a.status] - order[b.status]);
  const activeFixed = d.fixed.filter(f => f.status === 'ativa');
  const fixOut = activeFixed.filter(f => f.kind === 'saida').reduce((a, f) => a + f.amount, 0);
  const fixIn = activeFixed.filter(f => f.kind === 'entrada').reduce((a, f) => a + f.amount, 0);
  const thisMonth = d.installments.filter(i => i.first_date.slice(0, 7) <= d.today_ym && d.today_ym <= i.last_month).reduce((a, i) => a + i.installment_amount, 0);
  const remaining = d.installments.reduce((a, i) => a + i.remaining_amount, 0);

  const root = h('div');
  root.append(pageHead('Fixas e parcelas', 'Cadastre uma vez; o app lança todos os meses automaticamente.'));
  root.append(h('div', { class: 'kpis' },
    kpi('Despesas fixas / mês', fmt(fixOut), { tone: 'neg', dot: '#ef4444' }),
    kpi('Receitas fixas / mês', fmt(fixIn), { tone: 'pos', dot: '#22c55e' }),
    kpi('Parcelas neste mês', fmt(thisMonth), { tone: 'neg', dot: '#f59e0b' }),
    kpi('Ainda a pagar em parcelas', fmt(remaining), { sub: 'soma das parcelas restantes' })));

  const fixedList = h('div'), instList = h('div'), cardList = h('div');
  if (!fixed.length) fixedList.append(emptyState('Nada por aqui ainda', 'Cadastre salário, aluguel, internet, mensalidades…', btn('Novo item fixo', 'primary', () => fixedForm(), 'plus')));
  fixed.forEach(f => fixedList.append(fixedRow(f)));
  if (!d.installments.length) instList.append(emptyState('Nenhuma compra parcelada', 'Informe o valor da parcela, a data de início e o número de parcelas.', btn('Nova compra parcelada', 'primary', () => installmentForm(), 'plus')));
  d.installments.forEach(i => instList.append(installmentRow(i)));
  if (!d.cards.length) cardList.append(emptyState('Nenhum cartão cadastrado', 'Cadastre o cartão com os dias de fechamento e vencimento. As compras passam a cair na fatura certa e saem da conta só no vencimento.', btn('Novo cartão', 'primary', () => cardForm(), 'plus')));
  d.cards.forEach(c => cardList.append(cardRow(c)));

  root.append(h('div', { class: 'grid-eq' },
    h('div', { class: 'card' }, h('div', { class: 'section-title' }, 'Receitas e despesas fixas', btn('Novo', 'small primary', () => fixedForm(), 'plus')), fixedList),
    h('div', { class: 'card' }, h('div', { class: 'section-title' }, 'Compras parceladas', btn('Nova', 'small primary', () => installmentForm(), 'plus')), instList)));
  root.append(h('div', { class: 'card', style: { marginTop: '18px' } },
    h('div', { class: 'section-title' }, 'Cartões de crédito', btn('Novo cartão', 'small primary', () => cardForm(), 'plus')), cardList));
  return root;
}

// ------------------------------------------------------------------ investimentos: formulários
const tickers = () => (investData ? investData.positions.map(p => p.ticker) : []);

function opForm({ op = 'compra', ticker = '', existing = null, after = refresh } = {}) {
  const known = tickers();
  openForm({
    title: existing ? 'Editar operação' : op === 'venda' ? 'Registrar venda' : 'Registrar compra / aporte',
    intro: 'Cada compra recalcula o preço médio sobre o total da posição (taxas incluídas).',
    initial: existing ? { op: existing.op, ticker: existing.ticker, date: existing.date, quantity: existing.quantity, price: existing.price, fees: existing.fees || null, affects_cash: !!existing.affects_cash, kind: existing.kind || 'acao', account_id: accountInitial(existing) }
      : { op, ticker, date: todayISO(), affects_cash: true, kind: 'acao', account_id: accountInitial() },
    datalists: { tickers: known },
    fields: [
      { name: 'op', label: 'Operação', type: 'segmented', options: [{ value: 'compra', label: 'Compra / aporte', cls: 'inv' }, { value: 'venda', label: 'Venda', cls: 'out' }] },
      { name: 'ticker', label: 'Código do ativo', type: 'text', list: 'tickers', placeholder: 'Ex.: PETR4, HGLG11', half: true },
      { name: 'kind', label: 'Tipo do ativo', type: 'select', half: true, options: [{ value: 'acao', label: 'Ação' }, { value: 'fii', label: 'FII' }, { value: 'etf', label: 'ETF' }, { value: 'bdr', label: 'BDR' }, { value: 'outro', label: 'Outro' }],
        showIf: v => v.op !== 'venda' && !known.includes(String(v.ticker || '').trim().toUpperCase()) },
      { name: 'quantity', label: 'Quantidade', type: 'number', half: true, placeholder: '100' },
      { name: 'price', label: 'Preço pago por cota/ação (R$)', type: 'money', half: true, placeholder: '0,00' },
      { name: 'date', label: 'Data', type: 'date', half: true },
      { name: 'fees', label: 'Taxas / corretagem (R$)', type: 'money', half: true, placeholder: '0,00', hint: 'Opcional. Entram no preço médio.' },
      { name: 'affects_cash', label: 'Movimentar o saldo em conta', type: 'check', hint: 'Compra sai do caixa do mês; venda entra no caixa. Desmarque se o dinheiro já estava na corretora.' },
      ...accountField('Conta do dinheiro', { showIf: v => v.affects_cash }),
    ],
    onSubmit: async v => {
      await api(existing ? 'PUT' : 'POST', existing ? `/ops/${existing.id}` : '/ops', { ...v, ticker: String(v.ticker || '').trim().toUpperCase(), fees: v.fees ?? 0, account_id: accountVal(v) });
      toast(existing ? 'Operação atualizada — preço médio recalculado' : op === 'venda' || v.op === 'venda' ? 'Venda registrada' : 'Compra registrada — preço médio atualizado');
      after();
    },
  });
}

function dividendForm(p, existing = null, after = refresh) {
  const known = tickers();
  openForm({
    title: existing ? 'Editar provento' : 'Registrar provento',
    initial: existing ? { ticker: existing.ticker, tipo: existing.tipo, date: existing.date, amount: existing.amount, affects_cash: !!existing.affects_cash, account_id: accountInitial(existing) }
      : { ticker: p ? p.ticker : '', tipo: p && p.kind === 'fii' ? 'Rendimento' : 'Dividendo', date: todayISO(), affects_cash: true, account_id: accountInitial() },
    datalists: { tickers: known },
    fields: [
      { name: 'ticker', label: 'Código do ativo', type: 'text', list: 'tickers', half: true },
      { name: 'tipo', label: 'Tipo', type: 'select', half: true, options: ['Dividendo', 'JCP', 'Rendimento'].map(t => ({ value: t, label: t })) },
      { name: 'amount', label: 'Valor recebido (R$)', type: 'money', half: true, placeholder: '0,00', hint: 'Total líquido creditado.' },
      { name: 'date', label: 'Data do pagamento', type: 'date', half: true },
      { name: 'affects_cash', label: 'Creditar no saldo em conta', type: 'check', hint: 'Soma o provento às entradas do mês.' },
      ...accountField('Conta que recebe', { showIf: v => v.affects_cash }),
    ],
    onSubmit: async v => { await api(existing ? 'PUT' : 'POST', existing ? `/dividends/${existing.id}` : '/dividends', { ...v, ticker: String(v.ticker || '').trim().toUpperCase(), account_id: accountVal(v) }); toast(existing ? 'Provento atualizado' : 'Provento registrado'); after(); },
  });
}

function priceForm(p) {
  openForm({
    title: `Cotação de ${p.ticker}`,
    intro: 'O app não consulta a internet: informe a cotação que você vê na corretora para calcular o valor atual. Deixe vazio para usar o preço de custo.',
    initial: { price: p.current_price },
    fields: [{ name: 'price', label: 'Cotação atual por cota (R$)', type: 'money', placeholder: '0,00' }],
    onSubmit: async v => { await api('PUT', `/assets/${p.id}/price`, { price: v.price }); toast('Cotação atualizada'); refresh(); },
  });
}

function openAssetHistory(p) {
  modal({
    title: `Histórico de ${p.ticker}`, wide: true,
    build: close => {
      const body = h('div', { class: 'modal-body' });
      const load = async () => {
        let hist;
        try { hist = await api('GET', `/assets/${p.id}/history`); } catch { close(); return; }
        const del = (path, msg) => async () => {
          if (!(await confirmDialog({ message: msg }))) return;
          try { await api('DELETE', path); toast('Excluído'); await load(); refresh(); } catch (e) { toast(e.message, 'error'); }
        };
        const ops = hist.ops.map(o => h('div', { class: 'row' },
          h('div', { class: `ico ${o.op === 'compra' ? 'inv' : 'out'}` }, icon(o.op === 'compra' ? 'trending' : 'up')),
          h('div', { class: 'main-col' }, h('div', { class: 'title' }, `${o.op === 'compra' ? 'Compra' : 'Venda'} · ${qty(o.quantity)} × ${price4(o.price)}`),
            h('div', { class: 'meta' }, dateBR(o.date), o.fees ? h('span', null, `taxas ${fmt(o.fees)}`) : null, o.affects_cash ? null : chip('fora do saldo em conta'))),
          h('div', { class: 'amount' }, fmt(o.quantity * o.price)),
          iconBtn('edit', 'Editar', () => opForm({ existing: { ...o, ticker: hist.asset.ticker, kind: hist.asset.kind }, after: () => { load(); refresh(); } })),
          iconBtn('trash', 'Excluir', del(`/ops/${o.id}`, 'Excluir esta operação? O preço médio será recalculado.'), 'del')));
        const divs = hist.dividends.map(d => h('div', { class: 'row' },
          h('div', { class: 'ico in' }, icon('down')),
          h('div', { class: 'main-col' }, h('div', { class: 'title' }, d.tipo), h('div', { class: 'meta' }, dateBR(d.date), d.affects_cash ? null : chip('fora do saldo em conta'))),
          h('div', { class: 'amount pos' }, '+ ' + fmt(d.amount)),
          iconBtn('edit', 'Editar', () => dividendForm(null, { ...d, ticker: hist.asset.ticker }, () => { load(); refresh(); })),
          iconBtn('trash', 'Excluir', del(`/dividends/${d.id}`, 'Excluir este provento?'), 'del')));
        body.replaceChildren(
          h('div', { class: 'hist-sec' }, h('h3', null, 'Operações'), ops.length ? ops : h('div', { class: 'muted' }, 'Nenhuma operação.')),
          h('div', { class: 'hist-sec' }, h('h3', null, 'Proventos'), divs.length ? divs : h('div', { class: 'muted' }, 'Nenhum provento lançado.')));
      };
      load();
      return h('div', null, body, h('div', { class: 'modal-foot' },
        btn('Excluir ativo e histórico', 'danger-soft small', async () => {
          if (!(await confirmDialog({ message: `Excluir ${p.ticker} com todas as operações e proventos?` }))) return;
          try { await api('DELETE', `/assets/${p.id}`); toast('Ativo excluído'); close(); refresh(); } catch (e) { toast(e.message, 'error'); }
        }), h('span', { style: { flex: 1 } }), btn('Fechar', '', close)));
    },
  });
}

function fiForm(existing) {
  openForm({
    title: existing ? 'Editar investimento' : 'Novo investimento em renda fixa',
    intro: 'Para CDB, LCI/LCA, caixinhas e fundos que rendem um percentual do CDI.',
    initial: existing ? { ...existing } : { pct_cdi: 100, tax: 'regressivo' },
    fields: [
      { name: 'name', label: 'Nome', type: 'text', placeholder: 'Ex.: CDB Banco X, Caixinha Nubank' },
      { name: 'pct_cdi', label: 'Rende quantos % do CDI?', type: 'number', placeholder: '100', hint: 'Ex.: 100 para 100% do CDI, 110 para 110%.' },
      { name: 'tax', label: 'Imposto de renda', type: 'select', options: [{ value: 'regressivo', label: 'Tabela regressiva (CDB, Tesouro, LC, fundos)' }, { value: 'isento', label: 'Isento (LCI, LCA, poupança)' }],
        hint: 'Usado para estimar o valor líquido. Tabela regressiva: 22,5% até 180 dias, 20% até 360, 17,5% até 720 e 15% acima; IOF nos primeiros 30 dias.' },
    ],
    onSubmit: async v => { await api(existing ? 'PUT' : 'POST', existing ? `/fi/${existing.id}` : '/fi', v); toast('Investimento salvo'); refresh(); },
  });
}

function fiMoveForm(p, kind = 'aporte', existing = null, after = refresh) {
  openForm({
    title: existing ? `Editar movimentação · ${p.name}` : p.name,
    intro: 'Aporte e resgate mudam o saldo; rendimento é o valor creditado pelo banco no período (não mexe no saldo em conta).',
    initial: existing ? { kind: existing.kind, date: existing.date, amount: existing.amount, affects_cash: !!existing.affects_cash, account_id: accountInitial(existing) } : { kind, date: todayISO(), affects_cash: true, account_id: accountInitial() },
    fields: [
      { name: 'kind', label: 'Movimentação', type: 'segmented', options: [{ value: 'aporte', label: 'Aporte', cls: 'inv' }, { value: 'resgate', label: 'Resgate', cls: 'out' }, { value: 'rendimento', label: 'Rendimento', cls: 'in' }] },
      { name: 'amount', label: 'Valor (R$)', type: 'money', half: true, placeholder: '0,00' },
      { name: 'date', label: 'Data', type: 'date', half: true },
      { name: 'affects_cash', label: 'Movimentar o saldo em conta', type: 'check', hint: 'Aporte sai do caixa do mês; resgate entra.', showIf: v => v.kind !== 'rendimento' },
      ...accountField('Conta do dinheiro', { showIf: v => v.kind !== 'rendimento' && v.affects_cash }),
    ],
    onSubmit: async v => { await api(existing ? 'PUT' : 'POST', existing ? `/fi/moves/${existing.id}` : '/fi/moves', { ...v, fi_id: p.id, account_id: accountVal(v) }); toast(existing ? 'Movimentação atualizada' : 'Movimentação registrada'); after(); },
  });
}

function openFiHistory(p) {
  modal({
    title: `Histórico · ${p.name}`, wide: true,
    build: close => {
      const body = h('div', { class: 'modal-body' });
      const labels = { aporte: ['Aporte', 'inv', 'trending'], resgate: ['Resgate', 'out', 'up'], rendimento: ['Rendimento', 'in', 'down'] };
      const load = async () => {
        let hist;
        try { hist = await api('GET', `/fi/${p.id}/history`); } catch { close(); return; }
        const rows = hist.moves.map(m => h('div', { class: 'row' },
          h('div', { class: `ico ${labels[m.kind][1]}` }, icon(labels[m.kind][2])),
          h('div', { class: 'main-col' }, h('div', { class: 'title' }, labels[m.kind][0]), h('div', { class: 'meta' }, dateBR(m.date), m.kind !== 'rendimento' && !m.affects_cash ? chip('fora do saldo em conta') : null)),
          h('div', { class: 'amount' }, (m.kind === 'resgate' ? '− ' : '+ ') + fmt(m.amount)),
          iconBtn('edit', 'Editar', () => fiMoveForm(p, m.kind, m, () => { load(); refresh(); })),
          iconBtn('trash', 'Excluir', async () => {
            if (!(await confirmDialog({ message: 'Excluir esta movimentação?' }))) return;
            try { await api('DELETE', `/fi/moves/${m.id}`); toast('Excluído'); await load(); refresh(); } catch (e) { toast(e.message, 'error'); }
          }, 'del')));
        body.replaceChildren(...(rows.length ? rows : [h('div', { class: 'muted' }, 'Nenhuma movimentação ainda.')]));
      };
      load();
      return h('div', null, body, h('div', { class: 'modal-foot' }, btn('Fechar', '', close)));
    },
  });
}

async function cdiForm() {
  const s = await api('GET', '/settings');
  openForm({
    title: 'CDI considerado',
    intro: 'Usado para projetar a renda fixa. Consulte a taxa vigente (ex.: no site do Banco Central ou da B3) e atualize quando mudar.',
    initial: { cdi_annual: s.cdi_annual },
    fields: [{ name: 'cdi_annual', label: 'CDI anual (% ao ano)', type: 'number', placeholder: '13,65' }],
    onSubmit: async v => { await api('PUT', '/settings', { ...s, cdi_annual: v.cdi_annual }); toast('CDI atualizado'); refresh(); },
  });
}

// ------------------------------------------------------------------ investimentos: abas
function positionCard(p) {
  const hasPrice = p.current_price != null;
  const quote = h('div', { class: 'metric' }, h('small', null, 'Cotação atual'),
    h('b', { class: 'num' }, hasPrice ? price4(p.current_price) : 'não informada', iconBtn('edit', 'Informar cotação', () => priceForm(p))));
  return h('div', { class: 'card asset' + (p.closed ? ' dim' : '') },
    h('div', { class: 'asset-top' }, h('span', { class: 'tk' }, p.ticker), chip(p.kind_label, 'blue'), p.closed ? chip('Posição encerrada') : null, h('span', { class: 'grow' }),
      p.closed ? null : btn('Aportar', 'inv-soft small', () => opForm({ ticker: p.ticker }), 'plus'),
      btn('Provento', 'success small', () => dividendForm(p), 'plus'),
      btn('Histórico', 'small', () => openAssetHistory(p), 'clock')),
    h('div', { class: 'metrics' },
      p.closed ? null : metric('Quantidade', qty(p.qty)),
      p.closed ? null : metric('Preço médio', price4(p.avg_price), '', true),
      p.closed ? null : metric('Custo total', fmt(p.cost)),
      p.closed ? null : quote,
      p.closed ? null : metric(hasPrice ? 'Valor atual' : 'Valor (a custo)', fmt(p.value)),
      p.closed ? null : metric('Resultado', hasPrice ? `${signed(p.result)} (${pct(p.result_pct)})` : '—', hasPrice ? toneOf(p.result) : ''),
      metric('Proventos (12 meses)', fmt(p.div_12m)),
      p.closed ? null : metric('Yield on cost', pct(p.yoc_12m), 'pos', true),
      metric('Proventos acumulados', fmt(p.div_total)),
      p.realized ? metric('Lucro realizado', signed(p.realized), toneOf(p.realized)) : null));
}

function tabResumo(d) {
  const t = d.totals;
  const root = h('div');
  const hasAny = d.positions.length || d.fixed_income.length;
  root.append(h('div', { class: 'kpis' },
    kpi('Total investido', fmt(t.total), { dot: '#6b7280', sub: 'renda fixa + renda variável' }),
    kpi('Renda variável', fmt(t.rv_value), { dot: '#8b5cf6', sub: `custo ${fmt(t.rv_cost)}` }),
    kpi('Renda fixa (CDI)', fmt(t.rf_value), { dot: '#0ea5e9' }),
    kpi('Proventos em 12 meses', fmt(t.div_12m), { tone: 'pos', dot: '#22c55e' }),
    kpi('Yield on cost da carteira', pct(t.yoc_portfolio), { tone: 'pos', sub: 'proventos 12m ÷ custo' })));
  if (!hasAny) {
    root.append(h('div', { class: 'card' }, emptyState('Comece registrando seus investimentos', 'Cadastre compras de ações/FIIs ou um investimento de renda fixa.',
      btn('Registrar compra', 'primary', () => opForm(), 'plus'), ' ', btn('Novo investimento em CDI', '', () => fiForm(), 'plus'))));
    return root;
  }
  const evo = chartBox(), alloc = chartBox();
  root.append(h('div', { class: 'grid-2' },
    h('div', { class: 'card' }, h('div', { class: 'section-title' }, 'Evolução do patrimônio investido', h('small', null, 'renda variável a preço de custo')), evo),
    h('div', { class: 'card' }, h('div', { class: 'section-title' }, 'Distribuição da carteira'), alloc)));
  Charts.bar(evo, {
    labels: d.evolution.map(e => ymShort(e.ym)), height: 250, highlight: d.evolution.length - 1,
    series: [{ name: 'Renda fixa', color: '#0ea5e9', values: d.evolution.map(e => e.rf) }, { name: 'Renda variável', color: '#8b5cf6', values: d.evolution.map(e => e.rv) }],
  });
  Charts.donut(alloc, { items: d.allocation, centerTop: 'Investido', centerValue: fmt(t.total), centerSize: 15 });
  return root;
}

function tabAcoes(d) {
  const root = h('div');
  const active = d.positions.filter(p => !p.closed).sort((a, b) => b.value - a.value);
  const closed = d.positions.filter(p => p.closed);
  root.append(h('div', { class: 'actions', style: { marginBottom: '16px' } },
    btn('Compra / aporte', 'primary', () => opForm(), 'plus'), btn('Venda', '', () => opForm({ op: 'venda' })), btn('Provento', 'success', () => dividendForm(), 'plus')));
  if (!d.positions.length) {
    root.append(h('div', { class: 'card' }, emptyState('Nenhum ativo na carteira', 'Registre a primeira compra e o app calcula o preço médio automaticamente.', btn('Registrar compra', 'primary', () => opForm(), 'plus'))));
    return root;
  }
  active.forEach(p => root.append(positionCard(p)));
  if (closed.length) {
    root.append(h('div', { class: 'section-title', style: { margin: '22px 0 10px' } }, 'Posições encerradas'));
    closed.forEach(p => root.append(positionCard(p)));
  }
  root.append(h('div', { class: 'note', style: { marginTop: '16px' } }, h('b', null, 'Yield on cost'), ' = proventos recebidos nos últimos 12 meses ÷ custo total da posição (preço médio × quantidade). Ele se recalcula sozinho a cada novo aporte.'));
  return root;
}

function fiCard(p) {
  return h('div', { class: 'card asset' },
    h('div', { class: 'asset-top' }, h('span', { class: 'tk' }, p.name), chip(`${qty(p.pct_cdi)}% do CDI`, 'blue'), chip(`≈ ${num(p.taxa_anual)}% a.a.`), p.tax === 'isento' ? chip('Isento de IR', 'green') : null, h('span', { class: 'grow' }),
      btn('Aportar', 'inv-soft small', () => fiMoveForm(p, 'aporte'), 'plus'), btn('Resgatar', 'small', () => fiMoveForm(p, 'resgate')),
      btn('Rendimento', 'success small', () => fiMoveForm(p, 'rendimento'), 'plus'), btn('Histórico', 'small', () => openFiHistory(p), 'clock'),
      iconBtn('edit', 'Editar', () => fiForm(p)),
      iconBtn('trash', 'Excluir', () => remove(`/fi/${p.id}`, `Excluir "${p.name}" e todo o histórico?`), 'del')),
    h('div', { class: 'metrics' },
      metric('Saldo atual', fmt(p.saldo), '', true),
      metric('Aportado (líquido)', fmt(p.aportado)),
      metric('Rendimento no ano', fmt(p.rendimento_ano), 'pos'),
      metric('Rendimento acumulado', fmt(p.rendimento_total), 'pos'),
      metric('Projeção até dez.', '+ ' + fmt(p.projecao_resto), 'pos'),
      metric('Saldo em dezembro', fmt(p.saldo_dezembro)),
      p.tax === 'isento' ? null : metric(`IR estimado hoje (${num(p.aliquota_hoje, 1)}%)`, fmt(p.ir_hoje), 'neg'),
      p.tax === 'isento' ? null : metric('Líquido hoje', fmt(p.liquido_hoje)),
      p.tax === 'isento' ? null : metric('Líquido em dezembro', fmt(p.liquido_dezembro))));
}

function tabRendaFixa(d) {
  const root = h('div');
  root.append(h('div', { class: 'actions', style: { marginBottom: '16px', justifyContent: 'space-between' } },
    btn('Novo investimento em CDI', 'primary', () => fiForm(), 'plus'),
    h('div', { class: 'muted' }, `CDI considerado: ${num(d.cdi_annual)}% a.a. `, btn('alterar', 'ghost small', cdiForm))));
  if (!d.fixed_income.length) {
    root.append(h('div', { class: 'card' }, emptyState('Nenhum investimento em renda fixa', 'Cadastre um CDB, LCI/LCA ou caixinha e informe quantos % do CDI ele rende.', btn('Novo investimento em CDI', 'primary', () => fiForm(), 'plus'))));
    return root;
  }
  d.fixed_income.forEach(p => root.append(fiCard(p)));
  return root;
}

function tabProjecao(d) {
  const pr = d.projection, s = pr.summary, yy = String(pr.year).slice(2);
  const root = h('div');
  root.append(h('div', { class: 'kpis' },
    kpi(`Rendimento já recebido em ${pr.year}`, fmt(s.ytd_total), { tone: 'pos', sub: `renda fixa ${fmt(s.ytd_rf)} · proventos ${fmt(s.ytd_div)}` }),
    kpi('Projetado até dezembro', fmt(s.proj_rf + s.proj_div), { tone: 'pos', sub: `renda fixa ${fmt(s.proj_rf)} · proventos ${fmt(s.proj_div)}` }),
    kpi(`Total esperado em ${pr.year}`, fmt(s.year_total), { tone: 'pos', highlight: 'highlight pos-bg', sub: s.ir_ano > 0 ? `≈ ${fmt(s.year_total_liquido)} após IR estimado` : undefined }),
    kpi('Yield on cost projetado', pct(s.yoc_year), { sub: 'proventos do ano ÷ custo da renda variável' }),
    kpi(`Patrimônio investido em dez/${yy}`, fmt(s.patrimonio_dezembro), { sub: s.ir_dezembro > 0 ? `sem novos aportes · ≈ ${fmt(s.patrimonio_dezembro_liquido)} após IR` : 'sem novos aportes' })));

  const rows = pr.rows, cur = pr.current_month - 1;
  const line = chartBox(), bars = chartBox();
  root.append(h('div', { class: 'grid-eq' },
    h('div', { class: 'card' }, h('div', { class: 'section-title' }, 'Patrimônio investido no ano', h('small', null, 'tracejado = projeção')), line),
    h('div', { class: 'card' }, h('div', { class: 'section-title' }, 'Rendimento por mês', h('small', null, 'barras claras = projeção')), bars)));
  Charts.line(line, {
    labels: rows.map(r => MS[+r.ym.slice(5, 7) - 1]), height: 250,
    series: [
      { name: 'Realizado', color: '#6b7280', area: true, values: rows.map((r, i) => (i <= cur ? r.total : null)) },
      { name: 'Projetado', color: '#f59e0b', dash: true, values: rows.map((r, i) => (i >= cur ? r.total : null)) },
    ],
    tip: i => `${ymLabel(rows[i].ym)}${rows[i].projected ? ' (projeção)' : ''}\nRenda fixa: ${fmt(rows[i].rf)}\nRenda variável (custo): ${fmt(rows[i].rv)}\nTotal: ${fmt(rows[i].total)}`,
  });
  Charts.bar(bars, {
    labels: rows.map(r => MS[+r.ym.slice(5, 7) - 1]), height: 250, highlight: cur, muted: i => rows[i].projected,
    series: [{ name: 'Renda fixa', color: '#0ea5e9', values: rows.map(r => r.rf_yield) }, { name: 'Proventos', color: '#22c55e', values: rows.map(r => r.div) }],
  });
  root.append(h('div', { class: 'card', style: { marginTop: '18px' } },
    h('div', { class: 'section-title' }, 'Como a projeção é calculada', btn('Alterar CDI', 'small', cdiForm)),
    h('div', { class: 'note' },
      h('div', null, h('b', null, 'Renda fixa: '), `cada investimento rende o percentual do CDI informado (CDI considerado: ${num(pr.cdi_annual)}% a.a.) ao mês, sobre o saldo do fim do mês anterior.`),
      h('div', { style: { marginTop: '6px' } }, h('b', null, 'Proventos: '), `média mensal recebida nos últimos 12 meses (${fmt(pr.avg_monthly_dividends)} por mês), contada desde o primeiro provento lançado.`),
      h('div', { style: { marginTop: '6px' } }, h('b', null, 'Renda variável: '), 'permanece a preço de custo; a valorização das cotas não é projetada. Meses futuros não incluem novos aportes.'),
      h('div', { style: { marginTop: '6px' } }, h('b', null, 'Imposto de renda (estimativa): '),
        `os valores acima são brutos. O IR estimado em dezembro é ${fmt(s.ir_dezembro)} se tudo fosse resgatado, usando a tabela regressiva pela idade de cada aporte (e IOF nos primeiros 30 dias). Produtos isentos (LCI/LCA) não entram. Confirme o valor real no extrato da instituição.`))));
  return root;
}

async function pageInvest() {
  const d = await api('GET', '/invest');
  investData = d;
  const tabs = [['resumo', 'Resumo', tabResumo], ['acoes', 'Ações e FIIs', tabAcoes], ['rf', 'Renda fixa (CDI)', tabRendaFixa], ['proj', 'Projeção do ano', tabProjecao]];
  const root = h('div');
  root.append(pageHead('Investimentos', `Total investido: ${fmt(d.totals.total)}`));
  root.append(h('div', { class: 'tabs', role: 'tablist' }, tabs.map(([id, label]) =>
    h('button', { class: 'tab' + (state.investTab === id ? ' active' : ''), type: 'button', role: 'tab', onclick: () => { state.investTab = id; refresh(); } }, label))));
  const current = tabs.find(t => t[0] === state.investTab) || tabs[0];
  root.append(current[2](d));
  return root;
}

// ------------------------------------------------------------------ configurações
async function budgetForm(existing) {
  const cats = await categoryList();
  openForm({
    title: existing ? 'Editar limite' : 'Novo limite mensal',
    intro: 'Quanto você quer gastar, no máximo, por mês em uma categoria. O app avisa quando passar de 80% e quando estourar.',
    initial: existing ? { category: existing.category, amount: existing.limit ?? existing.amount } : {},
    datalists: { category: cats },
    fields: [
      { name: 'category', label: 'Categoria', type: 'text', list: 'category', placeholder: 'Ex.: Mercado' },
      { name: 'amount', label: 'Limite por mês (R$)', type: 'money', placeholder: '0,00' },
    ],
    onSubmit: async v => { await api(existing ? 'PUT' : 'POST', existing ? `/budgets/${existing.id}` : '/budgets', v); toast('Limite salvo'); refresh(); },
  });
}

const kb = n => (n >= 1048576 ? `${num(n / 1048576, 1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

async function restoreFrom(label, call) {
  const ok = await confirmDialog({ title: 'Restaurar backup', confirmLabel: 'Restaurar', danger: true,
    message: `Os dados atuais serão substituídos pelos de ${label}. Antes disso o app guarda uma cópia de segurança do que existe agora, então dá para desfazer. Continuar?` });
  if (!ok) return;
  try { await call(); toast('Backup restaurado'); refresh(); } catch (e) { toast(e.message, 'error'); }
}

async function fileToBase64(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

function resetForm() {
  openForm({
    title: 'Resetar dados', submitLabel: 'Apagar tudo', danger: true,
    intro: 'Apaga TODOS os lançamentos, fixas, parcelas, cartões, investimentos, extratos importados, orçamentos e configurações, e deixa o app como novo. Não dá para desfazer.',
    fields: [
      { name: 'wipe_backups', label: 'Apagar também todos os backups', type: 'check',
        hint: 'Os backups guardados no app contêm seus dados antigos. Sem marcar, o app guarda uma cópia de segurança "Antes de restaurar" para você poder voltar atrás.' },
      { name: 'confirm', label: 'Para confirmar, digite RESETAR', type: 'text', placeholder: 'RESETAR' },
    ],
    onSubmit: async v => {
      if (String(v.confirm || '').trim().toUpperCase() !== 'RESETAR') throw new Error('Digite RESETAR para confirmar.');
      const r = await api('POST', '/reset', { confirm: v.confirm, wipe_backups: !!v.wipe_backups });
      state.ym = ymNow(); state.bankView = 'pendente'; state.bankSel = new Set();
      toast(r.safety ? 'Dados apagados. Uma cópia de segurança foi guardada.' : 'Dados e backups apagados. O app está limpo.');
      location.hash = '#/visao';
      refresh();
    },
  });
}

function resetCard() {
  return h('div', { class: 'card alert-card', style: { marginTop: '18px' } },
    h('div', { class: 'section-title' }, 'Resetar dados'),
    h('p', { class: 'muted', style: { marginBottom: '12px' } }, 'Apaga tudo e deixa o aplicativo como recém-instalado. Salve uma cópia antes, caso queira voltar atrás.'),
    btn('Resetar dados…', 'danger-soft', resetForm, 'trash'));
}


// ------------------------------------------------------------------ cópia fora do aparelho
function exportDays() {
  const last = lastExport();
  if (!last) return null;
  const t = Date.parse(last);
  return Number.isNaN(t) ? null : Math.max(0, Math.floor((Date.now() - t) / 86400000));
}

function exportCard() {
  const days = exportDays();
  const late = days === null || days >= 7;
  const last = lastExport();
  return h('div', { class: 'card' + (late ? ' alert-card' : '') },
    h('div', { class: 'section-title' }, 'Cópia fora do aparelho',
      h('span', { class: 'chip ' + (late ? 'amber' : 'green') }, days === null ? 'Nunca feita' : late ? 'Atrasada' : 'Em dia')),
    h('p', { class: 'muted', style: { marginBottom: '12px' } },
      'Seus dados ficam só neste aparelho, dentro do app. Se o celular for perdido, quebrar ou o navegador limpar os dados do app, eles se perdem. '
      + 'Salve uma cópia em outro lugar (iCloud Drive, e-mail, computador) de vez em quando: é o seu seguro.'),
    h('div', { class: 'list-row' }, h('div', { class: 'grow' }, h('b', null, 'Última cópia salva'),
      h('small', null, last && !Number.isNaN(Date.parse(last))
        ? `${new Date(last).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })}${days ? ` · há ${days} dia(s)` : ' · hoje'}`
        : 'ainda nenhuma'))),
    h('div', { class: 'actions', style: { marginTop: '12px' } },
      btn('Salvar uma cópia agora', 'primary small', async () => { if (await exportBackup()) refresh(); }, 'shield')),
    h('p', { class: 'muted', style: { marginTop: '10px', fontSize: '12px' } },
      'Para voltar a ter os dados (celular novo, por exemplo): abra o app, vá em "Restaurar de um arquivo…" aqui ao lado e escolha a cópia salva.'));
}

const ACCOUNT_KINDS = [['corrente', 'Conta corrente'], ['poupanca', 'Poupança'], ['dinheiro', 'Dinheiro (carteira)'], ['outra', 'Outra']];

function accountForm(existing) {
  openForm({
    title: existing ? 'Editar conta' : 'Nova conta',
    intro: existing ? null : 'Cada conta tem seu próprio saldo. Use para conta corrente, poupança, dinheiro na carteira etc.',
    initial: existing ? { name: existing.name, kind: existing.kind, opening_balance: existing.opening_balance } : { kind: 'corrente', opening_balance: 0 },
    fields: [
      { name: 'name', label: 'Nome da conta', type: 'text', placeholder: 'Ex.: Itaú, Nubank, Carteira' },
      { name: 'kind', label: 'Tipo', type: 'select', options: ACCOUNT_KINDS.map(([value, label]) => ({ value, label })), half: true },
      { name: 'opening_balance', label: 'Saldo inicial (R$)', type: 'money', half: true, placeholder: '0,00', hint: 'O quanto havia nela no início do controle.' },
    ],
    onSubmit: async v => {
      await api(existing ? 'PUT' : 'POST', existing ? `/accounts/${existing.id}` : '/accounts', { ...v, opening_balance: v.opening_balance ?? 0 });
      toast('Conta salva');
      refresh();
    },
  });
}

function accountsCard() {
  const rows = accountsInfo.items.map(a => h('div', { class: 'list-row' },
    h('div', { class: 'grow' }, h('b', null, a.name, a.id === accountsInfo.default_id ? h('span', { style: { marginLeft: '8px' } }, chip('padrão')) : null),
      h('small', null, `${a.kind_label} · saldo inicial ${fmt(a.opening_balance)}`)),
    h('b', { class: 'num ' + toneOf(a.balance) }, fmt(a.balance)),
    iconBtn('edit', 'Editar', () => accountForm(a)),
    iconBtn('trash', 'Excluir', () => remove(`/accounts/${a.id}`, `Excluir a conta "${a.name}"?`), 'del')));
  return h('div', { class: 'card' },
    h('div', { class: 'section-title', style: { marginBottom: '6px' } }, 'Contas', btn('Nova conta', 'small primary', () => accountForm(), 'plus')),
    h('p', { class: 'muted', style: { marginBottom: '8px', fontSize: '12px' } }, 'Saldo de hoje por conta. Lançamentos novos vão para a conta escolhida no formulário; os antigos ficam na conta padrão (a primeira).'),
    rows,
    rows.length > 1 ? h('div', { class: 'list-row' }, h('div', { class: 'grow' }, h('b', null, 'Total em contas')), h('b', { class: 'num' }, fmt(accountsInfo.total))) : null);
}


async function pageSettings() {
  const [s, bk, budgets] = await Promise.all([api('GET', '/settings'), api('GET', '/backups'), api('GET', '/budgets')]);
  const balance = h('input', { type: 'text', id: 's-balance', inputmode: 'decimal', value: inputNum(s.opening_balance), placeholder: '0,00' });
  const month = h('input', { type: 'month', id: 's-month', value: s.opening_month });
  const cdi = h('input', { type: 'text', id: 's-cdi', inputmode: 'decimal', value: inputNum(s.cdi_annual) });
  const error = h('div', { class: 'form-error', hidden: true });
  const field = (label, input, hint) => h('div', { class: 'field half' }, h('label', { for: input.id }, label), input, hint ? h('div', { class: 'hint' }, hint) : null);
  const save = async () => {
    error.hidden = true;
    const ob = balance.value.trim() === '' ? 0 : parseBR(balance.value), cd = parseBR(cdi.value);
    if (Number.isNaN(ob) || Number.isNaN(cd)) { error.textContent = 'Confira os números informados.'; error.hidden = false; return; }
    try { await api('PUT', '/settings', { ...(multiAccount() ? {} : { opening_balance: ob }), opening_month: month.value, cdi_annual: cd }); toast('Configurações salvas'); refresh(); }
    catch (e) { error.textContent = e.message; error.hidden = false; }
  };

  const picker = h('input', { type: 'file', hidden: true, 'aria-label': 'Escolher arquivo de backup',
    onchange: async e => {
      const file = e.target.files[0];
      e.target.value = '';
      if (!file) return;
      await restoreFrom(`"${file.name}"`, async () => api('POST', '/backups/restore-upload', { data: await fileToBase64(file) }));
    } });
  const backupRows = bk.items.length ? bk.items.map(b => h('div', { class: 'list-row' },
    h('div', { class: 'grow' }, h('b', null, b.kind_label), h('small', null, `${dateBR(b.modified.slice(0, 10))} às ${b.modified.slice(11)} · ${kb(b.size)}`)),
    btn('Restaurar', 'small', () => restoreFrom(`${dateBR(b.modified.slice(0, 10))} (${b.kind_label.toLowerCase()})`, () => api('POST', '/backups/restore', { name: b.name })), 'upload')))
    : [h('div', { class: 'muted', style: { padding: '8px 0' } }, 'Nenhum backup ainda. A primeira cópia automática é feita quando houver dados.')];

  const budgetRows = budgets.length ? budgets.map(b => h('div', { class: 'list-row' },
    h('div', { class: 'grow' }, h('b', null, b.category), h('small', null, 'limite por mês')),
    h('b', { class: 'num' }, fmt(b.amount)),
    iconBtn('edit', 'Editar', () => budgetForm(b)),
    iconBtn('trash', 'Excluir', () => remove(`/budgets/${b.id}`, `Excluir o limite de "${b.category}"?`), 'del')))
    : [h('div', { class: 'muted', style: { padding: '8px 0' } }, 'Nenhum limite definido. Defina quanto quer gastar por mês em cada categoria para receber avisos.')];

  const root = h('div');
  root.append(pageHead('Configurações'));
  root.append(h('div', { class: 'grid-eq' },
    h('div', { class: 'stack' },
      h('div', { class: 'card' },
        h('div', { class: 'section-title' }, 'Saldo e investimentos'),
        h('div', { class: 'form-grid' },
          ...(multiAccount() ? [] : [field('Saldo inicial em conta (R$)', balance, 'O quanto você tinha no começo do primeiro mês controlado.')]),
          field('Mês inicial do controle', month, 'Vazio = começa no primeiro mês com lançamentos. Meses anteriores não entram no saldo.'),
          field('CDI anual (% a.a.)', cdi, 'Usado na projeção da renda fixa.')),
        error, h('div', { style: { marginTop: '16px' } }, btn('Salvar', 'primary', save))),
      accountsCard(),
      h('div', { class: 'card' },
        h('div', { class: 'section-title' }, 'Orçamento por categoria', btn('Novo limite', 'small primary', () => budgetForm(), 'plus')),
        budgetRows)),
    h('div', { class: 'stack' },
    licenseInfo.enabled ? licenseCard(licenseInfo) : null,
    exportCard(),
    h('div', { class: 'card' },
      h('div', { class: 'section-title' }, 'Seus dados e backups'),
      h('p', { class: 'muted', style: { marginBottom: '12px' } }, 'Tudo fica guardado dentro do app, neste aparelho. Nada é enviado para a internet. O app faz uma cópia automática por dia (guarda as últimas 7) dentro do próprio app.'),
      h('div', { class: 'actions', style: { marginBottom: '14px' } },
        btn('Criar backup agora', 'primary small', async () => { try { await api('POST', '/backups'); toast('Backup criado'); refresh(); } catch (e) { toast(e.message, 'error'); } }, 'shield'),
        btn('Salvar cópia atual', 'small', exportBackup, 'download'),
        btn('Restaurar de um arquivo…', 'small', () => picker.click(), 'upload'), picker),
      h('div', { class: 'section-title', style: { fontSize: '13px', marginBottom: '4px' } }, 'Cópias guardadas'),
      backupRows,
      h('p', { class: 'muted', style: { marginTop: '12px', fontSize: '12px' } }, 'Restaurar substitui os dados atuais, mas antes o app guarda uma cópia de segurança do que existe agora ("Antes de restaurar"). As cópias guardadas aqui dentro protegem de enganos, mas não de perder o celular: use também "Salvar cópia atual" para guardá-la fora do aparelho.')))), resetCard());
  return root;
}


// ------------------------------------------------------------------ relatórios
const repState = { preset: '6', from: '', to: '', account: '' };

function repRange() {
  const now = ymNow();
  const p = repState.preset;
  if (p === 'custom') return [repState.from || ymAdd(now, -5), repState.to || now];
  if (p === 'year') return [`${now.slice(0, 4)}-01`, `${now.slice(0, 4)}-12`];
  if (p === 'lastyear') { const y = String(+now.slice(0, 4) - 1); return [`${y}-01`, `${y}-12`]; }
  return [ymAdd(now, -(+p - 1)), now];
}

async function pageReports() {
  const [from, to] = repRange();
  const acct = repState.account || 'all';
  const r = await api('GET', `/report/${from}/${to}/${acct}`);
  const root = h('div', { class: 'report' });
  const base = `/report/${from}/${to}/${acct}`;
  const getFile = async path => { try { await downloadFrom(path); } catch (e) { toast(e.message, 'error'); } };
  root.append(pageHead('Relatórios', `${ymLabel(from)} a ${ymLabel(to)}${r.account_name ? ' · ' + r.account_name : ''}`,
    btn('Excel', 'small', () => getFile(`${base}/xlsx`), 'download'),
    btn('CSV', 'small', () => getFile(`${base}/csv`), 'download'),
    btn('Imprimir / PDF', 'small', () => window.print(), 'printer')));

  const preset = h('select', { 'aria-label': 'Período', onchange: e => { repState.preset = e.target.value; if (repState.preset === 'custom') { [repState.from, repState.to] = [from, to]; } refresh(); } },
    [['3', 'Últimos 3 meses'], ['6', 'Últimos 6 meses'], ['12', 'Últimos 12 meses'], ['year', 'Este ano'], ['lastyear', 'Ano passado'], ['custom', 'Personalizado…']].map(([v, l]) => h('option', { value: v }, l)));
  preset.value = repState.preset;
  const mFrom = h('input', { type: 'month', value: from, 'aria-label': 'Mês inicial', onchange: e => { repState.from = e.target.value; repState.preset = 'custom'; if (!repState.to) repState.to = to; refresh(); } });
  const mTo = h('input', { type: 'month', value: to, 'aria-label': 'Mês final', onchange: e => { repState.to = e.target.value; repState.preset = 'custom'; if (!repState.from) repState.from = from; refresh(); } });
  const acctSel = multiAccount() ? h('select', { 'aria-label': 'Conta', onchange: e => { repState.account = e.target.value; refresh(); } },
    h('option', { value: '' }, 'Todas as contas'), accountsInfo.items.map(a => h('option', { value: a.id }, a.name))) : null;
  if (acctSel) acctSel.value = String(repState.account || '');
  root.append(h('div', { class: 'filters no-print' }, preset, repState.preset === 'custom' ? h('div', { class: 'rep-range' }, mFrom, h('span', null, 'até'), mTo) : null, acctSel));

  const prev = r.previous && r.previous.totals;
  const delta = (cur, old, goodWhenUp) => {
    if (!prev || old == null) return '';
    const d = cur - old;
    if (Math.abs(d) < 0.005) return 'igual ao período anterior';
    return `${d > 0 ? '▲' : '▼'} ${fmt(Math.abs(d))} vs período anterior`;
  };
  const t = r.totals;
  root.append(h('div', { class: 'kpis' },
    kpi('Entradas', fmt(t.entradas), { tone: 'pos', dot: '#22c55e', sub: delta(t.entradas, prev && prev.entradas) || `média ${fmt(r.average.entradas)}/mês` }),
    kpi('Saídas', fmt(t.saidas), { tone: 'neg', dot: '#ef4444', sub: delta(t.saidas, prev && prev.saidas) || `média ${fmt(r.average.saidas)}/mês` }),
    kpi('Aportes', fmt(t.aportes), { tone: 'inv', dot: '#8b5cf6', sub: 'investimentos' }),
    kpi('Resultado', signed(t.resultado), { tone: toneOf(t.resultado), sub: delta(t.resultado, prev && prev.resultado) || 'entradas − saídas − aportes' })));

  const bars = chartBox();
  root.append(h('div', { class: 'card', style: { marginBottom: '18px' } }, h('div', { class: 'section-title' }, 'Mês a mês', h('small', null, 'lançado no mês, recebido ou não')), bars,
    h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, ['Mês', 'Entradas', 'Saídas', 'Aportes', 'Resultado', 'Saldo final'].map((x, i) => h('th', { class: i ? 'r' : '' }, x)))),
      h('tbody', null, r.months.map(m => h('tr', null, h('td', null, ymShort(m.ym)), h('td', { class: 'r pos' }, fmt(m.entradas)), h('td', { class: 'r neg' }, fmt(m.saidas)),
        h('td', { class: 'r' }, fmt(m.aportes)), h('td', { class: 'r ' + toneOf(m.resultado) }, signed(m.resultado)), h('td', { class: 'r' }, m.saldo_final == null ? '—' : fmt(m.saldo_final)))))))));
  Charts.bar(bars, { labels: r.months.map(m => ymShort(m.ym)), height: 220,
    series: [{ name: 'Entradas', color: '#22c55e', values: r.months.map(m => m.entradas) }, { name: 'Saídas', color: '#ff3b2f', values: r.months.map(m => -m.saidas) }] });

  const maxCat = Math.max(1, ...r.categories.map(c => c.total));
  root.append(h('div', { class: 'grid-2' },
    h('div', { class: 'card' }, h('div', { class: 'section-title' }, 'Saídas por categoria', h('small', null, prev ? 'variação vs período anterior' : 'no período')),
      r.categories.length ? r.categories.map(c => h('div', { class: 'cat-row' },
        h('div', { class: 'cat-top' }, h('b', null, c.category), h('span', { class: 'num' }, fmt(c.total)), h('small', { class: 'muted' }, `${String(c.pct).replace('.', ',')}%`)),
        h('div', { class: 'progress' }, h('span', { style: { width: `${Math.max(2, c.total / maxCat * 100)}%` } })),
        h('div', { class: 'cat-sub muted' }, `média ${fmt(c.avg)}/mês`,
          c.delta != null && Math.abs(c.delta) >= 0.005 ? h('span', { class: c.delta > 0 ? 'neg' : 'pos' }, ` · ${c.delta > 0 ? '▲' : '▼'} ${fmt(Math.abs(c.delta))}`) : null)))
        : emptyState('Sem saídas no período', 'Escolha outro período.')),
    h('div', { class: 'stack' },
      h('div', { class: 'card' }, h('div', { class: 'section-title' }, 'Maiores gastos'),
        r.top.length ? r.top.map(s => h('div', { class: 'list-row' }, h('div', { class: 'grow' }, h('b', null, s.description), h('small', null, `${dateBR(s.date)} · ${s.category}${s.account && multiAccount() ? ' · ' + s.account : ''}`)),
          h('b', { class: 'num neg' }, fmt(s.amount)))) : emptyState('Nada aqui', 'Sem saídas no período.')),
      r.accounts.length ? h('div', { class: 'card' }, h('div', { class: 'section-title' }, 'Por conta'),
        r.accounts.map(a => h('div', { class: 'list-row' }, h('div', { class: 'grow' }, h('b', null, a.name), h('small', null, `entrou ${fmt(a.entradas)} · saiu ${fmt(a.saidas)}${a.aportes ? ' · aportes ' + fmt(a.aportes) : ''}`)),
          h('b', { class: 'num' }, a.saldo_final == null ? '—' : fmt(a.saldo_final))))) : null)));
  root.append(h('p', { class: 'muted print-only', style: { marginTop: '12px', fontSize: '12px' } }, `Gerado em ${dateBR(r.generated)} · MoneyControl`));
  return root;
}

// ------------------------------------------------------------------ conciliação bancária (OFX)
async function bankAct(path, body, okMsg) {
  try { const r = await api('POST', '/bank' + path, body); if (okMsg) toast(okMsg); refresh(); return r; }
  catch (e) { toast(e.message, 'error'); return null; }
}

function suspectList(list) {
  return h('div', { class: 'bank-sus' }, list.map(s => h('div', { class: 'sus-line' },
    h('b', null, s.description), ' · ', s.date ? dateBR(s.date) : '', s.amount != null ? ' · ' + fmt(s.amount) : '',
    h('small', null, s.reason))));
}

async function bankImportFlow(file) {
  if (!file) return;
  let data, pv;
  try {
    data = await fileToBase64(file);
    pv = await api('POST', '/bank/preview', { data });
  } catch (e) { toast(e.message, 'error'); return; }
  const isCard = pv.kind === 'cartao';
  modal({
    title: 'Importar extrato', build: close => {
      const cardSel = isCard ? h('select', { id: 'imp-card' }, pv.cards.map(c => h('option', { value: c.id }, c.name))) : null;
      const acctSel = !isCard && pv.accounts && pv.accounts.length > 1 ? h('select', { id: 'imp-acct' }, pv.accounts.map(a => h('option', { value: a.id }, a.name))) : null;
      if (acctSel) acctSel.value = String(pv.account_id);
      const go = h('button', { class: 'btn primary', type: 'button', disabled: pv.new === 0 || (isCard && !pv.cards.length), onclick: async () => {
        go.disabled = true;
        try {
          const r = await api('POST', '/bank/import', { data, filename: file.name, card_id: cardSel ? +cardSel.value : null, account_id: acctSel ? +acctSel.value : null });
          toast(`${r.new} linha(s) importada(s)` + (r.duplicated ? `, ${r.duplicated} já existiam` : ''));
          state.bankView = 'pendente';
          close(); refresh();
        } catch (e) { toast(e.message, 'error'); go.disabled = false; }
      } }, pv.new ? `Importar ${pv.new} linha(s)` : 'Nada novo para importar');
      return h('div', null,
        h('div', { class: 'modal-body' },
          h('div', { class: 'stmt-head' },
            metric('Conta', pv.account_label), metric('Período', pv.period_start ? `${dateBR(pv.period_start)} a ${dateBR(pv.period_end)}` : '—'),
            metric('Linhas novas', `${pv.new} de ${pv.total}`)),
          h('div', { class: 'stmt-head', style: { marginTop: '10px' } },
            metric('Entradas', fmt(pv.inflow), 'pos'), metric('Saídas', fmt(pv.outflow), 'neg'),
            pv.ledger_balance != null ? metric(`Saldo em ${dateBR(pv.ledger_date)}`, fmt(pv.ledger_balance)) : null),
          pv.duplicated ? h('div', { class: 'note', style: { marginTop: '12px' } }, `${pv.duplicated} linha(s) deste arquivo já foram importadas antes e serão ignoradas.`) : null,
          isCard ? h('div', { class: 'field', style: { marginTop: '12px' } }, h('label', { for: 'imp-card' }, 'Este extrato é de qual cartão?'),
            pv.cards.length ? cardSel : h('div', { class: 'form-error' }, 'Cadastre o cartão primeiro em "Fixas e parcelas" e importe de novo.')) : null,
          acctSel ? h('div', { class: 'field', style: { marginTop: '12px' } }, h('label', { for: 'imp-acct' }, 'Este extrato é de qual conta do app?'), acctSel,
            h('div', { class: 'hint' }, 'A conciliação compara com os lançamentos dessa conta. O app lembra a escolha para este mesmo banco.')) : null,
          h('div', { class: 'note', style: { marginTop: '12px' } }, 'Importar não lança nada sozinho: as linhas ficam como pendentes até você conciliar ou lançar.')),
        h('div', { class: 'modal-foot' }, btn('Cancelar', '', close), go));
    },
  });
}

async function bankCreateForm(t) {
  const cats = await categoryList();
  openForm({
    title: 'Lançar como novo', submitLabel: 'Lançar', datalists: { cats },
    intro: `${dateBR(t.date)} · ${t.amount < 0 ? 'Saída' : 'Entrada'} de ${fmt(Math.abs(t.amount))}`,
    initial: { description: t.memo.slice(0, 120), category: t.suggested_category || '' },
    fields: [{ name: 'description', label: 'Descrição', type: 'text' }, { name: 'category', label: 'Categoria', type: 'text', list: 'cats', hint: t.suggested_category ? 'Sugerida pelos seus lançamentos anteriores.' : '' }],
    onSubmit: async v => {
      const r = await api('POST', `/bank/txns/${t.id}/create`, v);
      if (r.duplicate_suspected) {
        const sure = await confirmDialog({ title: 'Possível duplicidade', confirmLabel: 'Lançar mesmo assim', danger: false,
          message: 'Isto parece já estar lançado no app. Prefira vincular ao lançamento existente. Se for outra despesa de fato, lance mesmo assim.' });
        if (!sure) throw new Error('Nada foi lançado. Use "Vincular…" para ligar ao lançamento que já existe.');
        await api('POST', `/bank/txns/${t.id}/create`, { ...v, force: true });
      }
      toast('Lançado'); refresh();
    },
  });
}

async function bankLinkModal(t) {
  let list;
  try { list = await api('GET', `/bank/txns/${t.id}/candidates`); } catch (e) { toast(e.message, 'error'); return; }
  modal({
    title: 'Vincular a um lançamento do app', wide: true, build: close => {
      const link = async (c, fix) => {
        const r = await bankAct(`/txns/${t.id}/reconcile`, { source: c.source, ref_id: c.ref_id, ym: c.ym, fix_amount: fix }, 'Conciliado');
        if (r) close();
      };
      const rowFor = c => h('div', { class: 'row' },
        h('div', { class: `ico ${c.kind === 'entrada' ? 'in' : 'out'}` }, icon(c.kind === 'entrada' ? 'down' : 'up')),
        h('div', { class: 'main-col' }, h('div', { class: 'title' }, c.description),
          h('div', { class: 'meta' }, dateBR(c.date), c.exact ? chip('mesmo valor', 'green') : chip(`diferença de ${fmt(Math.abs(c.diff))}`, 'amber'),
            c.days ? chip(`${c.days} dia(s) de diferença`) : chip('mesma data'))),
        h('div', { class: 'amount' }, fmt(c.amount)),
        c.exact ? btn('Vincular', 'small primary', () => link(c, false))
          : (c.source === 'lancamento' ? btn('Vincular e ajustar valor', 'small', () => link(c, true)) : btn('Vincular', 'small', () => link(c, false))));
      const listBox = h('div');
      const paint = () => {
        const q = search.value.trim().toLowerCase();
        const shown = list.filter(c => !q || c.description.toLowerCase().includes(q) || fmt(c.amount).includes(q) || String(c.amount).includes(q));
        listBox.replaceChildren(...(shown.length ? shown.map(rowFor)
          : [emptyState('Nenhum lançamento parecido', 'Não há lançamento do mesmo sentido num intervalo de 31 dias. Use "Lançar como novo".')]));
      };
      const search = h('input', { type: 'text', placeholder: 'Filtrar por nome ou valor…', 'aria-label': 'Filtrar lançamentos', oninput: paint,
        style: { width: '100%', margin: '0 0 8px', font: 'inherit', padding: '8px 11px', border: '1.5px solid var(--border)', borderRadius: '10px', background: 'var(--bg)', color: 'var(--text)' } });
      paint();
      return h('div', null, h('div', { class: 'modal-body' },
        h('div', { class: 'note', style: { marginBottom: '10px' } }, h('b', null, t.memo), ` · ${dateBR(t.date)} · ${fmt(Math.abs(t.amount))}`),
        list.length > 6 ? search : null, listBox), h('div', { class: 'modal-foot' }, btn('Fechar', '', close)));
    },
  });
}

async function bankGroupModal(txnIds) {
  let data;
  try { data = await api('POST', '/bank/group/candidates', { txn_ids: txnIds }); } catch (e) { toast(e.message, 'error'); return; }
  const out = data.txns[0].amount < 0;
  const chosen = new Set();
  modal({
    title: 'Conciliar em grupo', wide: true, build: close => {
      const key = c => `${c.source}|${c.ref_id}|${c.ym}`;
      const total = h('b', { class: 'num' }), diff = h('b', { class: 'num' });
      const go = h('button', { class: 'btn primary', type: 'button', disabled: true, onclick: async () => {
        go.disabled = true;
        const items = data.items.filter(c => chosen.has(key(c))).map(c => ({ source: c.source, ref_id: c.ref_id, ym: c.ym }));
        try {
          const r = await api('POST', '/bank/group/reconcile', { txn_ids: txnIds, items });
          toast(`${r.txns} linha(s) conciliada(s) com ${r.items} lançamento(s)`);
          state.bankSel.clear(); close(); refresh();
        } catch (e) { toast(e.message, 'error'); go.disabled = false; }
      } }, 'Conciliar');
      const update = () => {
        const sum = Math.round(data.items.filter(c => chosen.has(key(c))).reduce((a, c) => a + c.amount * 100, 0)) / 100;
        const d = Math.round((data.total - sum) * 100) / 100;
        total.textContent = fmt(sum);
        diff.textContent = d === 0 && chosen.size ? 'Confere ✓' : fmt(d);
        diff.className = 'num ' + (d === 0 && chosen.size ? 'pos' : 'neg');
        go.disabled = !(d === 0 && chosen.size);
      };
      const rowFor = c => {
        const cb = h('input', { type: 'checkbox', id: 'g-' + key(c).replace(/\W/g, '_'), 'aria-label': `Incluir ${c.description}`,
          onchange: e => { e.target.checked ? chosen.add(key(c)) : chosen.delete(key(c)); update(); } });
        cb.checked = chosen.has(key(c));
        return h('label', { class: 'row group-pick' }, cb,
          h('div', { class: 'main-col' }, h('div', { class: 'title' }, c.description),
            h('div', { class: 'meta' }, dateBR(c.date), c.source === 'fixa' ? chip('Fixa', 'blue') : c.source === 'parcela' ? chip('Parcela', 'amber') : c.source === 'fatura' ? chip('Fatura', 'blue') : null,
              c.days ? chip(`${c.days} dia(s) do extrato`) : chip('mesma data'))),
          h('div', { class: 'amount' }, fmt(c.amount)));
      };
      const listBox = h('div');
      const search = h('input', { type: 'text', placeholder: 'Filtrar por nome ou valor…', 'aria-label': 'Filtrar lançamentos', class: 'modal-search',
        oninput: () => paint() });
      const paint = () => {
        const q = search.value.trim().toLowerCase();
        const shown = data.items.filter(c => chosen.has(key(c)) || !q || c.description.toLowerCase().includes(q) || String(c.amount).includes(q) || fmt(c.amount).includes(q));
        listBox.replaceChildren(...(shown.length ? shown.map(rowFor)
          : [emptyState('Nenhum lançamento disponível', 'Não há lançamentos do mesmo sentido num intervalo de 31 dias.')]));
      };
      paint(); update();
      return h('div', null,
        h('div', { class: 'modal-body' },
          h('div', { class: 'note', style: { marginBottom: '10px' } },
            data.txns.map(t => h('div', null, h('b', null, t.memo), ` · ${dateBR(t.date)} · ${fmt(Math.abs(t.amount))}`)),
            data.txns.length > 1 ? h('div', { style: { marginTop: '4px' } }, h('b', null, `Total do extrato: ${fmt(data.total)}`)) : null),
          h('p', { class: 'muted', style: { marginBottom: '8px' } }, out
            ? 'Marque os lançamentos que este pagamento quitou. A soma precisa ser igual ao valor do extrato.'
            : 'Marque os lançamentos que este recebimento cobre. A soma precisa ser igual ao valor do extrato.'),
          data.items.length > 6 ? search : null, listBox),
        h('div', { class: 'modal-foot' },
          h('div', { class: 'sum-box' }, h('span', null, 'Selecionado ', total), h('span', null, 'Diferença ', diff)),
          btn('Cancelar', '', close), go));
    },
  });
}

function bankRow(t, sel, onSel) {
  const out = t.amount < 0;
  const g = t.suggestion;
  const hint = t.group_hints && t.group_hints[0];
  const realSuspects = t.suspects.filter(s => s.source !== null || !String(s.reason).startsWith('a soma bate'));
  const meta = [h('span', null, dateBR(t.date)), t.kind === 'cartao' ? chip('Cartão', 'blue') : null];
  const tools = [];
  let extra = null;
  const hintBox = () => {
    if (!hint) return null;
    const its = hint.items.map(i => h('div', { class: 'sus-line' }, h('b', null, i.description), ` · ${dateBR(i.date)} · ${fmt(i.amount)}`));
    if (hint.type === 'um_para_varios') {
      tools.push(btn(`Conciliar com os ${hint.items.length} lançamentos`, 'small primary', async () => {
        const r = await bankAct('/group/reconcile', { txn_ids: hint.txn_ids, items: hint.items.map(i => ({ source: i.source, ref_id: i.ref_id, ym: i.ym })) }, 'Conciliado');
        return r;
      }, 'check'));
      return h('div', { class: 'bank-hint' }, h('b', null, 'Parece a soma de'), ` ${hint.items.length} lançamentos já existentes:`, ...its);
    }
    const others = (hint.txns || []).filter(x => x.id !== t.id);
    tools.push(btn(`Conciliar as ${hint.txn_ids.length} linhas`, 'small primary', () =>
      bankAct('/group/reconcile', { txn_ids: hint.txn_ids, items: hint.items.map(i => ({ source: i.source, ref_id: i.ref_id, ym: i.ym })) }, 'Conciliado'), 'check'));
    return h('div', { class: 'bank-hint' }, h('b', null, 'Pagamento em partes:'), ' esta linha e ',
      ...others.map(o => h('span', null, h('b', null, o.memo), ` (${dateBR(o.date)}, ${fmt(Math.abs(o.amount))}) `)), 'somam o lançamento:', ...its);
  };
  if (t.status === 'pendente') {
    if (g) {
      extra = h('div', { class: 'bank-sug' }, `Parece ser: `, h('b', null, g.description), ` (${dateBR(g.date)}, ${fmt(g.amount)})`,
        g.ambiguous ? chip('há outra opção parecida: confira', 'amber') : null);
      tools.push(btn(g.ambiguous ? 'Conciliar com este' : 'Conciliar', g.ambiguous ? 'small' : 'small primary',
        () => bankAct(`/txns/${t.id}/reconcile`, { source: g.source, ref_id: g.ref_id, ym: g.ym }, 'Conciliado'), 'check'));
      tools.push(btn('Outro…', 'small', () => bankLinkModal(t)));
    } else if (hint || realSuspects.length) {
      extra = h('div', null, hintBox(),
        realSuspects.length ? h('div', { class: 'bank-warn' }, h('b', null, 'Possível duplicidade'), ' — já existe algo parecido no app:', suspectList(realSuspects)) : null);
      tools.push(btn('Vincular…', hint ? 'small' : 'small primary', () => bankLinkModal(t), 'link'));
      tools.push(btn('Vários…', 'small', () => bankGroupModal([t.id])));
      tools.push(btn('Lançar mesmo assim', 'small', () => bankCreateForm(t)));
    } else {
      tools.push(btn('Lançar como novo', 'small primary', () => bankCreateForm(t), 'plus'));
      tools.push(btn('Vincular…', 'small', () => bankLinkModal(t)));
      tools.push(btn('Vários…', 'small', () => bankGroupModal([t.id])));
    }
    tools.push(btn('Ignorar', 'small', () => bankAct(`/txns/${t.id}/ignore`, {}, 'Ignorada')));
  } else if (t.status === 'ignorada') {
    tools.push(btn('Restaurar', 'small', () => bankAct(`/txns/${t.id}/restore`, {}, 'Voltou para pendentes')));
  } else {
    meta.push(t.status === 'criada' ? chip('Lançada a partir do extrato', 'blue') : chip('Conciliada', 'green'));
    if (t.matched_items.length > 1) meta.push(chip(`Pagamento de ${t.matched_items.length} lançamentos`, 'purple'));
    if (t.group_txns > 1) meta.push(chip(`Em grupo com ${t.group_txns - 1} outra(s) linha(s)`, 'purple'));
    if (t.matched_items.length) extra = h('div', { class: 'bank-sug' }, 'Ligada a: ', ...t.matched_items.flatMap((d, n) => [n ? ' + ' : null, h('b', null, d.description), ` (${fmt(d.amount)})`]));
    tools.push(btn('Desfazer', 'small', async () => {
      const grp = t.group_size > 0 ? ' Ela faz parte de um grupo: todas as linhas e lançamentos ligados voltam ao estado anterior.' : '';
      const msg = (t.status === 'criada' ? 'Desfazer remove o lançamento que foi criado a partir desta linha. Continuar?' : 'Desfazer a conciliação desta linha?') + grp;
      if (await confirmDialog({ title: 'Desfazer', message: msg, confirmLabel: 'Desfazer' })) bankAct(`/txns/${t.id}/unreconcile`, {}, 'Desfeito');
    }));
  }
  const pick = t.status === 'pendente' && sel
    ? h('input', { type: 'checkbox', class: 'bank-sel', 'aria-label': `Selecionar ${t.memo}`, title: 'Selecionar para conciliar várias linhas com um lançamento',
      onchange: e => { e.target.checked ? sel.add(t.id) : sel.delete(t.id); onSel(); } }) : null;
  if (pick) pick.checked = sel.has(t.id);
  return h('div', { class: 'row bank-row' + (t.status === 'ignorada' ? ' dim' : '') },
    pick,
    h('div', { class: `ico ${out ? 'out' : 'in'}` }, icon(out ? 'up' : 'down')),
    h('div', { class: 'main-col' }, h('div', { class: 'title', title: t.memo }, t.memo), h('div', { class: 'meta' }, meta), extra),
    h('div', { class: `amount ${out ? 'neg' : 'pos'}` }, (out ? '− ' : '+ ') + fmt(Math.abs(t.amount))),
    h('div', { class: 'mini-actions' }, tools));
}

async function pageBank() {
  const view = state.bankView || 'pendente';
  const o = await api('GET', `/bank/list/${view}`);
  const c = o.counts;
  const picker = h('input', { type: 'file', hidden: true, 'aria-label': 'Escolher extrato OFX',
    onchange: e => { const f = e.target.files[0]; e.target.value = ''; bankImportFlow(f); } });
  const importBtn = btn('Importar extrato (OFX)', 'primary', () => picker.click(), 'upload');
  state.bankSel = state.bankSel || new Set();
  const sel = state.bankSel;
  for (const id of [...sel]) if (!o.rows.some(r => r.id === id && r.status === 'pendente')) sel.delete(id);
  const root = h('div');
  root.append(pageHead('Conciliação', 'Compare o extrato do banco com o que está lançado no app', importBtn, picker));

  const total = c.pendente + c.conciliada + c.criada + c.ignorada;
  if (!total && !o.imports.length) {
    root.append(h('div', { class: 'card' }, emptyState('Importe o extrato do seu banco',
      'No internet banking, baixe o extrato do período em formato OFX (a opção costuma estar em "Extrato" → "Exportar/Baixar" → OFX). O app compara cada linha com o que você já lançou e marca as saídas como pagas. Nada é lançado sem a sua confirmação, e linhas com suspeita de duplicidade nunca são confirmadas sozinhas.',
      btn('Importar extrato (OFX)', 'primary', () => picker.click(), 'upload'))));
    return root;
  }

  root.append(h('div', { class: 'kpis' },
    kpi('Pendentes', String(c.pendente), { sub: 'ainda sem decisão' }),
    kpi('Sugestões prontas', String(o.suggested), { tone: 'pos', sub: 'combinam com o app' }),
    kpi('Para conferir', String(o.to_review), { tone: o.to_review ? 'amber' : '', sub: 'dúvida ou possível duplicidade' }),
    kpi('Sem correspondência', String(o.unmatched), { sub: 'não estão lançadas' })));

  (o.balance_checks || []).forEach(b => {
    const ok = Math.abs(b.diff) < 0.005;
    root.append(h('div', { class: 'card' + (ok ? '' : ' alert-card'), style: { marginBottom: '16px' } },
      h('div', { class: 'section-title' }, 'Conferência de saldo', o.accounts && o.accounts.length > 1 && b.account_name ? h('small', null, b.account_name) : null),
      h('div', { class: 'stmt-head' }, metric(`Saldo do banco em ${dateBR(b.date)}`, fmt(b.bank)), metric('Saldo no app na mesma data', fmt(b.app)),
        metric('Diferença', ok ? 'Confere ✓' : fmt(b.diff), ok ? 'pos' : 'neg')),
      ok ? null : h('div', { class: 'muted', style: { marginTop: '8px' } }, 'Diferença normalmente indica lançamentos que faltam no app (ou lançados a mais). Concilie as linhas pendentes para achá-los.')));
  });

  const tabs = [['pendente', `Pendentes (${c.pendente})`], ['conciliada', `Conciliadas (${c.conciliada + c.criada})`], ['ignorada', `Ignoradas (${c.ignorada})`]];
  root.append(h('div', { class: 'tabs', role: 'tablist' }, tabs.map(([id, label]) =>
    h('button', { class: 'tab' + (view === id ? ' active' : ''), type: 'button', role: 'tab', onclick: () => { state.bankView = id; state.bankSel.clear(); refresh(); } }, label))));

  if (view === 'pendente') {
    const bulk = h('div', { class: 'actions', style: { marginBottom: '12px' } });
    if (o.suggested) bulk.append(btn(o.suggested === 1 ? 'Conciliar a sugestão' : `Conciliar as ${o.suggested} sugestões`, 'success small', async () => {
      if (!(await confirmDialog({ title: 'Conciliar sugestões', confirmLabel: 'Conciliar', danger: false,
        message: `Vincular ${o.suggested} linha(s) do extrato aos lançamentos que combinam (mesmo valor, data próxima). Saídas serão marcadas como pagas. As ${o.to_review} que têm dúvida ou possível duplicidade ficam de fora para você decidir.` }))) return;
      const r = await bankAct('/reconcile-all', {});
      if (r) toast(`${r.reconciled} conciliada(s)` + (r.skipped ? `, ${r.skipped} deixada(s) para você conferir` : ''));
    }, 'check'));
    if (o.unmatched) bulk.append(btn(o.unmatched === 1 ? 'Lançar a linha sem correspondência' : `Lançar as ${o.unmatched} sem correspondência`, 'small', async () => {
      if (!(await confirmDialog({ title: 'Lançar como novos', confirmLabel: 'Lançar', danger: false,
        message: `Criar ${o.unmatched} lançamento(s) novo(s) a partir das linhas que não existem no app. Linhas com suspeita de duplicidade NÃO serão lançadas (${o.to_review} ficam para você decidir uma a uma).` }))) return;
      const r = await bankAct('/create-all', {});
      if (r) toast(`${r.created} lançada(s)` + (r.skipped ? `, ${r.skipped} pulada(s) por suspeita de duplicidade` : ''));
    }, 'plus'));
    if (bulk.children.length) root.append(bulk);
  }

  const selBar = h('div', { class: 'sel-bar', hidden: true });
  const paintSel = () => {
    const rows = o.rows.filter(r => sel.has(r.id));
    selBar.hidden = !rows.length;
    if (!rows.length) return;
    const total = rows.reduce((a, r) => a + Math.abs(r.amount), 0);
    selBar.replaceChildren(h('span', null, h('b', null, `${rows.length} linha(s) selecionada(s)`), ` · total ${fmt(total)}`),
      btn('Conciliar com lançamentos…', 'small primary', () => bankGroupModal(rows.map(r => r.id)), 'link'),
      btn('Limpar seleção', 'small', () => { sel.clear(); refresh(); }));
  };
  if (view === 'pendente') root.append(selBar);
  paintSel();
  root.append(h('div', { class: 'card' }, o.rows.length ? o.rows.map(r => bankRow(r, view === 'pendente' ? sel : null, paintSel))
    : emptyState(view === 'pendente' ? 'Tudo em dia' : 'Nada por aqui', view === 'pendente' ? 'Não há linhas pendentes no extrato.' : 'Nenhuma linha nesta lista.')));

  if (o.imports.length) root.append(h('div', { class: 'card', style: { marginTop: '16px' } },
    h('div', { class: 'section-title' }, 'Extratos importados'),
    o.imports.map(i => h('div', { class: 'list-row' },
      h('div', { class: 'grow' }, h('b', null, i.account_label), h('small', null, `${i.filename} · ${i.period_start ? dateBR(i.period_start) + ' a ' + dateBR(i.period_end) : ''} · ${i.total} linha(s), ${i.pending} pendente(s)`)),
      iconBtn('trash', 'Excluir importação', () => remove(`/bank/imports/${i.id}`, `Excluir a importação "${i.filename}"? As linhas do extrato somem e as conciliações feitas com ela são desfeitas (os seus lançamentos continuam).`), 'del')))));
  return root;
}


// ------------------------------------------------------------------ licença e primeiro uso
let licenseInfo = { enabled: false, blocked: false, state: 'livre' };
let wizardChecked = false;

function licenseForm() {
  openForm({
    title: 'Ativar licença',
    intro: 'Cole a chave que você recebeu (começa com MC1.). Ela vale só para este aplicativo.',
    fields: [{ name: 'key', label: 'Chave de licença', type: 'text', placeholder: 'MC1.…' }],
    submitLabel: 'Ativar',
    onSubmit: async v => { licenseInfo = await api('POST', '/license/activate', { key: v.key || '' }); toast('Licença ativada. Obrigado!'); },
    onClose: () => refresh(),
  });
}

function licenseScreen(l) {
  const title = l.state === 'vencida' ? `Sua licença venceu em ${dateBR(l.ate)}` : 'Seu período de teste terminou';
  const text = l.state === 'vencida' ? 'Para continuar usando, ative uma licença renovada.' : `Os ${l.trial_days} dias de teste acabaram. Para continuar usando, ative a licença que você recebeu.`;
  return h('div', { class: 'card lic-screen' },
    h('div', { class: 'section-title' }, title),
    h('p', { class: 'muted', style: { marginBottom: '14px' } }, `${text} Seus dados continuam guardados e nada foi apagado.`),
    btn('Ativar licença', 'primary', licenseForm, 'key'),
    h('p', { class: 'muted', style: { marginTop: '16px', fontSize: '13px' } }, 'Ainda não tem a chave? Peça a quem forneceu o MoneyControl. ',
      h('a', { href: '#', onclick: e => { e.preventDefault(); exportBackup(); } }, 'Baixar uma cópia dos meus dados')));
}

function licenseSummary(l) {
  if (l.state === 'teste') return `Período de teste: restam ${l.days_left} dia${l.days_left === 1 ? '' : 's'}.`;
  if (l.state === 'licenciado') return `Licenciado${l.cliente ? ' para ' + l.cliente : ''} · ${l.ate ? 'válida até ' + dateBR(l.ate) : 'sem data de vencimento'}.`;
  return 'Licença necessária.';
}

function licenseCard(l) {
  return h('div', { class: 'card' },
    h('div', { class: 'section-title' }, 'Licença'),
    h('p', { class: 'muted', style: { marginBottom: '12px' } }, licenseSummary(l)),
    btn(l.state === 'licenciado' ? 'Trocar chave' : 'Ativar licença', 'small' + (l.state === 'teste' ? ' primary' : ''), licenseForm, 'key'));
}

function licenseNotice(l) {
  const soon = l.state === 'teste' ? l.days_left <= 10 : l.state === 'licenciado' && l.days_left != null && l.days_left <= 15;
  if (!soon) return null;
  const msg = l.state === 'teste' ? `Período de teste: restam ${l.days_left} dia${l.days_left === 1 ? '' : 's'}.` : `Sua licença vence em ${l.days_left} dia${l.days_left === 1 ? '' : 's'} (${dateBR(l.ate)}).`;
  return h('div', { class: 'lic-note' }, h('span', null, msg), btn('Ativar', 'small', licenseForm));
}

function firstRunWizard(settings) {
  let finished = false;
  openForm({
    title: 'Bem-vindo ao MoneyControl',
    intro: 'Vamos deixar o básico pronto em 1 minuto. Tudo pode ser mudado depois em Configurações.',
    initial: { account_name: 'Conta principal', account_kind: 'corrente', opening_balance: 0, opening_month: ymNow(), cdi_annual: settings.cdi_annual },
    fields: [
      { name: 'account_name', label: 'Nome da sua conta principal', type: 'text', placeholder: 'Ex.: Itaú, Nubank, Carteira' },
      { name: 'account_kind', label: 'Tipo', type: 'select', options: ACCOUNT_KINDS.map(([value, label]) => ({ value, label })), half: true },
      { name: 'opening_balance', label: 'Saldo hoje (R$)', type: 'money', half: true, placeholder: '0,00', hint: 'Quanto há nela agora.' },
      { name: 'opening_month', label: 'Começar a controlar em', type: 'month', half: true, hint: 'Normalmente o mês atual.' },
      { name: 'cdi_annual', label: 'CDI anual (% a.a.)', type: 'number', half: true, hint: 'Só para projetar renda fixa. Pode deixar.' },
    ],
    submitLabel: 'Começar',
    onSubmit: async v => { await api('POST', '/wizard', { ...v, opening_balance: v.opening_balance ?? 0 }); finished = true; toast('Tudo pronto! Lance sua primeira entrada ou saída.'); },
    onClose: () => { if (!finished) api('POST', '/wizard/skip', {}).catch(() => {}); refresh(); },
  });
}

// ------------------------------------------------------------------ roteamento e inicialização
const PAGES = { visao: pageOverview, mes: pageMonth, conc: pageBank, fixas: pageRecurring, invest: pageInvest, relat: pageReports, config: pageSettings };
let renderToken = 0;

function renderNav(active) {
  $('#nav').replaceChildren(...NAV.map(n => h('a', { class: 'nav-item' + (n.id === active ? ' active' : ''), href: `#/${n.id}`, 'aria-label': n.label }, icon(n.icon), h('span', { class: 'long' }, n.label), h('span', { class: 'short' }, n.short))));
}

async function route() {
  const id = location.hash.replace('#/', '').split('/')[0];
  const page = PAGES[id] ? id : 'visao';
  renderNav(page);
  const token = ++renderToken;
  const v = $('#view');
  if (!v.firstChild) v.append(h('div', { class: 'loading' }, 'Carregando…'));
  try {
    licenseInfo = await api('GET', '/license');
    if (licenseInfo.blocked) { if (token === renderToken) v.replaceChildren(licenseScreen(licenseInfo)); return; }
    await loadAccounts();
    if (state.account && !accountsInfo.items.some(a => String(a.id) === String(state.account))) state.account = '';
    const node = await PAGES[page]();
    if (token !== renderToken) return;
    const note = licenseNotice(licenseInfo);
    if (note) node.prepend(note);
    v.replaceChildren(node);
    if (!wizardChecked) {
      wizardChecked = true;
      try { if ((await api('GET', '/wizard')).needed) firstRunWizard(await api('GET', '/settings')); } catch { /* sem assistente */ }
    }
  } catch (e) {
    if (token !== renderToken) return;
    if (e.license) { v.replaceChildren(licenseScreen(e.license)); return; }
    v.replaceChildren(h('div', { class: 'card' }, emptyState('Algo deu errado', e.message, btn('Tentar de novo', 'primary', refresh))));
  }
}
function refresh() { return route(); }

function setupTheme() {
  const b = $('#theme-btn');
  const paint = () => { b.textContent = document.documentElement.dataset.theme === 'dark' ? '☀ Tema claro' : '☾ Tema escuro'; };
  b.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('theme', next); } catch { /* sem armazenamento */ }
    paint();
  });
  paint();
}

// ------------------------------------------------------------------ inicialização
// O Python (motor do app) carrega num worker antes da primeira tela; depois disso tudo roda no aparelho.
async function boot() {
  const view = $('#view');
  view.replaceChildren(h('div', { class: 'boot' },
    h('div', { class: 'spinner', 'aria-hidden': 'true' }),
    h('b', null, 'MoneyControl'),
    h('p', { class: 'muted', id: 'boot-status' }, 'Iniciando…')));
  Backend.onStatus(text => { const el = $('#boot-status'); if (el) el.textContent = text; });
  try {
    await Backend.start();
  } catch (e) {
    view.replaceChildren(h('div', { class: 'card' }, emptyState('Não foi possível iniciar o app', e.message,
      btn('Tentar de novo', 'primary', () => location.reload()))));
    return;
  }
  // pede ao navegador para não apagar os dados do app por falta de uso (o iPhone pode ignorar)
  try { if (navigator.storage && navigator.storage.persist) navigator.storage.persist(); } catch { /* ignorar */ }
  registerOffline();
  route();
}

function registerOffline() {
  // guarda o app no aparelho para abrir sem internet (só funciona em HTTPS ou localhost)
  if (!('serviceWorker' in navigator) || window.MC_DEV_BACKEND) return;
  navigator.serviceWorker.register('sw.js').catch(() => {});
}

$('#fab').addEventListener('click', () => quickForm('saida'));
window.addEventListener('hashchange', () => { $('.main').scrollTop = 0; route(); });
setupTheme();
boot();
