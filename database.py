"""
SQL & PostgreSQL Unified Database Layer for Cyber OSINT Telegram Bot
Supports:
1. SQLite (Local zero-config bot.db)
2. PostgreSQL (Render.com free PostgreSQL via DATABASE_URL)
Automatic schema creation, JSON migration, and ACID persistence.
"""

import sys
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

import os
import json
import time
import sqlite3
import threading

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None
    RealDictCursor = None

DB_LOCK = threading.Lock()
DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")

# Render sometimes uses postgres:// which needs to be postgresql:// for SQLAlchemy/psycopg2
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

DATA_DIR = os.environ.get("DATA_DIR", "data")
SQLITE_PATH = os.path.join(DATA_DIR, "bot.db")


class DatabaseManager:
    def __init__(self):
        self.is_pg = bool(DATABASE_URL and psycopg2 is not None)
        self._ensure_dir()
        self.init_schema()

    def _ensure_dir(self):
        if not os.path.exists(DATA_DIR):
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
            except Exception as e:
                print(f"⚠️ [DB] Could not create {DATA_DIR}: {e}")

    def get_connection(self):
        if self.is_pg:
            try:
                return psycopg2.connect(DATABASE_URL)
            except Exception as e:
                print(f"⚠️ [DB] PostgreSQL connection failed ({e}). Falling back to SQLite.")
                self.is_pg = False
        return sqlite3.connect(SQLITE_PATH, check_same_thread=False)

    def init_schema(self):
        with DB_LOCK:
            conn = self.get_connection()
            cur = conn.cursor()

            # 1. Key-Value Store for configurations
            cur.execute("""
                CREATE TABLE IF NOT EXISTS kv_store (
                    key VARCHAR(128) PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at BIGINT NOT NULL
                )
            """)

            # 2. Users Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    uid BIGINT PRIMARY KEY,
                    lang VARCHAR(16) DEFAULT 'en',
                    tokens INT DEFAULT 480,
                    max_tokens INT DEFAULT 480,
                    regen_ms INT DEFAULT 3000,
                    regen_upgraded INT DEFAULT 0,
                    last_regen BIGINT DEFAULT 0,
                    instant_until BIGINT DEFAULT 0,
                    refs INT DEFAULT 0,
                    referred_by BIGINT DEFAULT 0,
                    last_seen BIGINT DEFAULT 0,
                    is_banned INT DEFAULT 0,
                    ban_reason TEXT,
                    note TEXT
                )
            """)

            # 3. Subscriptions Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    uid BIGINT PRIMARY KEY,
                    plan VARCHAR(64) NOT NULL,
                    started_at BIGINT NOT NULL,
                    expires_at BIGINT NOT NULL
                )
            """)

            # 4. Crypto Payment Orders Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS payment_orders (
                    order_id VARCHAR(64) PRIMARY KEY,
                    uid BIGINT NOT NULL,
                    plan_key VARCHAR(64),
                    plan_label VARCHAR(64),
                    plan_days INT,
                    usd_price NUMERIC,
                    crypto_name VARCHAR(64),
                    crypto_symbol VARCHAR(32),
                    address VARCHAR(256),
                    txid TEXT,
                    status VARCHAR(32) DEFAULT 'pending_txid',
                    created_at BIGINT NOT NULL,
                    submitted_at BIGINT DEFAULT 0,
                    approved_at BIGINT DEFAULT 0,
                    approved_by BIGINT DEFAULT 0
                )
            """)

            # 5. Admin Logs
            id_col = "SERIAL PRIMARY KEY" if self.is_pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS admin_logs (
                    id {id_col},
                    action VARCHAR(64) NOT NULL,
                    admin_id BIGINT NOT NULL,
                    details TEXT,
                    timestamp BIGINT NOT NULL
                )
            """)

            conn.commit()
            cur.close()
            conn.close()

            engine = "PostgreSQL" if self.is_pg else f"SQLite ({SQLITE_PATH})"
            print(f"🗄️ [DB] Database initialized successfully using {engine}!")

    # ── Key-Value CRUD ──
    def set_kv(self, key, value):
        with DB_LOCK:
            val_str = json.dumps(value, ensure_ascii=False)
            now = int(time.time())
            conn = self.get_connection()
            cur = conn.cursor()
            if self.is_pg:
                cur.execute("""
                    INSERT INTO kv_store (key, value, updated_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
                """, (key, val_str, now))
            else:
                cur.execute("""
                    INSERT INTO kv_store (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """, (key, val_str, now))
            conn.commit()
            cur.close()
            conn.close()

    def get_kv(self, key, default=None):
        with DB_LOCK:
            conn = self.get_connection()
            cur = conn.cursor()
            placeholder = "%s" if self.is_pg else "?"
            cur.execute(f"SELECT value FROM kv_store WHERE key = {placeholder}", (key,))
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row:
                try:
                    return json.loads(row[0])
                except Exception:
                    return row[0]
            return default

    # ── User CRUD ──
    def save_user(self, uid, data):
        with DB_LOCK:
            conn = self.get_connection()
            cur = conn.cursor()
            params = (
                int(uid),
                data.get("lang", "en"),
                int(data.get("tokens", 480)),
                int(data.get("max", 480)),
                int(data.get("regen_ms", 3000)),
                1 if data.get("regen_upgraded") else 0,
                int(data.get("last_regen", time.time())),
                int(data.get("instant_until", 0)),
                int(data.get("refs", 0)),
                int(data.get("referred_by", 0)),
                int(data.get("last_seen", time.time())),
                1 if data.get("is_banned") else 0,
                data.get("ban_reason", ""),
                data.get("note", "")
            )
            if self.is_pg:
                cur.execute("""
                    INSERT INTO users (uid, lang, tokens, max_tokens, regen_ms, regen_upgraded,
                                       last_regen, instant_until, refs, referred_by, last_seen,
                                       is_banned, ban_reason, note)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (uid) DO UPDATE SET
                        lang = EXCLUDED.lang, tokens = EXCLUDED.tokens, max_tokens = EXCLUDED.max_tokens,
                        regen_ms = EXCLUDED.regen_ms, regen_upgraded = EXCLUDED.regen_upgraded,
                        last_regen = EXCLUDED.last_regen, instant_until = EXCLUDED.instant_until,
                        refs = EXCLUDED.refs, referred_by = EXCLUDED.referred_by, last_seen = EXCLUDED.last_seen,
                        is_banned = EXCLUDED.is_banned, ban_reason = EXCLUDED.ban_reason, note = EXCLUDED.note
                """, params)
            else:
                cur.execute("""
                    INSERT INTO users (uid, lang, tokens, max_tokens, regen_ms, regen_upgraded,
                                       last_regen, instant_until, refs, referred_by, last_seen,
                                       is_banned, ban_reason, note)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(uid) DO UPDATE SET
                        lang = excluded.lang, tokens = excluded.tokens, max_tokens = excluded.max_tokens,
                        regen_ms = excluded.regen_ms, regen_upgraded = excluded.regen_upgraded,
                        last_regen = excluded.last_regen, instant_until = excluded.instant_until,
                        refs = excluded.refs, referred_by = excluded.referred_by, last_seen = excluded.last_seen,
                        is_banned = excluded.is_banned, ban_reason = excluded.ban_reason, note = excluded.note
                """, params)
            conn.commit()
            cur.close()
            conn.close()

    def get_all_users(self):
        with DB_LOCK:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("SELECT uid, lang, tokens, max_tokens, regen_ms, regen_upgraded, last_regen, instant_until, refs, referred_by, last_seen, is_banned, ban_reason, note FROM users")
            rows = cur.fetchall()
            cur.close()
            conn.close()
            users = {}
            for r in rows:
                users[int(r[0])] = {
                    "uid": int(r[0]),
                    "lang": r[1] or "en",
                    "tokens": int(r[2] or 480),
                    "max": int(r[3] or 480),
                    "regen_ms": int(r[4] or 3000),
                    "regen_upgraded": bool(r[5]),
                    "last_regen": float(r[6] or 0),
                    "instant_until": float(r[7] or 0),
                    "refs": int(r[8] or 0),
                    "referred_by": int(r[9] or 0),
                    "last_seen": int(r[10] or 0),
                    "is_banned": bool(r[11]),
                    "ban_reason": r[12] or "",
                    "note": r[13] or ""
                }
            return users

    # ── Subscription CRUD ──
    def save_subscription(self, uid, data):
        with DB_LOCK:
            conn = self.get_connection()
            cur = conn.cursor()
            params = (int(uid), data.get("plan", "VIP"), int(data.get("started_at", time.time())), int(data.get("expires_at", 0)))
            if self.is_pg:
                cur.execute("""
                    INSERT INTO subscriptions (uid, plan, started_at, expires_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (uid) DO UPDATE SET
                        plan = EXCLUDED.plan, started_at = EXCLUDED.started_at, expires_at = EXCLUDED.expires_at
                """, params)
            else:
                cur.execute("""
                    INSERT INTO subscriptions (uid, plan, started_at, expires_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(uid) DO UPDATE SET
                        plan = excluded.plan, started_at = excluded.started_at, expires_at = excluded.expires_at
                """, params)
            conn.commit()
            cur.close()
            conn.close()

    def delete_subscription(self, uid):
        with DB_LOCK:
            conn = self.get_connection()
            cur = conn.cursor()
            placeholder = "%s" if self.is_pg else "?"
            cur.execute(f"DELETE FROM subscriptions WHERE uid = {placeholder}", (int(uid),))
            conn.commit()
            cur.close()
            conn.close()

    def get_all_subscriptions(self):
        with DB_LOCK:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("SELECT uid, plan, started_at, expires_at FROM subscriptions")
            rows = cur.fetchall()
            cur.close()
            conn.close()
            subs = {}
            for r in rows:
                subs[int(r[0])] = {
                    "plan": r[1],
                    "started_at": int(r[2]),
                    "expires_at": int(r[3])
                }
            return subs

    # ── Order CRUD ──
    def save_order(self, order_id, o):
        with DB_LOCK:
            conn = self.get_connection()
            cur = conn.cursor()
            params = (
                str(order_id),
                int(o.get("uid", 0)),
                o.get("plan_key", ""),
                o.get("plan_label", ""),
                int(o.get("plan_days", 30)),
                float(o.get("usd_price", 0)),
                o.get("crypto_name", ""),
                o.get("crypto_symbol", ""),
                o.get("address", ""),
                o.get("txid", ""),
                o.get("status", "pending_txid"),
                int(o.get("created_at", time.time())),
                int(o.get("submitted_at", 0)),
                int(o.get("approved_at", 0)),
                int(o.get("approved_by", 0))
            )
            if self.is_pg:
                cur.execute("""
                    INSERT INTO payment_orders (order_id, uid, plan_key, plan_label, plan_days, usd_price,
                                                crypto_name, crypto_symbol, address, txid, status,
                                                created_at, submitted_at, approved_at, approved_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (order_id) DO UPDATE SET
                        status = EXCLUDED.status, txid = EXCLUDED.txid, submitted_at = EXCLUDED.submitted_at,
                        approved_at = EXCLUDED.approved_at, approved_by = EXCLUDED.approved_by
                """, params)
            else:
                cur.execute("""
                    INSERT INTO payment_orders (order_id, uid, plan_key, plan_label, plan_days, usd_price,
                                                crypto_name, crypto_symbol, address, txid, status,
                                                created_at, submitted_at, approved_at, approved_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(order_id) DO UPDATE SET
                        status = excluded.status, txid = excluded.txid, submitted_at = excluded.submitted_at,
                        approved_at = excluded.approved_at, approved_by = excluded.approved_by
                """, params)
            conn.commit()
            cur.close()
            conn.close()

    def get_all_orders(self):
        with DB_LOCK:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT order_id, uid, plan_key, plan_label, plan_days, usd_price,
                       crypto_name, crypto_symbol, address, txid, status,
                       created_at, submitted_at, approved_at, approved_by
                FROM payment_orders
            """)
            rows = cur.fetchall()
            cur.close()
            conn.close()
            orders = {}
            for r in rows:
                orders[str(r[0])] = {
                    "order_id": str(r[0]),
                    "uid": int(r[1]),
                    "plan_key": r[2],
                    "plan_label": r[3],
                    "plan_days": int(r[4] or 30),
                    "usd_price": float(r[5] or 0),
                    "crypto_name": r[6],
                    "crypto_symbol": r[7],
                    "address": r[8],
                    "txid": r[9] or "",
                    "status": r[10] or "pending_txid",
                    "created_at": int(r[11] or 0),
                    "submitted_at": int(r[12] or 0),
                    "approved_at": int(r[13] or 0),
                    "approved_by": int(r[14] or 0)
                }
            return orders

    # ── Initial JSON to SQL Migration ──
    def migrate_from_json(self):
        """Automatically imports all data from data/*.json into SQLite / PostgreSQL if empty."""
        try:
            # 1. System Config
            sys_file = os.path.join(DATA_DIR, "system_config.json")
            if os.path.exists(sys_file) and not self.get_kv("system_config"):
                with open(sys_file, "r", encoding="utf-8") as f:
                    self.set_kv("system_config", json.load(f))

            # 2. API Config
            api_file = os.path.join(DATA_DIR, "api_config.json")
            if os.path.exists(api_file) and not self.get_kv("api_config"):
                with open(api_file, "r", encoding="utf-8") as f:
                    self.set_kv("api_config", json.load(f))

            # 3. Crypto Config
            crypto_file = os.path.join(DATA_DIR, "crypto_config.json")
            if os.path.exists(crypto_file) and not self.get_kv("crypto_config"):
                with open(crypto_file, "r", encoding="utf-8") as f:
                    self.set_kv("crypto_config", json.load(f))

            # 4. Admins & Password
            admin_file = os.path.join(DATA_DIR, "admins.json")
            if os.path.exists(admin_file) and not self.get_kv("admins"):
                with open(admin_file, "r", encoding="utf-8") as f:
                    self.set_kv("admins", json.load(f))

            pwd_file = os.path.join(DATA_DIR, "admin_password.json")
            if os.path.exists(pwd_file) and not self.get_kv("admin_password"):
                with open(pwd_file, "r", encoding="utf-8") as f:
                    self.set_kv("admin_password", json.load(f))

            # 5. Orders
            orders_file = os.path.join(DATA_DIR, "payment_orders.json")
            if os.path.exists(orders_file) and not self.get_all_orders():
                with open(orders_file, "r", encoding="utf-8") as f:
                    orders_data = json.load(f)
                    for oid, o in (orders_data or {}).items():
                        self.save_order(oid, o)

            # 6. Subscriptions
            subs_file = os.path.join(DATA_DIR, "user_subscriptions.json")
            if os.path.exists(subs_file) and not self.get_all_subscriptions():
                with open(subs_file, "r", encoding="utf-8") as f:
                    subs_data = json.load(f)
                    for uid, s in (subs_data or {}).items():
                        self.save_subscription(uid, s)

            print("✅ [DB] JSON to SQL Migration completed successfully.")
        except Exception as e:
            print(f"⚠️ [DB] Migration note: {e}")


# Global Singleton
db = DatabaseManager()
