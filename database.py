import sqlite3
import threading
from datetime import datetime

from config import DB_PATH


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Database:
    def __init__(self, path=DB_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    phone TEXT NOT NULL,
                    token TEXT,
                    tid TEXT,
                    sid TEXT,
                    device_id TEXT,
                    swuid TEXT,
                    customer_id TEXT,
                    total_earned REAL DEFAULT 0,
                    active INTEGER DEFAULT 0,
                    created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS buzz_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    link_url TEXT UNIQUE NOT NULL,
                    campaign_id TEXT NOT NULL,
                    added_by INTEGER,
                    created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS buzz_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    link_id INTEGER,
                    action TEXT,
                    amount REAL DEFAULT 0,
                    status TEXT,
                    created_at TEXT
                );
                """
            )
            self._conn.commit()

    def _execute(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def add_account(self, telegram_id, phone, device_id, swuid, token, tid, sid, customer_id):
        now_s = now()
        cur = self._execute(
            "SELECT id FROM accounts WHERE telegram_id = ? AND phone = ?",
            (telegram_id, phone),
        )
        row = cur.fetchone()
        if row:
            self._execute(
                "UPDATE accounts SET token = ?, tid = ?, sid = ?, device_id = ?, swuid = ?, customer_id = ?, active = 1 WHERE id = ?",
                (token, tid, sid, device_id, swuid, customer_id, row["id"]),
            )
            account_id = row["id"]
        else:
            cur = self._execute(
                "INSERT INTO accounts (telegram_id, phone, token, tid, sid, device_id, swuid, customer_id, active, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (telegram_id, phone, token, tid, sid, device_id, swuid, customer_id, now_s),
            )
            account_id = cur.lastrowid
        self._execute("UPDATE accounts SET active = 0 WHERE telegram_id = ? AND id != ?", (telegram_id, account_id))
        return account_id

    def get_accounts(self, telegram_id):
        cur = self._execute("SELECT * FROM accounts WHERE telegram_id = ? ORDER BY id", (telegram_id,))
        return [dict(r) for r in cur.fetchall()]

    def get_account(self, account_id):
        cur = self._execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_active_account(self, telegram_id):
        cur = self._execute("SELECT * FROM accounts WHERE telegram_id = ? AND active = 1", (telegram_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def set_active(self, telegram_id, account_id):
        self._execute("UPDATE accounts SET active = 0 WHERE telegram_id = ?", (telegram_id,))
        self._execute("UPDATE accounts SET active = 1 WHERE id = ? AND telegram_id = ?", (account_id, telegram_id))

    def remove_account(self, account_id):
        self._execute("DELETE FROM accounts WHERE id = ?", (account_id,))

    def add_links(self, entries, added_by):
        count = 0
        for url, campaign_id in entries:
            cur = self._execute(
                "INSERT OR IGNORE INTO buzz_links (link_url, campaign_id, added_by, created_at) VALUES (?, ?, ?, ?)",
                (url, campaign_id, added_by, now()),
            )
            if cur.rowcount:
                count += 1
        return count

    def get_all_links(self):
        cur = self._execute("SELECT * FROM buzz_links ORDER BY id")
        return [dict(r) for r in cur.fetchall()]

    def delete_link(self, link_id):
        self._execute("DELETE FROM buzz_links WHERE id = ?", (link_id,))

    def log(self, account_id, link_id, action, amount, status):
        self._execute(
            "INSERT INTO buzz_logs (account_id, link_id, action, amount, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, link_id, action, amount, status, now()),
        )

    def add_earned(self, account_id, amount):
        self._execute("UPDATE accounts SET total_earned = total_earned + ? WHERE id = ?", (amount, account_id))

    def get_stats(self, telegram_id):
        accounts = self.get_accounts(telegram_id)
        total = sum(a["total_earned"] for a in accounts)
        return accounts, total

    def all_accounts(self):
        cur = self._execute("SELECT * FROM accounts ORDER BY telegram_id, id")
        return [dict(r) for r in cur.fetchall()]

    def total_earnings(self):
        cur = self._execute("SELECT COALESCE(SUM(amount), 0) AS total FROM buzz_logs")
        return cur.fetchone()["total"]

    def total_logs(self):
        cur = self._execute("SELECT COUNT(*) AS total FROM buzz_logs")
        return cur.fetchone()["total"]
