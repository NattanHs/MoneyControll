# MoneyControl no iPhone (sem loja de aplicativos)

Este é o mesmo MoneyControl do computador, empacotado para rodar **dentro do iPhone**. Não precisa do PC ligado, não precisa de
Tailscale e não passa pela App Store.

**Como funciona:** o app fica publicado num endereço da internet (grátis, no GitHub) que serve só de "instalador". Você abre esse
endereço uma vez no Safari e adiciona à Tela de Início. Dali em diante o app roda no próprio aparelho, até sem internet. O motor de
cálculo é o mesmo Python do app de computador (carregado no navegador pelo Pyodide), e **seus dados ficam só no iPhone**: o endereço
nunca recebe nada seu.

---

## Parte 1 — Publicar o app (uma vez, uns 10 minutos, no computador)

1. Crie uma conta grátis em **github.com** (se ainda não tiver).
2. Clique em **+** (canto superior direito) → **New repository**. Nome: `moneycontrol`. Marque **Public** e clique em **Create repository**.
   O repositório precisa ser público para o GitHub Pages ser grátis. Só o *código do app* fica público; nenhum dado seu vai para lá.
   (Se preferir tudo privado, dá para usar o Cloudflare Pages ou o Netlify no lugar; me peça que eu adapto.)
3. Na página do repositório, clique em **uploading an existing file** e **arraste para lá o conteúdo desta pasta**
   (as pastas `site`, `scripts`, `tests`, `.github` e os arquivos soltos). Clique em **Commit changes**.
   - Pastas que começam com ponto, como `.github`, às vezes não vão no arrastar. Se depois não aparecer a pasta `.github` no
     repositório: **Add file → Create new file**, escreva o nome `.github/workflows/publicar.yml`, cole o conteúdo do arquivo
     `publicar.yml.txt` desta pasta e clique em **Commit changes**.
4. Vá em **Settings → Pages**. Em **Source**, escolha **GitHub Actions**.
5. Abra a aba **Actions**. O trabalho "Publicar o app" roda sozinho (2 a 4 minutos). Quando ficar verde, o endereço do app é
   `https://SEU-USUARIO.github.io/moneycontrol/`.
   - Se ele falhou porque você ligou o Pages *depois* do envio, clique no trabalho com erro e em **Re-run all jobs**.
   - Se falhar por outro motivo, clique no trabalho, copie a mensagem vermelha e me mande.

## Parte 2 — Instalar no iPhone

1. No iPhone, abra o endereço **no Safari** (precisa ser o Safari).
2. Espere a tela "Carregando o motor do app…". Na primeira vez baixa uns 10 a 20 MB (use Wi-Fi). Quando abrir o assistente de
   primeiro uso, o app já está pronto.
3. Toque em **Compartilhar** (quadrado com seta) → **Adicionar à Tela de Início** → **Adicionar**.
4. **Use sempre pelo ícone novo da Tela de Início.** O iPhone guarda os dados do ícone separados dos dados do Safari: o que você
   lançar no ícone não aparece no Safari, e vice-versa. Faça tudo, inclusive a Parte 3, dentro do ícone.

## Parte 3 — Trazer os dados que você já tem no PC

1. No MoneyControl do computador: **Configurações → Baixar cópia atual**. Sai um arquivo `financas-backup-AAAA-MM-DD.db`.
2. Mande o arquivo para o iPhone (e-mail para você mesmo, iCloud Drive, Google Drive, WhatsApp…) e salve no app **Arquivos**.
3. Abra o app pelo ícone → **Configurações → Restaurar de um arquivo…** → escolha o arquivo → **Restaurar**.

Depois disso o iPhone e o computador são **dois bancos separados** (não sincronizam). Escolha um como principal. Se quiser voltar ao
computador, é o caminho inverso: **Salvar cópia atual** no iPhone e **Restaurar de um arquivo** no PC.

## Uso e cópias de segurança

- O botão vermelho **+** faz o lançamento rápido, como na versão web do celular.
- **Seus dados só existem neste aparelho.** Se o iPhone for perdido, quebrar, ou o iOS limpar os dados do app, eles se perdem.
  Por isso o app avisa quando faz mais de 7 dias que você não salvou uma cópia. Em **Configurações → Salvar cópia atual** abre o menu
  Compartilhar: escolha **Salvar em Arquivos** (iCloud Drive) ou envie por e-mail para você mesmo.
- O app também guarda cópias automáticas dentro dele (uma por dia, as últimas 7). Elas protegem de enganos (apagar sem querer),
  mas **não** de perder o aparelho.
- Não use "Limpar histórico e dados de sites" no Safari sem antes salvar uma cópia, e não remova o ícone sem salvar.
- Relatórios em Excel/CSV usam o mesmo menu de compartilhar.

## Atualizar o app no futuro

Se eu te enviar uma versão nova, é só subir os arquivos no GitHub (**Add file → Upload files** sobre o repositório). Ele publica
sozinho de novo e o iPhone baixa a versão nova na próxima vez que abrir com internet (feche o app e abra duas vezes). Os dados não são mexidos.

## Se algo der errado

- **"Não foi possível iniciar o app"** (com uma mensagem): feche o app (deslize para cima) e abra de novo, com internet. Se continuar,
  me mande a mensagem exata.
- **O app abriu vazio depois de eu ter lançado coisas:** provavelmente você abriu pelo Safari em vez do ícone (ou o contrário).
  Os dados estão no outro lugar. Restaure uma cópia salva dentro de onde você quer continuar.
- **A conta do GitHub pede algo que eu não entendi:** me mande uma captura de tela.

## O que mudou em relação à versão de computador

- Tudo roda no aparelho. Não existem mais PIN, rede local, Tailscale, licença nem instalador.
- A "cópia fora do computador" virou **Salvar cópia atual** (arquivo que você guarda onde quiser).
- Os cálculos, a conciliação de extratos OFX, os cartões, os investimentos e os relatórios são exatamente os mesmos (o mesmo código Python
  e os mesmos testes).

---

## Para quem for mexer no código

```
site/            o que vai para o ar: interface (HTML/CSS/JS), worker.js (Pyodide + IndexedDB), sw.js (offline)
site/py/financas/  o Python do app (api.py + routes.py são a camada nova; o resto é o código original)
scripts/montar-site.mjs   monta a pasta publicada: baixa o Pyodide, gera a lista de arquivos e a versão
tests/           testes de cálculo (python -m unittest discover -s tests) e testes de interface (e2e_*.py, precisam de Playwright)
.github/workflows/publicar.yml   roda os testes de cálculo, monta e publica no GitHub Pages
```

A versão do Pyodide fica em `scripts/montar-site.mjs` (variável `PYODIDE_VERSION`, hoje 0.27.7).
