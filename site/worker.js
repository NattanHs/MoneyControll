'use strict';
/* Web Worker do MoneyControl: roda o Python (Pyodide) e guarda os dados no IndexedDB do navegador.

   Os arquivos de dados (o banco e os backups automáticos) vivem na pasta /data do Python, que fica só
   na memória. A cada chamada o que mudou é copiado para o IndexedDB, e ao abrir o app tudo volta de lá.
   A resposta só é enviada à tela depois de gravado, então "Lançamento salvo" quer dizer salvo de verdade. */

const DATA_DIR = '/data';
const DB_PATH = '/data/financas.db';
const IDB_NAME = 'moneycontrol';
const IDB_STORE = 'files';

let py = null;
let dispatch = null;
let idb = null;
const persisted = new Map();   // caminho -> assinatura (data + tamanho) do que já está no IndexedDB
let chain = Promise.resolve(); // uma chamada de cada vez

const say = text => postMessage({ type: 'status', text });

// ---------------------------------------------------------------- IndexedDB
function openIdb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(IDB_NAME, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(IDB_STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error || new Error('Não consegui abrir o armazenamento do navegador.'));
    req.onblocked = () => reject(new Error('O armazenamento do navegador está bloqueado por outra aba do app. Feche as outras abas e tente de novo.'));
  });
}

function readAllSaved() {
  return new Promise((resolve, reject) => {
    const out = [];
    const tx = idb.transaction(IDB_STORE, 'readonly');
    const req = tx.objectStore(IDB_STORE).openCursor();
    req.onsuccess = () => {
      const cur = req.result;
      if (cur) { out.push([cur.key, cur.value]); cur.continue(); } else resolve(out);
    };
    req.onerror = () => reject(req.error);
  });
}

function writeSaved(puts, dels) {
  return new Promise((resolve, reject) => {
    const tx = idb.transaction(IDB_STORE, 'readwrite');
    const store = tx.objectStore(IDB_STORE);
    for (const [path, bytes] of puts) store.put(bytes, path);
    for (const path of dels) store.delete(path);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error || new Error('Não consegui gravar os dados no aparelho.'));
    tx.onabort = () => reject(tx.error || new Error('A gravação dos dados foi cancelada (o aparelho pode estar sem espaço).'));
  });
}

// ---------------------------------------------------------------- pasta de dados <-> IndexedDB
function walk(dir, out) {
  for (const name of py.FS.readdir(dir)) {
    if (name === '.' || name === '..') continue;
    const path = dir + '/' + name;
    const st = py.FS.stat(path);
    if (py.FS.isDir(st.mode)) walk(path, out);
    else out.set(path, `${st.mtime instanceof Date ? st.mtime.getTime() : st.mtime}:${st.size}`);
  }
}

async function persist(force) {
  const now = new Map();
  walk(DATA_DIR, now);
  const puts = [];
  const dels = [];
  for (const [path, sig] of now) {
    if (force && path === DB_PATH) puts.push([path, py.FS.readFile(path), sig]);
    else if (persisted.get(path) !== sig) puts.push([path, py.FS.readFile(path), sig]);
  }
  for (const path of persisted.keys()) if (!now.has(path)) dels.push(path);
  if (!puts.length && !dels.length) return;
  await writeSaved(puts.map(([p, b]) => [p, b]), dels);
  for (const [path, , sig] of puts) persisted.set(path, sig);
  for (const path of dels) persisted.delete(path);
}

function restoreSaved(saved) {
  for (const [path, bytes] of saved) {
    const dir = path.slice(0, path.lastIndexOf('/'));
    if (dir) py.FS.mkdirTree(dir);
    py.FS.writeFile(path, bytes);
  }
  const now = new Map();
  walk(DATA_DIR, now);
  for (const [path, sig] of now) persisted.set(path, sig);
}

// ---------------------------------------------------------------- início
async function init() {
  say('Carregando o motor do app… (na primeira vez pode levar um minuto)');
  importScripts('pyodide/pyodide.js');
  py = await loadPyodide({ indexURL: new URL('pyodide/', self.location.href).href });

  say('Preparando o app…');
  await py.loadPackage('sqlite3');

  const list = await (await fetch('py/files.json')).json();
  py.FS.mkdirTree('/app/financas');
  await Promise.all(list.map(async file => {
    const res = await fetch('py/' + file);
    if (!res.ok) throw new Error('Arquivo do app não encontrado: ' + file);
    py.FS.writeFile('/app/' + file, new Uint8Array(await res.arrayBuffer()));
  }));

  say('Abrindo seus dados…');
  py.FS.mkdirTree(DATA_DIR);
  idb = await openIdb();
  restoreSaved(await readAllSaved());

  py.runPython(`import sys\nsys.path.insert(0, '/app')\nfrom financas import api\napi.start('${DB_PATH}')`);
  dispatch = py.globals.get('api').dispatch;
  await persist(true);
  postMessage({ type: 'ready' });
}

// ---------------------------------------------------------------- chamadas da tela
async function handle(m) {
  try {
    const res = JSON.parse(dispatch(m.method, m.url, m.body, m.today));
    let file = null;
    if (res.file) {
      const bytes = py.FS.readFile(res.file.path).slice();
      py.FS.unlink(res.file.path);
      file = { name: res.file.name, ctype: res.file.ctype, bytes };
    }
    await persist(m.method !== 'GET');
    postMessage({ id: m.id, result: { status: res.status, body: res.body, file } }, file ? [file.bytes.buffer] : []);
  } catch (e) {
    postMessage({ id: m.id, error: String((e && e.message) || e) });
  }
}

self.onmessage = ev => {
  const m = ev.data;
  if (m && m.type === 'req') chain = chain.then(() => handle(m));
};

init().catch(e => postMessage({ type: 'fatal', error: 'Não foi possível iniciar o app: ' + String((e && e.message) || e) }));
