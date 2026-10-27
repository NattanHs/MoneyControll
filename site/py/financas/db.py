"""Banco de dados SQLite local."""
import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- lançamentos enviados pelo celular com a internet instável: o mesmo envio nunca grava duas vezes
CREATE TABLE IF NOT EXISTS client_refs (
    ref        TEXT PRIMARY KEY,
    row_id     INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE COLLATE NOCASE,
    kind            TEXT NOT NULL DEFAULT 'corrente' CHECK (kind IN ('corrente', 'poupanca', 'dinheiro', 'outra')),
    opening_balance REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transfers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    date         TEXT NOT NULL,
    from_account INTEGER NOT NULL,
    to_account   INTEGER NOT NULL,
    amount       REAL NOT NULL CHECK (amount > 0),
    description  TEXT NOT NULL DEFAULT '',
    CHECK (from_account != to_account)
);

CREATE TABLE IF NOT EXISTS sync_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('entrada', 'saida')),
    description TEXT NOT NULL,
    category    TEXT NOT NULL DEFAULT '',
    amount      REAL NOT NULL CHECK (amount > 0),
    card_id     INTEGER
);

CREATE TABLE IF NOT EXISTS fixed_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL CHECK (kind IN ('entrada', 'saida')),
    description TEXT NOT NULL,
    category    TEXT NOT NULL DEFAULT '',
    amount      REAL NOT NULL CHECK (amount > 0),
    day         INTEGER NOT NULL CHECK (day BETWEEN 1 AND 31),
    start_month TEXT NOT NULL,
    end_month   TEXT
);

CREATE TABLE IF NOT EXISTS installments (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    description        TEXT NOT NULL,
    category           TEXT NOT NULL DEFAULT '',
    installment_amount REAL NOT NULL CHECK (installment_amount > 0),
    first_date         TEXT NOT NULL,
    n_installments     INTEGER NOT NULL CHECK (n_installments >= 1),
    card_id            INTEGER
);

CREATE TABLE IF NOT EXISTS assets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT NOT NULL UNIQUE,
    kind          TEXT NOT NULL,
    current_price REAL
);

CREATE TABLE IF NOT EXISTS asset_ops (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id     INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    date         TEXT NOT NULL,
    op           TEXT NOT NULL CHECK (op IN ('compra', 'venda')),
    quantity     REAL NOT NULL CHECK (quantity > 0),
    price        REAL NOT NULL CHECK (price >= 0),
    fees         REAL NOT NULL DEFAULT 0,
    affects_cash INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS dividends (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id     INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    date         TEXT NOT NULL,
    amount       REAL NOT NULL CHECK (amount > 0),
    tipo         TEXT NOT NULL DEFAULT 'Dividendo',
    affects_cash INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS fixed_income (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL,
    pct_cdi REAL NOT NULL DEFAULT 100,
    tax     TEXT NOT NULL DEFAULT 'regressivo'
);

CREATE TABLE IF NOT EXISTS fixed_income_moves (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fi_id        INTEGER NOT NULL REFERENCES fixed_income(id) ON DELETE CASCADE,
    date         TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('aporte', 'resgate', 'rendimento')),
    amount       REAL NOT NULL CHECK (amount > 0),
    affects_cash INTEGER NOT NULL DEFAULT 1
);

-- reajustes: a partir de from_month o item passa a valer amount (meses anteriores não mudam)
CREATE TABLE IF NOT EXISTS amount_changes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ref_type   TEXT NOT NULL CHECK (ref_type IN ('fixed', 'installment')),
    ref_id     INTEGER NOT NULL,
    from_month TEXT NOT NULL,
    amount     REAL NOT NULL CHECK (amount > 0),
    UNIQUE (ref_type, ref_id, from_month)
);

-- cartões de crédito: compras caem na fatura que vence no mês calculado pelo fechamento
CREATE TABLE IF NOT EXISTS credit_cards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    closing_day INTEGER NOT NULL CHECK (closing_day BETWEEN 1 AND 31),
    due_day     INTEGER NOT NULL CHECK (due_day BETWEEN 1 AND 31)
);

-- marca manual de pago / não pago (sem marca: pago quando a data já chegou)
CREATE TABLE IF NOT EXISTS payment_marks (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL CHECK (source IN ('lancamento', 'fixa', 'parcela', 'fatura')),
    ref_id INTEGER NOT NULL,
    ym     TEXT NOT NULL,
    paid   INTEGER NOT NULL,
    UNIQUE (source, ref_id, ym)
);

-- lançamento que quitou antes do prazo um débito (ou recebimento) já previsto: o previsto não conta de novo
CREATE TABLE IF NOT EXISTS occurrence_links (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL CHECK (source IN ('lancamento', 'fixa', 'parcela')),
    ref_id INTEGER NOT NULL,
    ym     TEXT NOT NULL,
    tx_id  INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    UNIQUE (source, ref_id, ym),
    UNIQUE (tx_id)
);

CREATE TABLE IF NOT EXISTS budgets (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL UNIQUE COLLATE NOCASE,
    amount   REAL NOT NULL CHECK (amount > 0)
);

-- extratos bancários importados (OFX) e conciliação
CREATE TABLE IF NOT EXISTS bank_imports (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    filename       TEXT NOT NULL,
    imported_at    TEXT NOT NULL,
    account_key    TEXT NOT NULL,
    account_label  TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('conta', 'cartao')),
    card_id        INTEGER,
    period_start   TEXT,
    period_end     TEXT,
    ledger_balance REAL,
    ledger_date    TEXT
);

CREATE TABLE IF NOT EXISTS bank_txns (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id      INTEGER NOT NULL REFERENCES bank_imports(id) ON DELETE CASCADE,
    account_key    TEXT NOT NULL,
    fitid          TEXT NOT NULL,
    date           TEXT NOT NULL,
    amount         REAL NOT NULL,
    memo           TEXT NOT NULL,
    trntype        TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'pendente' CHECK (status IN ('pendente', 'conciliada', 'criada', 'ignorada')),
    UNIQUE (account_key, fitid)
);
-- vínculo entre linhas do extrato e lançamentos do app: 1 linha para 1 lançamento, 1 para vários, vários para 1
CREATE TABLE IF NOT EXISTS bank_links (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_id  INTEGER NOT NULL REFERENCES bank_txns(id) ON DELETE CASCADE,
    source  TEXT NOT NULL,
    ref_id  INTEGER NOT NULL,
    ym      TEXT NOT NULL,
    marked  INTEGER NOT NULL DEFAULT 0,
    UNIQUE (txn_id, source, ref_id, ym)
);
CREATE INDEX IF NOT EXISTS idx_bank_links_item ON bank_links(source, ref_id, ym);
CREATE INDEX IF NOT EXISTS idx_bank_status ON bank_txns(status, date);

CREATE INDEX IF NOT EXISTS idx_tx_date   ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_ops_asset ON asset_ops(asset_id, date);
CREATE INDEX IF NOT EXISTS idx_div_asset ON dividends(asset_id, date);
CREATE INDEX IF NOT EXISTS idx_fim_fi    ON fixed_income_moves(fi_id, date);
"""

# opening_month vazio = automático (primeiro mês com algum lançamento)
DEFAULT_SETTINGS = {
    "cdi_annual": "13.65",
    "opening_balance": "0",
    "opening_month": "",
}


def connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _add_column(conn, table: str, column: str, ddl: str) -> None:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def migrate(conn: sqlite3.Connection) -> None:
    """Atualiza bancos criados por versões anteriores do aplicativo."""
    _add_column(conn, "transactions", "card_id", "INTEGER")
    _add_column(conn, "installments", "card_id", "INTEGER")
    _add_column(conn, "fixed_income", "tax", "TEXT NOT NULL DEFAULT 'regressivo'")
    # contas: tudo que existia pertence à "Conta principal", que herda o antigo saldo inicial
    for table in ("transactions", "fixed_items", "installments", "asset_ops", "dividends", "fixed_income_moves", "bank_imports"):
        _add_column(conn, table, "account_id", "INTEGER")
    _add_column(conn, "credit_cards", "pay_account_id", "INTEGER")
    if conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0:
        old = conn.execute("SELECT value FROM settings WHERE key = 'opening_balance'").fetchone()
        try:
            opening = round(float(old[0]), 2) if old else 0.0
        except (TypeError, ValueError):
            opening = 0.0
        conn.execute("INSERT INTO accounts(name, kind, opening_balance) VALUES ('Conta principal', 'corrente', ?)", (opening,))
        conn.execute("UPDATE settings SET value = '0' WHERE key = 'opening_balance'")
    # conciliação: versão antiga guardava um único vínculo dentro de bank_txns
    cols = {r[1] for r in conn.execute("PRAGMA table_info(bank_txns)")}
    if "matched_source" in cols:
        conn.execute("INSERT OR IGNORE INTO bank_links(txn_id, source, ref_id, ym, marked) "
                     "SELECT id, matched_source, matched_ref_id, matched_ym, marked FROM bank_txns WHERE matched_source IS NOT NULL")
        conn.execute("UPDATE bank_txns SET matched_source = NULL, matched_ref_id = NULL, matched_ym = NULL, marked = 0 "
                     "WHERE matched_source IS NOT NULL")
    conn.execute("DROP INDEX IF EXISTS idx_bank_match")


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    migrate(conn)
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value))
    conn.commit()


@contextmanager
def session(path):
    """Uma conexão por requisição; confirma ao sair, desfaz em caso de erro."""
    conn = connect(path)
    try:
        init(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_settings(conn) -> dict:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}


def set_setting(conn, key: str, value) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def rows(conn, sql: str, params=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params)]
