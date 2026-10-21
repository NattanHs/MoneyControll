'use strict';
/* Sincronização com o Google Drive.

   O banco inteiro vai para um arquivo (financas.db) na "pasta de dados do app" do SEU Drive: uma área escondida,
   que só este app enxerga e que não aparece na lista de arquivos do Drive. O app nunca pede acesso aos seus outros arquivos
   (escopo drive.appdata). Nada passa por servidor nenhum além do Google.

   REGRAS ABSOLUTAS (as travas de verdade ficam no Python: ver syncmeta.py e backup.adopt):
   1. Quem decide qual lado é mais novo é a LINHAGEM das versões (guardada dentro do banco), nunca a data ou o relógio:
      uma versão só substitui outra se descende dela.
   2. Nunca se envia ao Drive um arquivo mais antigo que o do Drive, nem se baixa um arquivo mais antigo que os dados daqui.
   3. Nunca se apagam alterações ainda não registradas: se os dois lados mudaram, é conflito e quem decide é você.
   4. Antes de qualquer substituição há cópia: aqui ("Antes de restaurar") e no Drive ("financas-anterior-DATA.db", as 5 últimas)
      quando o Drive vai ser substituído por uma restauração, um reset ou uma escolha sua num conflito.
   5. Se os dados mudarem enquanto a sincronização está em andamento, ela recomeça em vez de sobrescrever.

   Decisão a cada sincronização, comparando a versão do Drive (B) com a daqui (A):
     B igual a A, sem alterações pendentes   -> nada a fazer
     B é ancestral de A (A está à frente)    -> envia
     A é ancestral de B (B está à frente)    -> baixa (só se não houver alterações pendentes aqui)
     nenhum descende do outro                -> conflito: o app pergunta qual manter

   O login usa o fluxo por redirecionamento do Google (funciona no ícone do iPhone, sem pop-up). O Google entrega um
   token que vale ~1 hora; passado isso, basta um toque em "Conectar" (o Google já conhece o app, então é rápido). */
const Sync = (() => {
  const KEY = 'mc_sync';
  const NONCE_KEY = 'mc_sync_nonce';
  const SCOPE = 'https://www.googleapis.com/auth/drive.appdata';
  const FILE_NAME = 'financas.db';
  const PREV_PREFIX = 'financas-anterior-';
  const PREV_KEEP = 5;
  const API = 'https://www.googleapis.com/drive/v3';
  const UPLOAD = 'https://www.googleapis.com/upload/drive/v3';
  const DEBOUNCE_MS = 4000;
  const MIN_GAP_MS = 20000;   // ao voltar para o app, não sincroniza de novo se acabou de sincronizar
  const MAX_ATTEMPTS = 3;     // recomeços quando os dados mudam no meio da sincronização
  const CHANGED = 'mudaram durante a sincronização';   // texto do erro que o Python devolve (backup.CHANGED_MSG)

  let listeners = [];
  let busy = null;
  let running = false;
  let timer = null;
  let lastRunAt = 0;
  let conflictPending = false;
  let redirectResult = null;

  // ------------------------------------------------------------ estado guardado no aparelho
  function load() {
    try { return JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch { return {}; }
  }
  function save(patch) {
    const next = { ...load(), ...patch };
    try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* sem armazenamento */ }
    return next;
  }
  function emit(ev) { for (const fn of listeners) { try { fn(ev); } catch { /* ouvinte com defeito não derruba o app */ } } }

  const clientId = () => (load().clientId || window.MC_GOOGLE_CLIENT_ID || '').trim();
  const tokenValid = s => !!(s.token && s.expires && Date.now() < s.expires);

  /** Endereço de volta do login: o do app, sem "#" e sem "index.html". Precisa ser igual ao cadastrado no Google Cloud. */
  function redirectUri() {
    return location.origin + location.pathname.replace(/index\.html$/, '');
  }

  function status() {
    const s = load();
    return {
      configured: !!clientId(),
      connected: tokenValid(s),
      everConnected: !!(s.baseMd5 || s.lastSync),
      dirty: !!s.dirty,
      lastSync: s.lastSync || '',
      lastError: s.lastError || '',
      busy: running,
      redirectUri: redirectUri(),
    };
  }

  // ------------------------------------------------------------ login (fluxo por redirecionamento)
  function connect() {
    const id = clientId();
    if (!id) throw new Error('Informe primeiro o ID do cliente Google.');
    const nonce = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2)) + '';
    try { localStorage.setItem(NONCE_KEY, nonce); } catch { /* sem armazenamento: o login não vai poder ser conferido */ }
    const q = new URLSearchParams({
      client_id: id, redirect_uri: redirectUri(), response_type: 'token', scope: SCOPE,
      state: nonce, include_granted_scopes: 'true',
    });
    location.href = 'https://accounts.google.com/o/oauth2/v2/auth?' + q.toString();
  }

  /** Roda ao abrir o app: se o Google acabou de nos devolver um token (ou um erro) no endereço, guarda e limpa o endereço. */
  function handleRedirect() {
    const hash = location.hash || '';
    // o Google pode mandar "#state=...&access_token=..." (state primeiro), então o parâmetro pode estar em qualquer posição
    if (!/^#(?!\/)(.*&)?(access_token|error)=/.test(hash)) return null;
    const p = new URLSearchParams(hash.slice(1));
    let expected = '';
    try { expected = localStorage.getItem(NONCE_KEY) || ''; localStorage.removeItem(NONCE_KEY); } catch { /* ignorar */ }
    let result;
    if (p.get('error')) {
      result = { ok: false, message: p.get('error') === 'access_denied' ? 'O acesso ao Google Drive não foi autorizado.' : 'O Google recusou o login: ' + p.get('error') };
    } else if (!expected || p.get('state') !== expected) {
      result = { ok: false, message: 'Não consegui confirmar o login do Google (tente conectar de novo).' };
    } else if (!p.get('access_token')) {
      result = { ok: false, message: 'O Google não enviou a autorização.' };
    } else {
      const secs = parseInt(p.get('expires_in') || '3600', 10) || 3600;
      save({ token: p.get('access_token'), expires: Date.now() + secs * 1000 - 60000, lastError: '' });
      result = { ok: true };
    }
    // devolve o app ao endereço normal (tela de configurações) sem recarregar
    try { history.replaceState(null, '', redirectUri() + location.search + '#/config'); } catch { location.hash = '#/config'; }
    if (!result.ok) save({ lastError: result.message });
    redirectResult = result;
    return result;
  }

  async function disconnect() {
    const s = load();
    if (s.token) { try { await fetch('https://oauth2.googleapis.com/revoke?token=' + encodeURIComponent(s.token), { method: 'POST' }); } catch { /* sem internet: só esquece aqui */ } }
    save({ token: '', expires: 0, baseMd5: '', baseHead: '', fileId: '', dirty: false, lastSync: '', lastError: '' });
    conflictPending = false;
    emit({ type: 'changed' });
  }

  function saveClientId(id) {
    save({ clientId: String(id || '').trim() });
    emit({ type: 'changed' });
  }

  // ------------------------------------------------------------ Google Drive (API v3, pasta de dados do app)
  class AuthError extends Error {}

  async function drive(url, opts = {}) {
    const s = load();
    if (!tokenValid(s)) throw new AuthError('Sessão do Google expirada.');
    let res;
    try {
      res = await fetch(url, { ...opts, headers: { ...(opts.headers || {}), Authorization: 'Bearer ' + s.token } });
    } catch {
      throw new Error('Sem conexão com o Google Drive.');
    }
    if (res.status === 401) { save({ token: '', expires: 0, lastError: 'O Google recusou a autorização (erro 401). Conecte de novo.' }); throw new AuthError('Sessão do Google expirada.'); }
    if (!res.ok) {
      let detail = '';
      try { detail = (await res.json()).error.message; } catch { /* sem detalhe */ }
      if (res.status === 403 && /disabled|not been used|accessNotConfigured/i.test(detail)) throw new Error('A API do Google Drive não está ativada no seu projeto do Google Cloud (veja o LEIA-ME, Parte 4).');
      throw new Error(`Google Drive respondeu ${res.status}${detail ? ': ' + detail : ''}`);
    }
    return res;
  }

  async function findFiles(name) {
    const q = encodeURIComponent(`name = '${name}' and trashed = false`);
    const res = await drive(`${API}/files?spaces=appDataFolder&q=${q}&fields=files(id,md5Checksum,modifiedTime,size)&orderBy=modifiedTime desc&pageSize=10`);
    return (await res.json()).files || [];
  }

  async function download(id) {
    const res = await drive(`${API}/files/${id}?alt=media`);
    return new Uint8Array(await res.arrayBuffer());
  }

  async function upload(bytes, existingId) {
    if (existingId) {
      const res = await drive(`${UPLOAD}/files/${existingId}?uploadType=media&fields=id,md5Checksum`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/octet-stream' }, body: bytes });
      return res.json();
    }
    const boundary = 'mc' + Math.random().toString(16).slice(2);
    const head = `--${boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n${JSON.stringify({ name: FILE_NAME, parents: ['appDataFolder'] })}\r\n--${boundary}\r\nContent-Type: application/octet-stream\r\n\r\n`;
    const body = new Blob([head, bytes, `\r\n--${boundary}--`]);
    const res = await drive(`${UPLOAD}/files?uploadType=multipart&fields=id,md5Checksum`, {
      method: 'POST', headers: { 'Content-Type': 'multipart/related; boundary=' + boundary }, body });
    return res.json();
  }

  /** Guarda a versão que está no Drive como "financas-anterior-DATA.db" (as 5 últimas) antes de ela ser substituída. */
  async function keepPrevious(id) {
    const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\..*/, '').replace('T', '-');
    await drive(`${API}/files/${id}/copy?fields=id`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: `${PREV_PREFIX}${stamp}.db`, parents: ['appDataFolder'] }) });
    const q = encodeURIComponent(`name contains '${PREV_PREFIX}' and trashed = false`);
    const res = await drive(`${API}/files?spaces=appDataFolder&q=${q}&fields=files(id,name)&orderBy=name desc&pageSize=50`);
    const copies = ((await res.json()).files || []).sort((a, b) => (a.name < b.name ? 1 : -1));
    for (const old of copies.slice(PREV_KEEP)) await drive(`${API}/files/${old.id}`, { method: 'DELETE' });
  }

  // ------------------------------------------------------------ dados daqui (as travas ficam no Python)
  async function py(method, path, body) {
    const res = await Backend.request(method, '/api' + path, body);
    if (res.status < 200 || res.status >= 300) throw new Error((res.body && res.body.error) || `Erro ${res.status}`);
    return res;
  }

  const localState = async () => (await py('GET', '/sync/state')).body;
  const stampLocal = async (absorb, force) => (await py('POST', '/sync/stamp', { absorb: absorb || [], force: !!force })).body;

  async function localSnapshot() {
    const res = await py('GET', '/backup');
    if (!res.file) throw new Error('Não consegui preparar os dados para enviar.');
    return res.file.bytes;
  }

  /** Compara o arquivo do Drive com os dados daqui. Devolve { relation, file:{history,head,...}, local:{...} }. */
  async function compareWithLocal(bytes) {
    return (await py('POST', '/backups/inspect-upload', { data: bytesToBase64(bytes) })).body;
  }

  /** Aplica o arquivo do Drive aqui. O Python recusa se ele não for mais novo, ou se houver alterações pendentes/mudanças no meio. */
  async function adoptRemote(bytes, remote, remoteHead, expectHead, force) {
    try {
      await py('POST', '/sync/adopt', { data: bytesToBase64(bytes), expect_head: expectHead, force: !!force });
    } catch (e) {
      if (String(e.message).includes(CHANGED)) return { state: 'retry' };
      throw e;
    }
    const st = await localState();
    save({ baseMd5: remote.md5Checksum || '', baseHead: remoteHead || st.head || '', fileId: remote.id, lastSync: new Date().toISOString(), lastError: '', dirty: !!st.dirty });
    return { state: 'baixado' };
  }

  /** Envia os dados daqui. Antes: confere de novo que o Drive não mudou desde a decisão (se mudou, recomeça). */
  async function commitUpload(remote, st) {
    if (remote) {
      const again = (await findFiles(FILE_NAME))[0];
      if (!again || again.md5Checksum !== remote.md5Checksum) return { state: 'retry' };
    }
    const bytes = await localSnapshot();
    const up = await upload(bytes, remote && remote.id);
    const now = await localState();
    save({ baseMd5: up.md5Checksum || '', baseHead: st.head, fileId: up.id, lastSync: new Date().toISOString(), lastError: '', dirty: !!now.dirty });
    return { state: 'enviado' };
  }

  /** Envio normal (os dados daqui estão à frente do Drive, ou o Drive está vazio). */
  async function pushLocal(remote, local) {
    if (remote && local.rewind) await keepPrevious(remote.id);   // restauração/reset feitos aqui: o Drive guarda uma cópia antes
    const st = await stampLocal([], false);
    return commitUpload(remote, st);
  }

  // ------------------------------------------------------------ uma tentativa de sincronização
  async function attempt() {
    const s = load();
    if (!tokenValid(s)) return { state: 'login' };
    const local = await localState();
    const remote = (await findFiles(FILE_NAME))[0] || null;

    if (!remote) {                                                // primeiro envio (um banco vazio nunca é enviado: ele não é "o mais novo" de nada)
      if (!local.has_data) return { state: 'em-dia' };
      return pushLocal(null, local);
    }

    // o Drive está exatamente como na última sincronização: só pode haver novidade aqui
    if (s.baseMd5 && remote.md5Checksum === s.baseMd5) {
      if (local.dirty || local.head !== (s.baseHead || null)) return pushLocal(remote, local);
      save({ lastSync: new Date().toISOString(), lastError: '', dirty: false });
      return { state: 'em-dia' };
    }

    // o Drive mudou (ou é a primeira vez aqui): baixa e compara as linhagens
    const bytes = await download(remote.id);
    const cmp = await compareWithLocal(bytes);
    const remoteHead = cmp.file.head;
    const first = !s.baseMd5;
    switch (cmp.relation) {
      case 'same':
        if (local.dirty) return pushLocal(remote, local);
        save({ baseMd5: remote.md5Checksum || '', baseHead: remoteHead, fileId: remote.id, lastSync: new Date().toISOString(), lastError: '', dirty: false });
        return { state: 'em-dia' };
      case 'file-older':                                          // o Drive é ancestral dos dados daqui: aqui está à frente
        return pushLocal(remote, local);
      case 'file-newer':                                          // o Drive está à frente
        if (local.dirty) return { state: 'conflito', remote, first };
        return adoptRemote(bytes, remote, remoteHead, local.head, false);
      default:                                                    // linhagens diferentes ou arquivo sem linhagem
        if (!local.has_data) return adoptRemote(bytes, remote, remoteHead, local.head, false);
        return { state: 'conflito', remote, first };
    }
  }

  function finish(extra) {
    lastRunAt = Date.now();
    return { ...extra, at: new Date().toISOString() };
  }

  async function withRetries(fn) {
    let r = { state: 'retry' };
    for (let i = 0; i < MAX_ATTEMPTS && r.state === 'retry'; i++) r = await fn();
    if (r.state === 'retry') return { state: 'erro', message: 'Os dados mudaram várias vezes durante a sincronização. Tente de novo em instantes.' };
    return r;
  }

  function guarded(fn) {
    if (busy) return busy;
    busy = (async () => {
      running = true;
      emit({ type: 'busy' });
      try {
        return finish(await withRetries(fn));
      } catch (e) {
        if (e instanceof AuthError) return finish({ state: 'login' });
        save({ lastError: e.message });
        return finish({ state: 'erro', message: e.message });
      } finally {
        busy = null;
        running = false;
      }
    })().then(r => { emit({ type: 'result', result: r }); return r; });
    return busy;
  }

  /** Uma sincronização. Devolve { state }:  em-dia | enviado | baixado | conflito | login | erro (com message). */
  const run = () => guarded(attempt);

  /** Resolve um conflito. keep = 'aqui' (o Drive passa a ter os dados deste aparelho) ou 'drive' (este aparelho recebe os do Drive). */
  async function resolveConflict(keep) {
    if (busy) await busy;
    const result = await guarded(async () => {
      const remote = (await findFiles(FILE_NAME))[0] || null;
      const local = await localState();
      if (!remote) {
        if (keep === 'drive') throw new Error('O arquivo do Drive não está mais lá.');
        return pushLocal(null, local);
      }
      const bytes = await download(remote.id);
      const cmp = await compareWithLocal(bytes);
      if (keep === 'drive') {
        // escolha da pessoa: os dados daqui ficam guardados em "Antes de restaurar"
        return adoptRemote(bytes, remote, cmp.file.head, local.head, true);
      }
      // "manter os daqui": o Drive guarda a versão que vai ser substituída, e a nova versão descende dela
      await keepPrevious(remote.id);
      const st = await stampLocal(cmp.file.history, true);
      return commitUpload(remote, st);
    });
    conflictPending = false;
    return result;
  }

  // ------------------------------------------------------------ gatilhos automáticos
  /** A tela chama isto depois de qualquer gravação que mudou dados aqui. */
  function markDirty() {
    save({ dirty: true });   // só uma dica para a tela; a verdade fica no banco (sync_meta)
    if (tokenValid(load()) && navigator.onLine !== false) {
      clearTimeout(timer);
      timer = setTimeout(() => { if (!conflictPending) run(); }, DEBOUNCE_MS);
    }
  }

  /** Sincroniza sozinho se estiver conectado (ao abrir o app e ao voltar para ele). */
  function auto({ force } = {}) {
    if (!clientId() || !tokenValid(load())) return null;
    if (conflictPending || busy) return busy;
    if (!force && Date.now() - lastRunAt < MIN_GAP_MS) return null;
    if (navigator.onLine === false) return null;
    return run();
  }

  function setConflictPending(v) { conflictPending = !!v; }

  return {
    status, connect, disconnect, saveClientId, handleRedirect, run, resolveConflict, markDirty, auto, setConflictPending,
    onEvent(fn) { listeners.push(fn); },
    get redirectResult() { const r = redirectResult; redirectResult = null; return r; },
  };
})();

// precisa rodar antes de a tela ler o endereço (o Google devolve o token em "#access_token=...")
Sync.handleRedirect();
