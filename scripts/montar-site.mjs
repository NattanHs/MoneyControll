// Monta a pasta "dist" que vai para o ar:
//   site/  +  py/files.json  +  pyodide/ (baixado do CDN oficial)  +  precache.json  +  versão no sw.js
// Uso:  node scripts/montar-site.mjs            (usado pelo GitHub Actions)
//       node scripts/montar-site.mjs --sem-pyodide   (só para conferir a montagem sem internet)
import { createHash } from 'node:crypto';
import { cp, mkdir, readdir, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { dirname, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SITE = join(ROOT, 'site');
const DIST = join(ROOT, 'dist');
const PYODIDE_VERSION = process.env.PYODIDE_VERSION || '0.27.7';
const CDN = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const SEM_PYODIDE = process.argv.includes('--sem-pyodide');
// núcleo do Pyodide + o módulo sqlite3 (o resto do Python que o app usa já vem no python_stdlib.zip)
const CORE = ['pyodide.js', 'pyodide.asm.js', 'pyodide.asm.wasm', 'python_stdlib.zip', 'pyodide-lock.json'];
const PACKAGES = ['sqlite3'];

async function download(name, dest) {
  let last;
  for (let i = 1; i <= 4; i++) {
    try {
      const res = await fetch(CDN + name);
      if (!res.ok) throw new Error(`${name}: HTTP ${res.status}`);
      const buf = Buffer.from(await res.arrayBuffer());
      if (!buf.length) throw new Error(`${name}: arquivo vazio`);
      await writeFile(join(dest, name), buf);
      console.log(`  ok ${name} (${(buf.length / 1048576).toFixed(1)} MB)`);
      return;
    } catch (e) { last = e; await new Promise(r => setTimeout(r, 1500 * i)); }
  }
  throw last;
}

async function listFiles(dir) {
  const out = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...await listFiles(full));
    else out.push(full);
  }
  return out;
}

const toUrl = p => relative(DIST, p).split(sep).join('/');

await rm(DIST, { recursive: true, force: true });
await mkdir(DIST, { recursive: true });
await cp(SITE, DIST, { recursive: true });
await rm(join(DIST, 'py', 'financas', '__pycache__'), { recursive: true, force: true });

// lista dos arquivos Python que o worker copia para dentro do Pyodide
const py = (await listFiles(join(DIST, 'py'))).map(toUrl).filter(f => f.endsWith('.py')).map(f => f.slice('py/'.length)).sort();
await writeFile(join(DIST, 'py', 'files.json'), JSON.stringify(py));
console.log(`Python: ${py.length} arquivos`);

if (!SEM_PYODIDE) {
  const dest = join(DIST, 'pyodide');
  await mkdir(dest, { recursive: true });
  console.log(`Baixando o Pyodide ${PYODIDE_VERSION}…`);
  for (const f of CORE) await download(f, dest);
  const lock = JSON.parse(await readFile(join(dest, 'pyodide-lock.json'), 'utf8'));
  const wanted = new Set();
  const visit = name => {
    const pkg = lock.packages[name];
    if (!pkg) throw new Error(`Pacote ${name} não existe nesta versão do Pyodide.`);
    if (wanted.has(name)) return;
    wanted.add(name);
    (pkg.depends || []).forEach(visit);
  };
  PACKAGES.forEach(visit);
  for (const name of wanted) await download(lock.packages[name].file_name, dest);
  // o lock completo lista centenas de pacotes que não vamos guardar; o Pyodide só abre os que pedimos
}

// versão do app = resumo de tudo que vai no ar (muda a cada publicação com alteração)
const files = (await listFiles(DIST)).map(toUrl).filter(f => f !== 'sw.js' && f !== 'precache.json').sort();
const hash = createHash('sha256');
for (const f of files) { hash.update(f); hash.update(await readFile(join(DIST, f))); }
const build = hash.digest('hex').slice(0, 12);

const swPath = join(DIST, 'sw.js');
const sw = await readFile(swPath, 'utf8');
if (!sw.includes("const BUILD = 'dev';")) throw new Error('sw.js: marcador de versão não encontrado.');
await writeFile(swPath, sw.replace("const BUILD = 'dev';", `const BUILD = '${build}';`));

await writeFile(join(DIST, 'precache.json'), JSON.stringify({ build, files: ['./', ...files] }));
await writeFile(join(DIST, '.nojekyll'), '');
let total = 0;
for (const f of files) total += (await stat(join(DIST, f))).size;
console.log(`Pronto: ${files.length} arquivos, ${(total / 1048576).toFixed(1)} MB, versão ${build}`);
