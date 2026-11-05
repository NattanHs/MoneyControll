# Versão do escritório — login por usuário e senha

Este pacote adiciona **login/senha** ao MoneyControl de celular (a versão que roda no navegador e guarda
os dados no próprio aparelho), para você distribuir aos clientes. Também **esconde a configuração do Google
Drive** (complicada para leigos) — os clientes usam o **backup manual** que o app já tem.

> **Importante (honestidade técnica):** como o app roda no navegador e o repositório é público, isto trava o
> **compartilhamento casual** (o cliente não sabe que não está "compartilhando os próprios dados"), mas não é
> à prova de alguém com conhecimento técnico. É uma trava de dissuasão, não um cadeado.

## O que vai em cada lugar (aplique na cópia do escritório)

Copie estes arquivos para dentro do repositório novo do escritório (a cópia do seu repositório pessoal):

| Arquivo | O que é |
|---|---|
| `site/py/financas/acessos.py` | **novo** — confere usuário e senha dentro do aparelho |
| `site/py/financas/acessos_contas.py` | **novo** — onde ficam os clientes (começa vazio) |
| `site/py/financas/routes.py` | **substituir** — liga as rotas de login |
| `site/pages.js` | **substituir** — tela de login, botão Sair, esconde o Drive |
| `site/sync-config.js` | **substituir** — liga o "esconder Drive" (`MC_HIDE_SYNC`) |
| `ferramentas/cadastrar-cliente.py` | **novo** — você cadastra os clientes com ele |

Com `acessos_contas.py` vazio, o app fica **aberto** (sem pedir login). Ele passa a exigir login assim que
você cadastrar o primeiro cliente.

## Como cadastrar um cliente

Na sua máquina, dentro da pasta do repositório do escritório:

```
python ferramentas/cadastrar-cliente.py --usuario joao --nome "João Silva"
```

- Ele gera uma **senha forte**, mostra na tela (anote e envie ao cliente) e grava o **hash** em
  `acessos_contas.py`. A senha em si nunca é guardada.
- Para definir validade: `--ate 2027-12-31`. Para escolher a senha: `--senha minhasenha`.
- Listar: `--listar`. Remover (corta o acesso): `--remover joao`.

Depois de cadastrar/remover: **faça commit e republique** o site (o GitHub Pages atualiza sozinho). O cliente
entra com o usuário e a senha; o acesso fica guardado no aparelho dele (não precisa logar toda vez).

## Backup (explique ao cliente)

Os dados ficam **no aparelho**. Oriente o cliente a usar, de vez em quando, **"Salvar uma cópia agora"**
(na Visão geral ou em Configurações → Seus dados e backups) e guardar o arquivo no iCloud/e-mail. É o seguro
dele — e evita que você receba reclamação de perda de dados se o navegador limpar o app.

## Publicar

Mesmo processo do seu repositório pessoal (o workflow do GitHub ou `node scripts/montar-site.mjs`). O novo
módulo `acessos_contas.py` é incluído automaticamente na publicação.

## Voltar atrás / religar o Drive

- Para o app ficar **aberto** de novo: deixe `acessos_contas.py` com `CONTAS = {}`.
- Para **religar o Google Drive**: em `sync-config.js`, apague a linha `MC_HIDE_SYNC` e cole o ID do Google.
