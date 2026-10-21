/* Pyodide de mentira, só para testar o worker.js aqui (onde não dá para baixar o Pyodide de verdade).
   Imita só o que o worker usa: FS em memória, loadPackage, runPython e globals.get('api').dispatch.
   O Python "de verdade" roda no servidor de teste; cada chamada devolve também os arquivos de dados,
   que são espelhados na FS de mentira (é daí que o worker copia para o IndexedDB). */
self.loadPyodide = async function () {
  const files = new Map();
  const dirs = new Set(['/']);
  const norm = p => p.replace(/\/+$/, '') || '/';
  const parent = p => norm(p.slice(0, p.lastIndexOf('/')) || '/');
  const FS = {
    mkdirTree(p) { let cur = ''; for (const part of norm(p).split('/').filter(Boolean)) { cur += '/' + part; dirs.add(cur); } },
    writeFile(p, data) { if (!dirs.has(parent(p))) throw new Error('ENOENT ' + p); files.set(p, { data: new Uint8Array(data), mtime: new Date(Date.now() + files.size * 0) }); },
    readFile(p) { const f = files.get(p); if (!f) throw new Error('ENOENT ' + p); return f.data.slice(); },
    readdir(d) {
      d = norm(d); if (!dirs.has(d)) throw new Error('ENOENT ' + d);
      const out = ['.', '..'];
      for (const x of dirs) if (x !== d && parent(x) === d) out.push(x.slice(d === '/' ? 1 : d.length + 1));
      for (const x of files.keys()) if (parent(x) === d) out.push(x.slice(d === '/' ? 1 : d.length + 1));
      return out;
    },
    stat(p) {
      if (dirs.has(norm(p))) return { mode: 0o40755, size: 0, mtime: new Date() };
      const f = files.get(p); if (!f) throw new Error('ENOENT ' + p);
      return { mode: 0o100644, size: f.data.length, mtime: f.mtime };
    },
    isDir(mode) { return (mode & 0o170000) === 0o040000; },
    unlink(p) { if (!files.delete(p)) throw new Error('ENOENT ' + p); },
  };
  const post = (url, obj) => {
    const x = new XMLHttpRequest();
    x.open('POST', url, false);
    x.setRequestHeader('Content-Type', 'application/json');
    x.send(JSON.stringify(obj));
    if (x.status !== 200) throw new Error('servidor de teste: ' + x.status);
    return JSON.parse(x.responseText);
  };
  const b64 = bytes => { let s = ''; for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000)); return btoa(s); };
  const unb64 = t => Uint8Array.from(atob(t), c => c.charCodeAt(0));
  return {
    FS,
    async loadPackage() {},
    runPython(code) {
      if (!code.includes('api.start')) return;
      // início: o que o worker recuperou do IndexedDB vai para o "Python" do servidor
      const pushed = {};
      for (const [p] of files) if (p.startsWith('/data/')) pushed[p] = b64(files.get(p).data);
      post('/__fake/restore', { files: pushed });
      mirror(post('/__fake/dispatch', { method: 'GET', url: '/api/license', body: null, today: '2026-10-21' }));
    },
    globals: {
      get() {
        return {
          dispatch(method, url, body, today) {
            const r = post('/__fake/dispatch', { method, url, body, today });
            mirror(r);
            return r.raw;
          },
        };
      },
    },
  };
  function mirror(r) {
    FS.mkdirTree('/data');
    for (const [p, t] of Object.entries(r.files)) {
      const bytes = unb64(t);
      const old = files.get(p);
      const same = old && old.data.length === bytes.length && old.data.every((v, i) => v === bytes[i]);
      if (!same) { FS.mkdirTree(parent(p)); FS.writeFile(p, bytes); }
    }
    for (const p of [...files.keys()]) if (p.startsWith('/data/') && !(p in r.files)) files.delete(p);
    if (r.out) { FS.mkdirTree(parent(r.out.path)); FS.writeFile(r.out.path, unb64(r.out.b64)); }
  }
};
