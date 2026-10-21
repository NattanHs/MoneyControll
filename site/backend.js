'use strict';
/* Ponte entre a interface e o Python.
   O Python (a mesma lógica do MoneyControl de computador) roda num Web Worker, dentro do próprio aparelho,
   e guarda o banco de dados no IndexedDB do navegador. Nada sai do celular.
   Em testes, window.MC_DEV_BACKEND aponta para um servidor de teste que faz o mesmo papel do worker. */
const REQUEST_TIMEOUT_MS = 120000;
const Backend = (() => {
  let worker = null;
  let nextId = 1;
  const pending = new Map();
  let readyPromise = null;
  let statusListener = () => {};
  let lastStatus = '';

  function setStatus(text) { lastStatus = text; statusListener(text); }

  function b64ToBytes(b64) {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  // ----- modo de teste: um servidor comum no lugar do worker
  async function devRequest(method, path, body) {
    const r = await fetch(window.MC_DEV_BACKEND, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ method, path, body: body === undefined ? null : JSON.stringify(body), today: todayISO() }),
    });
    const res = await r.json();
    if (res.file && res.file.b64) res.file = { name: res.file.name, ctype: res.file.ctype, bytes: b64ToBytes(res.file.b64) };
    return res;
  }

  // ----- modo normal: worker com Pyodide
  function start() {
    if (readyPromise) return readyPromise;
    if (window.MC_DEV_BACKEND) { readyPromise = Promise.resolve(); return readyPromise; }
    readyPromise = new Promise((resolve, reject) => {
      try { worker = new Worker('worker.js'); } catch (e) { reject(e); return; }
      worker.onmessage = ev => {
        const m = ev.data;
        if (m.type === 'status') setStatus(m.text);
        else if (m.type === 'ready') resolve();
        else if (m.type === 'fatal') { const err = new Error(m.error); err.fatal = true; reject(err); }
        else if (m.id && pending.has(m.id)) {
          const { res, rej } = pending.get(m.id);
          pending.delete(m.id);
          if (m.error) rej(new Error(m.error)); else res(m.result);
        }
      };
      worker.onerror = ev => {
        const err = new Error('Falha ao iniciar o app: ' + (ev.message || 'erro desconhecido'));
        err.fatal = true;
        reject(err);
      };
    });
    return readyPromise;
  }

  async function request(method, path, body) {
    await start();
    if (window.MC_DEV_BACKEND) return devRequest(method, path, body);
    const id = nextId++;
    return new Promise((res, rej) => {
      // se o worker travar (o iPhone pode suspendê-lo), a tela avisa em vez de ficar esperando para sempre
      const timer = setTimeout(() => {
        pending.delete(id);
        rej(new Error('O app demorou demais para responder. Feche e abra o MoneyControl de novo (seus dados estão guardados).'));
      }, REQUEST_TIMEOUT_MS);
      pending.set(id, { res: v => { clearTimeout(timer); res(v); }, rej: e => { clearTimeout(timer); rej(e); } });
      // corpo vazio vai como texto vazio (nunca null: o null do JavaScript pode virar um valor estranho no Python)
      worker.postMessage({ id, type: 'req', method, url: path, body: body === undefined ? '' : JSON.stringify(body), today: todayISO() });
    });
  }

  return {
    start,
    request,
    onStatus(fn) { statusListener = fn; if (lastStatus) fn(lastStatus); },
  };
})();
