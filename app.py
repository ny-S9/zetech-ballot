"""
Zetech Electronic Ballot System
Flask + SQLite.  Run:  python app.py   then open http://127.0.0.1:5000
"""
import csv
import hmac
import hashlib
import io
import os
import secrets
import smtplib
import sqlite3
import time
import uuid
from email.message import EmailMessage
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, send_from_directory, session)
from werkzeug.security import check_password_hash, generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
# On Vercel the project folder is read-only; only /tmp is writable (and it is NOT permanent storage).
ON_VERCEL = bool(os.environ.get("VERCEL"))
# DATABASE_URL=mysql://user:password@host:3306/dbname  ->  use a MySQL database (permanent, shared by every
# copy of the app). Without it the app uses a local SQLite file.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_MYSQL = DATABASE_URL.lower().startswith(("mysql://", "mysql+pymysql://", "mariadb://"))
# Local data (the SQLite database and any old upload files) lives OUTSIDE the project folder,
# so putting a new version of the project in place never touches it. Override with DATA_DIR or DB_PATH.
DATA_DIR = os.environ.get("DATA_DIR") or (
    "/tmp/zetech-ballot" if ON_VERCEL else os.path.join(os.path.expanduser("~"), "ZetechBallotData"))
DB_PATH = os.environ.get("DB_PATH") or os.path.join(DATA_DIR, "ballot.db")
UPLOAD_DIR = os.environ.get("UPLOAD_DIR") or os.path.join(DATA_DIR, "uploads")   # old uploads only
os.makedirs(UPLOAD_DIR, exist_ok=True)
if not USE_MYSQL:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
MAX_UPLOAD_MB = 4 if ON_VERCEL else 8   # Vercel limits request bodies to about 4.5 MB

INTEGRITY_ERRORS = (sqlite3.IntegrityError,)
if USE_MYSQL:
    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError("DATABASE_URL is a MySQL address but the PyMySQL package is missing. "
                           "Run: pip install PyMySQL certifi") from exc
    INTEGRITY_ERRORS += (pymysql.err.IntegrityError,)
elif ON_VERCEL:
    print("[ZETECH BALLOT] WARNING: no DATABASE_URL set on Vercel. Data is temporary and will be lost.", flush=True)

PHOTO_EXT = {"png", "jpg", "jpeg", "webp"}
RESULTS_EXT = {"pdf", "png", "jpg", "jpeg"}
MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
        "pdf": "application/pdf"}
# Live results stay visible to students as before. Set SHOW_LIVE_RESULTS=0 to hide live counts from students.
SHOW_LIVE = os.environ.get("SHOW_LIVE_RESULTS", "1") != "0"
# Seconds of inactivity before a login ends. Admins get longer so they can leave the Control room open.
IDLE_SECONDS = {"student": 30 * 60, "admin": 4 * 60 * 60}


# ---------------------------------------------------------------- database layer (SQLite or MySQL)
class Row(dict):
    """A result row that works like sqlite3.Row: row["name"], row[0] and row.name in templates."""
    def __getitem__(self, k):
        if isinstance(k, int):
            return list(self.values())[k]
        return dict.__getitem__(self, k)


def mysql_sql(sql, has_params):
    """Translate the SQLite-flavoured SQL used in this app to MySQL."""
    sql = sql.replace("INSERT OR IGNORE INTO", "INSERT IGNORE INTO").replace("INSERT OR REPLACE INTO", "REPLACE INTO")
    if not has_params:
        return sql
    out, quote = [], None
    for ch in sql:
        if quote:
            out.append("%%" if ch == "%" else ch)
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            out.append(ch)
        elif ch == "?":
            out.append("%s")
        elif ch == "%":
            out.append("%%")
        else:
            out.append(ch)
    return "".join(out)


class MyCursor:
    def __init__(self, cur):
        self.cur, self.rowcount, self.lastrowid = cur, cur.rowcount, cur.lastrowid

    def fetchone(self):
        r = self.cur.fetchone()
        return Row(r) if r is not None else None

    def fetchall(self):
        return [Row(r) for r in self.cur.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class DB:
    """Same small interface for both databases: execute / commit / rollback / close."""
    def __init__(self, conn, mysql):
        self.conn, self.mysql = conn, mysql

    def execute(self, sql, params=None):
        if not self.mysql:
            return self.conn.execute(sql, tuple(params) if params else ())
        cur = self.conn.cursor()
        cur.execute(mysql_sql(sql, bool(params)), tuple(params) if params else None)
        return MyCursor(cur)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


def ca_bundle():
    import ssl
    paths = ["/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt",
             ssl.get_default_verify_paths().cafile]
    try:
        import certifi
        paths.insert(0, certifi.where())
    except ImportError:
        pass
    for p in paths:
        if p and os.path.exists(p):
            return p
    raise RuntimeError("No certificate bundle found for the secure database connection. "
                       "Run: pip install certifi (or set DB_SSL=0 for a local database).")


def connect():
    if USE_MYSQL:
        from urllib.parse import unquote, urlparse
        u = urlparse(DATABASE_URL.replace("mysql+pymysql://", "mysql://").replace("mariadb://", "mysql://"))
        host = u.hostname or "127.0.0.1"
        kw = dict(host=host, port=u.port or 3306, user=unquote(u.username or ""),
                  password=unquote(u.password or ""), database=(u.path or "/").lstrip("/"),
                  charset="utf8mb4", autocommit=False, connect_timeout=15,
                  init_command="SET time_zone='+00:00'")
        local = host in ("localhost", "127.0.0.1", "::1")
        if not local and os.environ.get("DB_SSL", "1").lower() not in ("0", "false", "no"):
            kw.update(ssl_ca=ca_bundle(), ssl_verify_cert=True, ssl_verify_identity=True)
        return DB(pymysql.connect(**kw), True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return DB(conn, False)


# Data that must exist in EVERY database, applied once (recorded in app_settings). Existing data is never touched.
DATA_PATCHES = [
    ("2026-10-dse-students", [
        # admission_number, full_name, email, phone, department
        ("DSE-03-0435/2025", "Immanuel Ndunda", "immanuelndunda@students.zetech.ac.ke", "0758461503", "ICT"),
        ("DSE-03-0436/2025", "Kelvin Ochieng", "kelvinochieng@students.zetech.ac.ke", "0700000436", "ICT"),
        ("DSE-03-0437/2025", "Winfred Mwikali", "winfredmwikali@students.zetech.ac.ke", "0700000437", "ICT"),
        ("DSE-03-0438/2025", "Samson Kipchumba", "samsonkipchumba@students.zetech.ac.ke", "0700000438", "ICT"),
    ]),
]


def import_legacy_data():
    """One time: if an older version kept ballot.db inside the project folder, move a copy to DATA_DIR."""
    old = os.path.join(BASE, "ballot.db")
    if os.path.exists(DB_PATH) or not os.path.exists(old):
        return
    import shutil
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(old + suffix):
            shutil.copy2(old + suffix, DB_PATH + suffix)
    old_up = os.path.join(BASE, "uploads")
    if os.path.isdir(old_up):
        for name in os.listdir(old_up):
            if not name.startswith("."):
                shutil.copy2(os.path.join(old_up, name), os.path.join(UPLOAD_DIR, name))
    print(f"[ZETECH BALLOT] Copied your existing data to {DB_PATH}", flush=True)


EXTRA_TABLES_SQLITE = [
    """CREATE TABLE IF NOT EXISTS login_codes (
        student_id INTEGER PRIMARY KEY, code_hash TEXT NOT NULL, expires_at REAL NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, sent_at REAL NOT NULL,
        FOREIGN KEY (student_id) REFERENCES students(student_id))""",
    """CREATE TABLE IF NOT EXISTS app_settings (setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS stored_files (file_name TEXT PRIMARY KEY, mime_type TEXT NOT NULL,
        content BLOB NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS feedback (
        feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL DEFAULT 'feedback'
                 CHECK (category IN ('feedback', 'suggestion', 'concern', 'other')),
        message TEXT NOT NULL,
        is_anonymous INTEGER NOT NULL DEFAULT 1 CHECK (is_anonymous IN (0, 1)),
        sender_name TEXT, sender_admission TEXT,
        status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'read', 'resolved')),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
]
EXTRA_TABLES_MYSQL = [
    """CREATE TABLE IF NOT EXISTS app_settings (setting_key VARCHAR(100) NOT NULL PRIMARY KEY,
        setting_value TEXT NOT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS stored_files (file_name VARCHAR(100) NOT NULL PRIMARY KEY,
        mime_type VARCHAR(100) NOT NULL, content LONGBLOB NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS feedback (
        feedback_id INT AUTO_INCREMENT PRIMARY KEY,
        category VARCHAR(20) NOT NULL DEFAULT 'feedback',
        message TEXT NOT NULL,
        is_anonymous TINYINT NOT NULL DEFAULT 1,
        sender_name VARCHAR(150) NULL, sender_admission VARCHAR(40) NULL,
        status VARCHAR(10) NOT NULL DEFAULT 'new',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        CHECK (category IN ('feedback', 'suggestion', 'concern', 'other')),
        CHECK (status IN ('new', 'read', 'resolved'))) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]


def run_mysql_schema(c):
    with open(os.path.join(BASE, "schema_mysql.sql"), encoding="utf-8") as f:
        text = "\n".join(line for line in f.read().splitlines() if not line.strip().startswith("--"))
    for stmt in text.split(";\n"):
        if stmt.strip():
            cur = c.conn.cursor()
            cur.execute(stmt.strip().rstrip(";"))
    c.commit()


def init_db():
    """Create tables the first time, apply data patches, and return the secret key.
    Existing data is never reset: tables are only created when missing."""
    if not USE_MYSQL:
        import_legacy_data()
    c = connect()
    try:
        if USE_MYSQL:
            has = c.execute("""SELECT COUNT(*) AS n FROM information_schema.tables
                               WHERE table_schema = DATABASE() AND table_name = 'students'""").fetchone()["n"]
            if not has:
                run_mysql_schema(c)
            for ddl in EXTRA_TABLES_MYSQL:
                c.conn.cursor().execute(ddl)
        else:
            c.conn.execute("PRAGMA journal_mode = WAL")
            if not c.execute("SELECT name FROM sqlite_master WHERE name='students'").fetchone():
                with open(os.path.join(BASE, "schema.sql"), encoding="utf-8") as f:
                    c.conn.executescript(f.read())
            if not c.execute("SELECT name FROM sqlite_master WHERE name='departments'").fetchone():
                with open(os.path.join(BASE, "migrate_v2.sql"), encoding="utf-8") as f:   # upgrade an older database
                    c.conn.executescript(f.read())
            for ddl in EXTRA_TABLES_SQLITE:
                c.execute(ddl)
        c.execute("INSERT OR IGNORE INTO departments (department_name, delegate_seats) VALUES ('Nursing', 1)")
        if not c.execute("SELECT 1 FROM admins").fetchone():
            c.execute("INSERT INTO admins (full_name, phone_number, department, registration_no, password)"
                      " VALUES (?,?,?,?,?)",
                      ("Kariuki Susan", "0748672922", "ICT", "456",
                       generate_password_hash(os.environ.get("ADMIN_PASSWORD", "456"))))
        for patch_id, students in DATA_PATCHES:
            if c.execute("SELECT 1 FROM app_settings WHERE setting_key=?", ("patch:" + patch_id,)).fetchone():
                continue
            for adm, name, email, phone, dept in students:
                c.execute("""INSERT OR IGNORE INTO students (admission_number, full_name, email, phone_number, department_id)
                             VALUES (?,?,?,?,(SELECT department_id FROM departments WHERE department_name=?))""",
                          (adm, name, email, phone, dept))
            c.execute("INSERT OR IGNORE INTO app_settings (setting_key, setting_value) VALUES (?,?)",
                      ("patch:" + patch_id, "done"))
        secret = os.environ.get("SECRET_KEY")
        if not secret:   # kept in the database so every copy of the app signs sessions with the same key
            row = c.execute("SELECT setting_value FROM app_settings WHERE setting_key='secret_key'").fetchone()
            if not row:
                c.execute("INSERT OR IGNORE INTO app_settings (setting_key, setting_value) VALUES (?,?)",
                          ("secret_key", secrets.token_hex(32)))
                c.commit()
                row = c.execute("SELECT setting_value FROM app_settings WHERE setting_key='secret_key'").fetchone()
            secret = row["setting_value"]
        c.commit()
        return secret
    finally:
        c.close()


# Static files live in public/static (Vercel serves the public folder directly).
app = Flask(__name__, static_folder=os.path.join(BASE, "public", "static"), static_url_path="/static")
app.secret_key = os.environ.get("SECRET_KEY") or "replaced-by-init-db"
app.config.update(
    MAX_CONTENT_LENGTH=MAX_UPLOAD_MB * 1024 * 1024,   # per request
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),    # cookie lifetime; idle limits are in IDLE_SECONDS
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=ON_VERCEL,   # Vercel is always https
)


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


def current_election():
    return db().execute("SELECT * FROM elections ORDER BY election_id DESC LIMIT 1").fetchone()


def log(action, details="", actor_id=None, role=None, anonymous=False):
    if anonymous:   # nothing that identifies the person is written
        db().execute("INSERT INTO audit_log (actor_id, actor_role, action, details) VALUES (?,?,?,?)",
                     (None, None, action, details))
        return
    db().execute(
        "INSERT INTO audit_log (actor_id, actor_role, action, details) VALUES (?,?,?,?)",
        (actor_id if actor_id is not None else session.get("uid"),
         role or session.get("role"), action, details))


# ---------------------------------------------------------------- security
FAILS = {}


def is_locked(key):
    n, until = FAILS.get(key, (0, 0))
    if until and until <= time.time():
        FAILS.pop(key, None)
        return False
    return until > time.time()


def register_fail(key):
    n, _ = FAILS.get(key, (0, 0))
    n += 1
    FAILS[key] = (n, time.time() + 300 if n >= 5 else 0)


def need(*roles):
    def deco(f):
        @wraps(f)
        def wrapper(*a, **k):
            now = time.time()
            if session.get("uid") and now - session.get("t", now) > IDLE_SECONDS.get(session.get("role"), 1800):
                session.clear()   # idle for too long
            if not session.get("uid"):
                flash("Please log in to continue. You are signed out after a period of inactivity.", "err")
                if request.path.startswith("/admin") and request.cookies.get("dean") == "1":
                    return redirect(ADMIN_PATH)   # only browsers that logged in as Dean's office before
                return redirect("/login")
            if roles and session.get("role") not in roles:
                return redirect("/")
            session["t"] = now
            session.permanent = True
            return f(*a, **k)
        return wrapper
    return deco


@app.before_request
def csrf_protect():
    if request.method == "POST":
        sent, real = request.form.get("csrf", ""), session.get("csrf", "")
        if not real or not secrets.compare_digest(sent, real):
            abort(400, "Your form expired. Go back, refresh the page and try again.")


@app.after_request
def headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.context_processor
def inject():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return dict(csrf=session["csrf"], role=session.get("role"), show_live=SHOW_LIVE,
                user_name=session.get("name"), election=current_election())


@app.template_filter("eat")
def eat(ts):
    """SQLite stores UTC; show East Africa Time (UTC+3)."""
    if not ts:
        return "-"
    try:
        dt = (ts if isinstance(ts, datetime) else datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S")) + timedelta(hours=3)
        return dt.strftime("%d %b %Y, %H:%M")
    except ValueError:
        return ts


@app.template_filter("initials")
def initials(name):
    parts = (name or "?").split()
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()


@app.errorhandler(400)
def bad(e):
    flash(getattr(e, "description", "Bad request."), "err")
    return redirect(request.referrer or "/login")


@app.errorhandler(413)
def too_big(e):
    flash(f"That upload is too large. Keep files under {MAX_UPLOAD_MB} MB in total.", "err")
    return redirect(request.referrer or "/")


# ---------------------------------------------------------------- uploads
def save_upload(file, prefix, allowed):
    """Store an uploaded file in the database under a random name. Returns (filename|None, error|None)."""
    if not file or not file.filename:
        return None, None
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in allowed:
        return None, "Allowed file types: " + ", ".join(sorted(allowed)) + "."
    data = file.read()
    if not data:
        return None, "That file is empty."
    name = f"{prefix}_{uuid.uuid4().hex}.{ext}"
    db().execute("INSERT INTO stored_files (file_name, mime_type, content) VALUES (?,?,?)",
                 (name, MIME[ext], data))
    return name, None


def remove_upload(name):
    if name:
        name = os.path.basename(name)
        db().execute("DELETE FROM stored_files WHERE file_name=?", (name,))
        try:
            os.remove(os.path.join(UPLOAD_DIR, name))   # older uploads that were saved on disk
        except OSError:
            pass


@app.route("/uploads/<path:fn>")
@need()
def uploads(fn):
    fn = os.path.basename(fn)
    if fn.startswith("photo_"):
        pass
    elif fn.startswith("results_"):
        if session["role"] != "admin":
            own = db().execute("SELECT 1 FROM candidates WHERE results_path=? AND student_id=?",
                               (fn, session["uid"])).fetchone()
            if not own:
                abort(403)
    else:
        abort(404)
    row = db().execute("SELECT mime_type, content FROM stored_files WHERE file_name=?", (fn,)).fetchone()
    if row:
        resp = Response(bytes(row["content"]), mimetype=row["mime_type"])
        resp.headers["Cache-Control"] = "private, max-age=86400"   # file names are unique per upload
        return resp
    if os.path.exists(os.path.join(UPLOAD_DIR, fn)):
        return send_from_directory(UPLOAD_DIR, fn)
    abort(404)


# ---------------------------------------------------------------- auth
@app.route("/")
def index():
    if session.get("uid"):
        return redirect("/admin" if session.get("role") == "admin" else "/dashboard")
    return redirect("/login")


# The Dean's office login is NOT linked anywhere on the student pages.
# It lives at this secret address. Change it with the ADMIN_LOGIN_PATH environment variable.
ADMIN_PATH = "/" + os.environ.get("ADMIN_LOGIN_PATH", "dean-access").strip("/")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ident = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        key = f"student:{ident.lower()}"
        if is_locked(key):
            flash("Too many wrong attempts. Wait 5 minutes and try again.", "err")
            return render_template("login.html")
        s = db().execute("SELECT * FROM students WHERE UPPER(admission_number)=UPPER(?)",
                         (ident,)).fetchone()
        if s and s["password"] is None:
            flash("Your account isn't activated yet. Set a password below to start.", "ok")
            return redirect("/activate?adm=" + ident)
        if s and check_password_hash(s["password"], pw):
            FAILS.pop(key, None)
            session.clear()
            session["pending"] = s["student_id"]   # password OK, email code still needed
            session.permanent = True
            status, msg = issue_code(s)
            flash(msg, "err" if status == "fail" else "ok")
            return redirect("/verify")
        register_fail(key)
        flash("Those details don't match. Check your ID and password and try again.", "err")
    return render_template("login.html")


@app.route(ADMIN_PATH, methods=["GET", "POST"])
def admin_login():
    if request.method == "GET" and session.get("role") == "admin":
        return redirect("/admin")
    if request.method == "POST":
        ident = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        key = f"admin:{ident.lower()}"
        if is_locked(key):
            flash("Too many wrong attempts. Wait 5 minutes and try again.", "err")
            return render_template("admin_login.html")
        u = db().execute("SELECT * FROM admins WHERE registration_no=?", (ident,)).fetchone()
        if u and check_password_hash(u["password"], pw):
            FAILS.pop(key, None)
            session.clear()
            session.update(uid=u["admin_id"], role="admin", name=u["full_name"], t=time.time())
            session.permanent = True
            log("Admin logged in")
            db().commit()
            resp = redirect("/admin")
            resp.set_cookie("dean", "1", max_age=30 * 24 * 3600, httponly=True, samesite="Lax", secure=ON_VERCEL)
            return resp
        register_fail(key)
        flash("Those details don't match.", "err")
    return render_template("admin_login.html")


# ---------------------------------------------------------------- email verification
CODE_TTL = 300        # code valid for 5 minutes
CODE_MAX_TRIES = 5
RESEND_WAIT = 30      # seconds between emails


def mask_email(e):
    name, _, dom = e.partition("@")
    return (name[:2] + "*" * max(len(name) - 2, 1)) + "@" + dom


def hash_code(code):
    return hmac.new(app.secret_key.encode(), code.encode(), hashlib.sha256).hexdigest()


def send_code_email(to, name, code):
    """Returns (status, message). status: sent | demo | fail."""
    host = os.environ.get("SMTP_HOST")
    if not host:  # no email server configured: demo mode (prints code in the terminal)
        print(f"[ZETECH BALLOT - DEMO MODE] verification code for {to}: {code}", flush=True)
        return "demo", f"Demo mode: no email server is set up yet, so your code is {code}."
    try:
        m = EmailMessage()
        m["Subject"] = "Your Zetech Ballot verification code"
        m["From"] = os.environ.get("SMTP_FROM") or os.environ["SMTP_USER"]
        m["To"] = to
        m.set_content(f"Hi {name.split()[0]},\n\nYour Zetech Ballot verification code is {code}.\n"
                      f"It expires in 5 minutes. If you did not try to log in, ignore this email "
                      f"and do not share the code with anyone.\n")
        port = int(os.environ.get("SMTP_PORT", 587))
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15) as srv:
                srv.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
                srv.send_message(m)
        else:
            with smtplib.SMTP(host, port, timeout=15) as srv:
                srv.starttls()
                srv.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
                srv.send_message(m)
        return "sent", f"We emailed a 6-digit code to {mask_email(to)}. It expires in 5 minutes."
    except Exception as exc:  # network / login problems
        print("[ZETECH BALLOT] email failed:", exc, flush=True)
        return "fail", "We couldn't send the email right now. Press 'Send a new code' to try again."


def issue_code(student):
    code = f"{secrets.randbelow(10 ** 6):06d}"
    now = time.time()
    if USE_MYSQL:
        upsert = """ON DUPLICATE KEY UPDATE code_hash=VALUES(code_hash), expires_at=VALUES(expires_at),
                    attempts=0, sent_at=VALUES(sent_at)"""
    else:
        upsert = """ON CONFLICT(student_id) DO UPDATE SET code_hash=excluded.code_hash,
                    expires_at=excluded.expires_at, attempts=0, sent_at=excluded.sent_at"""
    db().execute("INSERT INTO login_codes (student_id, code_hash, expires_at, attempts, sent_at) "
                 "VALUES (?,?,?,0,?) " + upsert,
                 (student["student_id"], hash_code(code), now + CODE_TTL, now))
    db().commit()
    status, msg = send_code_email(student["email"], student["full_name"], code)
    log("Verification code sent" if status != "fail" else "Verification email failed",
        mask_email(student["email"]), student["student_id"], "student")
    db().commit()
    return status, msg


@app.route("/verify", methods=["GET", "POST"])
def verify():
    sid = session.get("pending")
    if not sid:
        return redirect("/login")
    d = db()
    s = d.execute("SELECT * FROM students WHERE student_id=?", (sid,)).fetchone()
    if not s:
        session.clear()
        return redirect("/login")
    if request.method == "POST":
        row = d.execute("SELECT * FROM login_codes WHERE student_id=?", (sid,)).fetchone()
        if request.form.get("step") == "resend":
            wait = RESEND_WAIT - (time.time() - row["sent_at"]) if row else 0
            if wait > 0:
                flash(f"Please wait {int(wait) + 1} seconds before asking for a new code.", "err")
            else:
                status, msg = issue_code(s)
                flash(msg, "err" if status == "fail" else "ok")
        else:
            code = "".join(ch for ch in request.form.get("code", "") if ch.isdigit())
            if not row or row["expires_at"] < time.time():
                flash("That code has expired. Send a new code.", "err")
            elif row["attempts"] >= CODE_MAX_TRIES:
                d.execute("DELETE FROM login_codes WHERE student_id=?", (sid,))
                log("Verification locked (too many wrong codes)", actor_id=sid, role="student")
                d.commit()
                session.clear()
                flash("Too many wrong codes. Log in again to get a new one.", "err")
                return redirect("/login")
            elif hmac.compare_digest(row["code_hash"], hash_code(code)):
                d.execute("DELETE FROM login_codes WHERE student_id=?", (sid,))
                session.clear()
                session.update(uid=s["student_id"], role="student", name=s["full_name"], t=time.time())
                session.permanent = True
                log("Student logged in (email verified)")
                d.commit()
                return redirect("/dashboard")
            else:
                d.execute("UPDATE login_codes SET attempts = attempts + 1 WHERE student_id=?", (sid,))
                d.commit()
                left = CODE_MAX_TRIES - row["attempts"] - 1
                flash(f"Wrong code. {left} {'try' if left == 1 else 'tries'} left.", "err")
    session.permanent = True
    return render_template("verify.html", email_hint=mask_email(s["email"]))


@app.route("/activate", methods=["GET", "POST"])
def activate():
    if request.method == "POST":
        adm = request.form.get("admission_number", "").strip()
        email = request.form.get("email", "").strip().lower()
        pw, pw2 = request.form.get("password", ""), request.form.get("confirm", "")
        s = db().execute("SELECT * FROM students WHERE UPPER(admission_number)=UPPER(?)", (adm,)).fetchone()
        if not s or s["email"].lower() != email:
            flash("We couldn't match that admission number and school email.", "err")
        elif s["password"] is not None:
            flash("This account is already activated. Log in instead.", "err")
            return redirect("/login")
        elif len(pw) < 6:
            flash("Choose a password with at least 6 characters.", "err")
        elif pw != pw2:
            flash("The two passwords don't match.", "err")
        else:
            db().execute("UPDATE students SET password=? WHERE student_id=?",
                         (generate_password_hash(pw), s["student_id"]))
            log("Account activated", s["admission_number"], s["student_id"], "student")
            db().commit()
            flash("Account activated! Log in with your admission number.", "ok")
            return redirect("/login")
    return render_template("activate.html", adm=request.args.get("adm", ""))


@app.route("/logout")
def logout():
    was_admin = session.get("role") == "admin"
    if session.get("uid"):
        log("Logged out")
        db().commit()
    session.clear()
    return redirect(ADMIN_PATH if was_admin else "/login")


# ---------------------------------------------------------------- shared helpers
def student_row(uid):
    return db().execute(
        """SELECT s.*, dp.department_name FROM students s
           LEFT JOIN departments dp ON dp.department_id = s.department_id
           WHERE s.student_id=?""", (uid,)).fetchone()


def is_delegate(el, uid):
    return db().execute("SELECT 1 FROM delegates WHERE election_id=? AND student_id=?",
                        (el["election_id"], uid)).fetchone() is not None


def tally(election_id):
    """Stage 2: executive votes per position."""
    rows = db().execute("""
        SELECT p.position_id, p.position_name, c.candidate_id, c.student_id, s.full_name, c.photo_path,
               COUNT(v.vote_id) AS votes
        FROM positions p
        LEFT JOIN candidates c ON c.position_id = p.position_id AND c.status = 'approved'
        LEFT JOIN students s   ON s.student_id = c.student_id
        LEFT JOIN votes v      ON v.candidate_id = c.candidate_id AND v.election_id = ?
        GROUP BY p.position_id, p.position_name, c.candidate_id, c.student_id, s.full_name, c.photo_path, c.applied_at
        ORDER BY p.position_id, votes DESC, c.applied_at, c.candidate_id""", (election_id,)).fetchall()
    out = {}
    for r in rows:
        p = out.setdefault(r["position_id"], dict(id=r["position_id"], name=r["position_name"],
                                                  total=0, seats=1, candidates=[]))
        if r["candidate_id"]:
            p["candidates"].append(dict(id=r["candidate_id"], student_id=r["student_id"], name=r["full_name"],
                                        photo=r["photo_path"], votes=r["votes"]))
            p["total"] += r["votes"]
    return finish_groups(out)


def tally_delegates(election_id):
    """Stage 1: delegate votes per department."""
    rows = db().execute("""
        SELECT dp.department_id, dp.department_name, dp.delegate_seats,
               dc.delegate_candidate_id, dc.student_id, s.full_name, dc.photo_path,
               COUNT(v.dvote_id) AS votes
        FROM departments dp
        LEFT JOIN delegate_candidates dc ON dc.department_id = dp.department_id AND dc.status = 'approved'
        LEFT JOIN students s ON s.student_id = dc.student_id
        LEFT JOIN delegate_votes v ON v.delegate_candidate_id = dc.delegate_candidate_id AND v.election_id = ?
        GROUP BY dp.department_id, dp.department_name, dp.delegate_seats, dc.delegate_candidate_id, dc.student_id, s.full_name, dc.photo_path, dc.applied_at
        ORDER BY dp.department_name, votes DESC, dc.applied_at, dc.delegate_candidate_id""",
                         (election_id,)).fetchall()
    out = {}
    for r in rows:
        g = out.setdefault(r["department_id"], dict(id=r["department_id"], name=r["department_name"],
                                                    total=0, seats=r["delegate_seats"], candidates=[]))
        if r["delegate_candidate_id"]:
            g["candidates"].append(dict(id=r["delegate_candidate_id"], student_id=r["student_id"],
                                        name=r["full_name"], photo=r["photo_path"], votes=r["votes"]))
            g["total"] += r["votes"]
    return finish_groups(out)


def finish_groups(out):
    for g in out.values():
        for c in g["candidates"]:
            c["pct"] = round(100 * c["votes"] / g["total"]) if g["total"] else 0
    return list(out.values())


def stage_turnout(el):
    d, eid = db(), el["election_id"]
    if el["stage"] == "delegates":
        total = d.execute("SELECT COUNT(*) FROM students WHERE department_id IS NOT NULL").fetchone()[0]
        voted = d.execute("SELECT COUNT(DISTINCT student_id) FROM delegate_votes WHERE election_id=?",
                          (eid,)).fetchone()[0]
    else:
        total = d.execute("SELECT COUNT(*) FROM delegates WHERE election_id=?", (eid,)).fetchone()[0]
        voted = d.execute("SELECT COUNT(DISTINCT student_id) FROM votes WHERE election_id=?",
                          (eid,)).fetchone()[0]
    return dict(voters=voted, total=total, pct=round(100 * voted / total) if total else 0)


def exec_apply_open(el):
    """Executive applications: before voting starts, or while delegates haven't voted yet."""
    if el["status"] == "open" or el["stage"] == "declared":
        return False
    if el["stage"] == "executives":
        return db().execute("SELECT COUNT(*) FROM votes WHERE election_id=?",
                            (el["election_id"],)).fetchone()[0] == 0
    return True


def delegate_apply_open(el):
    return el["stage"] == "delegates" and el["status"] != "open"


# ---------------------------------------------------------------- results pages
@app.route("/results")
@need()
def results_page():
    if session["role"] != "admin" and not SHOW_LIVE:
        flash("Live counts stay hidden until the Dean's office declares the results.", "ok")
        return redirect("/leaders")
    return render_template("results.html")


def declared_delegates(election_id, dept_id=None):
    sql = """SELECT dl.department_id, dp.department_name, s.full_name, dc.photo_path, dl.votes
             FROM delegates dl
             JOIN students s ON s.student_id = dl.student_id
             JOIN departments dp ON dp.department_id = dl.department_id
             LEFT JOIN delegate_candidates dc ON dc.student_id = dl.student_id
             WHERE dl.election_id=?"""
    args = [election_id]
    if dept_id is not None:
        sql += " AND dl.department_id=?"
        args.append(dept_id)
    out = {}
    for r in db().execute(sql + " ORDER BY dp.department_name, dl.votes DESC, s.full_name", tuple(args)).fetchall():
        g = out.setdefault(r["department_id"], dict(id=r["department_id"], name=r["department_name"], members=[]))
        g["members"].append(dict(name=r["full_name"], photo=r["photo_path"], votes=r["votes"]))
    return list(out.values())


@app.route("/api/results")
@need()
def api_results():
    if session["role"] != "admin" and not SHOW_LIVE:
        abort(403)
    el = current_election()
    kind = "delegates" if el["stage"] == "delegates" else "executives"
    groups = tally_delegates(el["election_id"]) if kind == "delegates" else tally(el["election_id"])
    is_admin = session["role"] == "admin"
    if is_admin:
        shown = declared_delegates(el["election_id"])
    else:
        my_dept = student_row(session["uid"])["department_id"]
        if kind == "delegates":   # a student only sees the delegate candidates of their own department
            groups = [g_ for g_ in groups if my_dept and g_["id"] == my_dept]
        shown = declared_delegates(el["election_id"], my_dept if my_dept else -1)
        for g_ in groups:   # students must not see how many seats a department has
            g_["seats"] = None
    return jsonify(election=dict(title=el["title"], status=el["status"], stage=el["stage"]),
                   kind=kind, turnout=stage_turnout(el), scope="all" if is_admin else "own",
                   declared_delegates=shown,
                   total_votes=sum(g["total"] for g in groups), groups=groups)


@app.route("/leaders")
@need()
def leaders():
    d, el = db(), current_election()
    dept_only, dept_name = session["role"] != "admin", None
    sql = """SELECT dl.*, s.full_name, dp.department_name, dc.photo_path, dc.statement
             FROM delegates dl
             JOIN students s ON s.student_id = dl.student_id
             JOIN departments dp ON dp.department_id = dl.department_id
             LEFT JOIN delegate_candidates dc ON dc.student_id = dl.student_id
             WHERE dl.election_id=?"""
    args = [el["election_id"]]
    if dept_only:   # a student only sees the delegates of their own department
        me = student_row(session["uid"])
        dept_name = me["department_name"]
        sql += " AND dl.department_id=?"
        args.append(me["department_id"] if me["department_id"] else -1)
    dels = d.execute(sql + " ORDER BY dp.department_name, dl.votes DESC, s.full_name", tuple(args)).fetchall()
    groups = {}
    for r in dels:
        groups.setdefault(r["department_name"], []).append(r)
    execs = d.execute("""SELECT l.*, p.position_name, s.full_name, c.photo_path, c.manifesto
                         FROM leaders l JOIN positions p ON p.position_id = l.position_id
                         JOIN candidates c ON c.candidate_id = l.candidate_id
                         JOIN students s ON s.student_id = c.student_id
                         WHERE l.election_id=? ORDER BY l.position_id""", (el["election_id"],)).fetchall()
    vacant = []
    if el["stage"] == "declared":
        have = {r["position_id"] for r in execs}
        vacant = [p["position_name"] for p in d.execute("SELECT * FROM positions ORDER BY position_id")
                  if p["position_id"] not in have]
    return render_template("leaders.html", groups=groups, execs=execs, vacant=vacant,
                           dept_only=dept_only, dept_name=dept_name)


# ---------------------------------------------------------------- student pages
@app.route("/dashboard")
@need("student")
def dashboard():
    d, uid, el = db(), session["uid"], current_election()
    me = student_row(uid)
    positions = d.execute("SELECT * FROM positions ORDER BY position_id").fetchall()
    is_del = is_delegate(el, uid)
    mine = {r["position_id"]: r["full_name"] for r in d.execute(
        """SELECT v.position_id, s.full_name FROM votes v
           JOIN candidates c ON c.candidate_id = v.candidate_id
           JOIN students s ON s.student_id = c.student_id
           WHERE v.student_id=? AND v.election_id=?""", (uid, el["election_id"]))}
    dvote = d.execute("""SELECT s.full_name FROM delegate_votes v
                         JOIN delegate_candidates dc ON dc.delegate_candidate_id = v.delegate_candidate_id
                         JOIN students s ON s.student_id = dc.student_id
                         WHERE v.student_id=? AND v.election_id=?""", (uid, el["election_id"])).fetchone()
    app_row = d.execute(
        """SELECT c.*, p.position_name FROM candidates c JOIN positions p USING(position_id)
           WHERE c.student_id=? ORDER BY (c.status='rejected'), c.candidate_id DESC""", (uid,)).fetchone()
    dapp_row = d.execute("SELECT * FROM delegate_candidates WHERE student_id=?", (uid,)).fetchone()
    n_dcands = d.execute("""SELECT COUNT(*) FROM delegate_candidates
                            WHERE status='approved' AND department_id=?""", (me["department_id"],)).fetchone()[0]
    n_cands = d.execute("SELECT COUNT(*) FROM candidates WHERE status='approved'").fetchone()[0]
    return render_template("dashboard.html", me=me, positions=positions, mine=mine, is_del=is_del,
                           dvote=dvote, app_row=app_row, dapp_row=dapp_row, n_cands=n_cands,
                           n_dcands=n_dcands, t=stage_turnout(el))


@app.route("/ballot")
@need("student")
def ballot():
    d, uid, el = db(), session["uid"], current_election()
    if el["stage"] == "declared":
        return redirect("/leaders")
    me = student_row(uid)
    if el["stage"] == "delegates":
        rows = d.execute("""SELECT dc.*, s.full_name, s.admission_number, dp.department_name, dp.delegate_seats
                            FROM delegate_candidates dc
                            JOIN students s ON s.student_id = dc.student_id
                            JOIN departments dp ON dp.department_id = dc.department_id
                            WHERE dc.status='approved' AND dc.department_id = ?
                            ORDER BY s.full_name""",
                          (me["department_id"],)).fetchall() if me["department_id"] else []
        groups = []
        for r in rows:   # only the student's own department
            if not groups or groups[-1]["name"] != r["department_name"]:
                groups.append(dict(name=r["department_name"], seats=r["delegate_seats"], cands=[]))
            groups[-1]["cands"].append(r)
        my_vote = d.execute("SELECT delegate_candidate_id FROM delegate_votes WHERE student_id=? AND election_id=?",
                            (uid, el["election_id"])).fetchone()
        return render_template("ballot.html", mode="delegates", me=me, groups=groups,
                               my_vote=my_vote[0] if my_vote else None)
    positions = d.execute("SELECT * FROM positions ORDER BY position_id").fetchall()
    cands = {}
    for c in d.execute("""SELECT c.*, s.full_name, s.admission_number FROM candidates c
                          JOIN students s ON s.student_id = c.student_id
                          WHERE c.status='approved' ORDER BY s.full_name"""):
        cands.setdefault(c["position_id"], []).append(c)
    mine = {r["position_id"]: r["candidate_id"] for r in d.execute(
        "SELECT position_id, candidate_id FROM votes WHERE student_id=? AND election_id=?",
        (uid, el["election_id"]))}
    return render_template("ballot.html", mode="executives", me=me, is_del=is_delegate(el, uid),
                           positions=positions, cands=cands, mine=mine)


@app.route("/vote/delegate", methods=["POST"])
@need("student")
def vote_delegate():
    d, uid, el = db(), session["uid"], current_election()
    me = student_row(uid)
    try:
        cid = int(request.form.get("candidate_id", ""))
    except ValueError:
        flash("Pick a candidate first.", "err")
        return redirect("/ballot")
    c = d.execute("SELECT * FROM delegate_candidates WHERE delegate_candidate_id=? AND status='approved'",
                  (cid,)).fetchone()
    if el["stage"] != "delegates" or el["status"] != "open":
        flash("Delegate voting isn't open right now.", "err")
    elif not me["department_id"]:
        flash("Your department isn't set yet. Ask the Dean's office.", "err")
    elif not c or c["department_id"] != me["department_id"]:
        flash("You can only vote for delegates from your own department.", "err")
    else:
        try:  # UNIQUE(student_id, election_id): one delegate vote per student
            d.execute("INSERT INTO delegate_votes (student_id, delegate_candidate_id, election_id) VALUES (?,?,?)",
                      (uid, cid, el["election_id"]))
            log("Delegate vote cast", f"Voter department: {me['department_name'] or '-'} (election #{el['election_id']})")
            d.commit()
            flash("Your delegate vote is recorded. Thank you!", "ok")
        except INTEGRITY_ERRORS:
            d.rollback()
            flash("You already cast your delegate vote.", "err")
    return redirect("/ballot")


@app.route("/vote", methods=["POST"])
@need("student")
def vote():
    d, uid, el = db(), session["uid"], current_election()
    try:
        cid = int(request.form.get("candidate_id", ""))
    except ValueError:
        flash("Pick a candidate first.", "err")
        return redirect("/ballot")
    c = d.execute("SELECT * FROM candidates WHERE candidate_id=? AND status='approved'", (cid,)).fetchone()
    if el["stage"] != "executives" or el["status"] != "open":
        flash("Executive voting isn't open right now.", "err")
    elif not is_delegate(el, uid):
        flash("Only elected delegates vote for the executives.", "err")
    elif not c:
        flash("That candidate isn't on the ballot.", "err")
    else:
        try:  # UNIQUE(student_id, position_id, election_id) blocks double voting in the database
            d.execute("INSERT INTO votes (student_id, candidate_id, position_id, election_id) VALUES (?,?,?,?)",
                      (uid, c["candidate_id"], c["position_id"], el["election_id"]))
            pos = d.execute("SELECT position_name FROM positions WHERE position_id=?",
                            (c["position_id"],)).fetchone()[0]
            log("Vote cast", f"Position: {pos} (election #{el['election_id']})")
            d.commit()
            flash(f"Your vote for {pos} is recorded. Thank you!", "ok")
        except INTEGRITY_ERRORS:
            d.rollback()
            flash("You already voted for this position.", "err")
    return redirect("/ballot")


@app.route("/vote/batch", methods=["POST"])
@need("student")
def vote_batch():
    """A delegate chooses a candidate for each position and casts all the votes with one button."""
    d, uid, el = db(), session["uid"], current_election()
    if el["stage"] != "executives" or el["status"] != "open":
        flash("Executive voting isn't open right now.", "err")
        return redirect("/ballot")
    if not is_delegate(el, uid):
        flash("Only elected delegates vote for the executives.", "err")
        return redirect("/ballot")
    picks = {}
    for key, val in request.form.items():
        if key.startswith("pos_"):
            try:
                picks[int(key[4:])] = int(val)
            except ValueError:
                continue
    if not picks:
        flash("Choose at least one candidate first.", "err")
        return redirect("/ballot")
    names = {r["position_id"]: r["position_name"] for r in d.execute("SELECT position_id, position_name FROM positions")}
    already = {r["position_id"] for r in d.execute(
        "SELECT position_id FROM votes WHERE student_id=? AND election_id=?", (uid, el["election_id"]))}
    todo, skipped = [], []
    for pid, cid in picks.items():   # check everything first so a bad request saves nothing
        c = d.execute("SELECT * FROM candidates WHERE candidate_id=? AND status='approved'", (cid,)).fetchone()
        if pid not in names or not c or c["position_id"] != pid:
            flash("One of the choices isn't on the ballot. Reload the page and try again.", "err")
            return redirect("/ballot")
        (skipped if pid in already else todo).append((pid, cid))
    if not todo:
        flash("You already voted for those positions.", "err")
        return redirect("/ballot")
    try:   # UNIQUE(student_id, position_id, election_id) still blocks double voting in the database
        for pid, cid in todo:
            d.execute("INSERT INTO votes (student_id, candidate_id, position_id, election_id) VALUES (?,?,?,?)",
                      (uid, cid, pid, el["election_id"]))
        done = ", ".join(names[p] for p, _ in todo)
        log("Votes cast", f"Positions: {done} (election #{el['election_id']})")
        d.commit()
        flash(f"Your votes are recorded for: {done}. Thank you!" +
              (f" ({len(skipped)} position(s) were already voted.)" if skipped else ""), "ok")
    except INTEGRITY_ERRORS:
        d.rollback()
        flash("Some of your votes were already recorded. Reload the page to see your ballot.", "err")
    return redirect("/ballot")


# ---------------------------------------------------------------- contact us (feedback to the Dean's office)
FEEDBACK_TYPES = [("feedback", "Feedback"), ("suggestion", "Suggestion"), ("concern", "Concern"), ("other", "Something else")]


@app.route("/contact", methods=["GET", "POST"])
@need("student")
def contact():
    me = student_row(session["uid"])
    if request.method == "POST":
        if request.form.get("website"):   # hidden field only bots fill in: pretend it worked
            flash("Thank you! Your message was sent to the Dean's office.", "ok")
            return redirect("/contact")
        now = time.time()
        recent = [t for t in session.get("fb_times", []) if now - t < 3600]
        category = request.form.get("category", "other")
        if category not in dict(FEEDBACK_TYPES):
            category = "other"
        message = request.form.get("message", "").strip()
        name = request.form.get("sender_name", "").strip()[:150]
        adm = request.form.get("sender_admission", "").strip()[:40]
        anon = bool(request.form.get("anonymous")) or not (name or adm)
        errors = []
        if not 10 <= len(message) <= 2000:
            errors.append("Please write between 10 and 2000 characters.")
        if len(recent) >= 5:
            errors.append("You have sent several messages in the last hour. Please try again a little later.")
        if errors:
            for e in errors:
                flash(e, "err")
            return render_template("contact.html", types=FEEDBACK_TYPES, me=me, form=request.form)
        d = db()
        if anon:   # no name, no admission number, no student id, and the time is rounded to the hour
            d.execute("""INSERT INTO feedback (category, message, is_anonymous, created_at)
                         VALUES (?,?,1,?)""", (category, message, datetime.utcnow().strftime("%Y-%m-%d %H:00:00")))
            # deliberately NOT written to the audit log: its exact time could be matched with who was logged in
        else:
            d.execute("""INSERT INTO feedback (category, message, is_anonymous, sender_name, sender_admission)
                         VALUES (?,?,0,?,?)""", (category, message, name or None, adm or None))
            log("Feedback received", f"from {name or adm}")
        d.commit()
        session["fb_times"] = recent + [now]
        flash("Thank you! Your message was sent to the Dean's office." +
              (" It was sent anonymously." if anon else ""), "ok")
        return redirect("/contact")
    return render_template("contact.html", types=FEEDBACK_TYPES, me=me, form=None)


@app.route("/apply", methods=["GET", "POST"])
@need("student")
def apply():
    d, uid, el = db(), session["uid"], current_election()
    positions = d.execute("SELECT * FROM positions ORDER BY position_id").fetchall()
    mine = d.execute(
        """SELECT c.*, p.position_name FROM candidates c JOIN positions p USING(position_id)
           WHERE c.student_id=? ORDER BY (c.status='rejected'), c.candidate_id DESC""", (uid,)).fetchone()
    has_delegate_app = d.execute("SELECT 1 FROM delegate_candidates WHERE student_id=?", (uid,)).fetchone() is not None
    can_apply = exec_apply_open(el) and not has_delegate_app

    if request.method == "POST":
        errors = []
        if has_delegate_app:
            errors.append("You applied as a delegate, so you can't apply for an executive position.")
        elif not exec_apply_open(el):
            errors.append("Executive applications are closed right now.")
        try:
            pid = int(request.form.get("position_id", ""))
            if not d.execute("SELECT 1 FROM positions WHERE position_id=?", (pid,)).fetchone():
                raise ValueError
        except ValueError:
            pid = None
            errors.append("Choose the position you are running for.")
        manifesto = request.form.get("manifesto", "").strip()
        if not 30 <= len(manifesto) <= 1500:
            errors.append("Your manifesto should be between 30 and 1500 characters.")
        if not request.form.get("fee"):
            errors.append("Confirm that your fees are cleared. The Dean's office will verify it.")

        same = None
        if pid:
            same = d.execute("SELECT * FROM candidates WHERE student_id=? AND position_id=?",
                             (uid, pid)).fetchone()
            other = d.execute("""SELECT COUNT(*) FROM candidates
                                 WHERE student_id=? AND position_id!=? AND status!='rejected'""",
                              (uid, pid)).fetchone()[0]
            if other:
                errors.append("You already have an active application for another position. One at a time.")
            if same and same["status"] == "approved":
                errors.append("Your application is approved, so it can't be edited now.")

        photo, e1 = save_upload(request.files.get("photo"), "photo", PHOTO_EXT)
        results, e2 = save_upload(request.files.get("results"), "results", RESULTS_EXT)
        errors += [e for e in (e1, e2) if e]
        if pid and not (same and same["photo_path"]) and not photo and not e1:
            errors.append("Upload a clear photo of yourself.")
        if pid and not (same and same["results_path"]) and not results and not e2:
            errors.append("Upload your academic results (PDF or image).")

        if errors:
            remove_upload(photo)
            remove_upload(results)
            for e in errors:
                flash(e, "err")
            return render_template("apply.html", positions=positions, mine=mine, form=request.form,
                                   can_apply=can_apply, has_delegate_app=has_delegate_app)

        if same:
            if photo:
                remove_upload(same["photo_path"])
            if results:
                remove_upload(same["results_path"])
            d.execute("""UPDATE candidates SET manifesto=?, photo_path=?, results_path=?, fee_compliance=1,
                         status='pending', applied_at=CURRENT_TIMESTAMP WHERE candidate_id=?""",
                      (manifesto, photo or same["photo_path"], results or same["results_path"],
                       same["candidate_id"]))
            log("Candidate application updated")
        else:
            d.execute("""INSERT INTO candidates (student_id, position_id, manifesto, photo_path,
                         results_path, fee_compliance) VALUES (?,?,?,?,?,1)""",
                      (uid, pid, manifesto, photo, results))
            log("Candidate application submitted")
        d.commit()
        flash("Application saved. The Dean's office will review it soon.", "ok")
        return redirect("/apply")
    return render_template("apply.html", positions=positions, mine=mine, form=None, can_apply=can_apply,
                           has_delegate_app=has_delegate_app)


@app.route("/apply/delegate", methods=["GET", "POST"])
@need("student")
def apply_delegate():
    d, uid, el = db(), session["uid"], current_election()
    me = student_row(uid)
    mine = d.execute("SELECT * FROM delegate_candidates WHERE student_id=?", (uid,)).fetchone()
    can_apply = delegate_apply_open(el) and bool(me["department_id"])

    if request.method == "POST":
        errors = []
        if not me["department_id"]:
            errors.append("Your department isn't set yet. Ask the Dean's office to add it.")
        elif not delegate_apply_open(el):
            errors.append("Delegate applications are closed right now.")
        statement = request.form.get("statement", "").strip()
        if not 30 <= len(statement) <= 800:
            errors.append("Your statement should be between 30 and 800 characters.")
        if mine and mine["status"] == "approved":
            errors.append("Your application is approved, so it can't be edited now.")
        photo, e1 = save_upload(request.files.get("photo"), "photo", PHOTO_EXT)
        if e1:
            errors.append(e1)
        if not (mine and mine["photo_path"]) and not photo and not e1:
            errors.append("Upload a clear photo of yourself.")
        if errors:
            remove_upload(photo)
            for e in errors:
                flash(e, "err")
            return render_template("apply_delegate.html", me=me, mine=mine, form=request.form,
                                   can_apply=can_apply)
        if mine:
            if photo:
                remove_upload(mine["photo_path"])
            d.execute("""UPDATE delegate_candidates SET statement=?, photo_path=?, department_id=?,
                         status='pending', applied_at=CURRENT_TIMESTAMP WHERE delegate_candidate_id=?""",
                      (statement, photo or mine["photo_path"], me["department_id"],
                       mine["delegate_candidate_id"]))
            log("Delegate application updated")
        else:
            d.execute("""INSERT INTO delegate_candidates (student_id, department_id, statement, photo_path)
                         VALUES (?,?,?,?)""", (uid, me["department_id"], statement, photo))
            log("Delegate application submitted", me["department_name"])
        d.commit()
        flash("Delegate application saved. The Dean's office will review it soon.", "ok")
        return redirect("/apply/delegate")
    return render_template("apply_delegate.html", me=me, mine=mine, form=None, can_apply=can_apply)


# ---------------------------------------------------------------- admin
@app.route("/admin")
@need("admin")
def admin():
    d, el = db(), current_election()
    cands = d.execute("""SELECT c.*, s.full_name, s.admission_number, s.email, p.position_name
                         FROM candidates c JOIN students s ON s.student_id=c.student_id
                         JOIN positions p ON p.position_id=c.position_id
                         ORDER BY CASE c.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
                                  p.position_id, c.applied_at""").fetchall()
    dcands = d.execute("""SELECT dc.*, s.full_name, s.admission_number, dp.department_name
                          FROM delegate_candidates dc JOIN students s ON s.student_id=dc.student_id
                          JOIN departments dp ON dp.department_id=dc.department_id
                          ORDER BY CASE dc.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
                                   dp.department_name, dc.applied_at""").fetchall()
    voters = d.execute("""SELECT s.*, dp.department_name,
                            (SELECT COUNT(*) FROM votes v WHERE v.student_id=s.student_id AND v.election_id=?) AS n_votes,
                            EXISTS(SELECT 1 FROM delegate_votes dv WHERE dv.student_id=s.student_id AND dv.election_id=?) AS dvoted,
                            EXISTS(SELECT 1 FROM delegates dl WHERE dl.student_id=s.student_id AND dl.election_id=?) AS is_del
                          FROM students s LEFT JOIN departments dp ON dp.department_id=s.department_id
                          ORDER BY s.admission_number""",
                        (el["election_id"],) * 3).fetchall()
    departments = d.execute("""SELECT dp.*, (SELECT COUNT(*) FROM students s WHERE s.department_id=dp.department_id) AS n_students
                               FROM departments dp ORDER BY dp.department_name""").fetchall()
    audit = d.execute("""SELECT a.*, COALESCE(s.full_name, ad.full_name) AS who FROM audit_log a
                         LEFT JOIN students s ON a.actor_role='student' AND s.student_id=a.actor_id
                         LEFT JOIN admins ad ON a.actor_role='admin' AND ad.admin_id=a.actor_id
                         ORDER BY a.log_id DESC LIMIT 80""").fetchall()
    stats = dict(
        voters=len(voters),
        activated=sum(1 for v in voters if v["password"]),
        pending=sum(1 for c in cands if c["status"] == "pending") + sum(1 for c in dcands if c["status"] == "pending"),
        approved=sum(1 for c in cands if c["status"] == "approved"),
        dapproved=sum(1 for c in dcands if c["status"] == "approved"),
        delegates=sum(1 for v in voters if v["is_del"]),
        votes=(d.execute("SELECT COUNT(*) FROM delegate_votes WHERE election_id=?", (el["election_id"],)).fetchone()[0]
               if el["stage"] == "delegates" else
               d.execute("SELECT COUNT(*) FROM votes WHERE election_id=?", (el["election_id"],)).fetchone()[0]),
        t=stage_turnout(el),
        new_feedback=d.execute("SELECT COUNT(*) FROM feedback WHERE status='new'").fetchone()[0])
    feedback = d.execute("""SELECT * FROM feedback ORDER BY CASE status WHEN 'new' THEN 0 WHEN 'read' THEN 1 ELSE 2 END,
                            feedback_id DESC LIMIT 500""").fetchall()
    return render_template("admin.html", feedback=feedback, cands=cands, dcands=dcands, voters=voters, departments=departments,
                           audit=audit, stats=stats,
                           n_positions=d.execute("SELECT COUNT(*) FROM positions").fetchone()[0])


def tie_text(label, names):
    return f"{label}: {', '.join(names)}"


def declare_delegates(el, force, seats_override=None, replace=False):
    """Pick the winners of each department (its top vote-getters, up to its seats).
    replace=True re-does an earlier declaration (used by 'Re-declare winners')."""
    d = db()
    if d.execute("SELECT COUNT(*) FROM delegate_votes WHERE election_id=?", (el["election_id"],)).fetchone()[0] == 0:
        return False, "No delegate votes have been cast yet."
    ties, winners = [], []
    for g in tally_delegates(el["election_id"]):
        cs, seats = g["candidates"], (seats_override or g["seats"])
        if not cs:
            continue
        if len(cs) > seats and cs[seats - 1]["votes"] == cs[seats]["votes"]:
            line = cs[seats - 1]["votes"]
            ties.append(tie_text(g["name"], [c["name"] for c in cs if c["votes"] == line]))
        winners += [(g["id"], c) for c in cs[:seats]]
    if ties and not force:
        return False, ("Tie for the last seat in: " + "; ".join(ties) +
                       ". Tick 'break ties by earliest application' to continue.")
    if replace:   # votes, students and applications are never touched, only the list of declared delegates
        d.execute("DELETE FROM delegates WHERE election_id=?", (el["election_id"],))
    if seats_override:
        d.execute("UPDATE departments SET delegate_seats=?", (seats_override,))
    for dept_id, c in winners:
        d.execute("""INSERT OR IGNORE INTO delegates (election_id, student_id, department_id, votes)
                     VALUES (?,?,?,?)""", (el["election_id"], c["student_id"], dept_id, c["votes"]))
    d.execute("UPDATE elections SET stage='executives', status='closed' WHERE election_id=?", (el["election_id"],))
    log("Delegates re-declared" if replace else "Delegates declared",
        f"{len(winners)} delegates" + (f", {seats_override} per department" if seats_override else "") +
        (" (ties broken by earliest application: " + "; ".join(ties) + ")" if ties else ""))
    return True, (f"{len(winners)} delegates " + ("re-declared" if replace else "declared") +
                  " from the votes. Executive voting is next.")


def declare_leaders(el, force):
    d = db()
    if d.execute("SELECT COUNT(*) FROM votes WHERE election_id=?", (el["election_id"],)).fetchone()[0] == 0:
        return False, "No executive votes have been cast yet."
    ties, winners, vacant = [], [], []
    for p in tally(el["election_id"]):
        cs = p["candidates"]
        if not cs or cs[0]["votes"] == 0:
            vacant.append(p["name"])
            continue
        if len(cs) > 1 and cs[1]["votes"] == cs[0]["votes"]:
            ties.append(tie_text(p["name"], [c["name"] for c in cs if c["votes"] == cs[0]["votes"]]))
        winners.append((p["id"], cs[0]))
    if ties and not force:
        return False, ("Tie for: " + "; ".join(ties) +
                       ". Tick 'break ties by earliest application' to continue.")
    for pid, c in winners:
        d.execute("""INSERT OR REPLACE INTO leaders (election_id, position_id, candidate_id, votes)
                     VALUES (?,?,?,?)""", (el["election_id"], pid, c["id"], c["votes"]))
    d.execute("UPDATE elections SET stage='declared', status='closed', end_time=CURRENT_TIMESTAMP WHERE election_id=?",
              (el["election_id"],))
    log("Leaders declared", f"{len(winners)} elected" + (f"; no winner for: {', '.join(vacant)}" if vacant else ""))
    return True, (f"{len(winners)} leaders declared." +
                  (f" No votes for: {', '.join(vacant)}." if vacant else ""))


@app.route("/admin/election", methods=["POST"])
@need("admin")
def admin_election():
    d, el, action = db(), current_election(), request.form.get("action")
    if action == "open":
        if el["stage"] == "declared":
            flash("This election is finished. Start a new election below.", "err")
        elif el["stage"] == "delegates" and not d.execute(
                "SELECT 1 FROM delegate_candidates WHERE status='approved'").fetchone():
            flash("Approve at least one delegate candidate before opening delegate voting.", "err")
        elif el["stage"] == "executives" and not d.execute(
                "SELECT 1 FROM candidates WHERE status='approved'").fetchone():
            flash("Approve at least one executive candidate before opening executive voting.", "err")
        else:
            d.execute("""UPDATE elections SET status='open', start_time=COALESCE(start_time, CURRENT_TIMESTAMP),
                         end_time=NULL WHERE election_id=?""", (el["election_id"],))
            log("Voting opened", f"{el['title']} - stage: {el['stage']}")
            flash("Voting is open for this stage.", "ok")
    elif action == "pause":
        d.execute("UPDATE elections SET status='paused' WHERE election_id=?", (el["election_id"],))
        log("Voting paused", f"stage: {el['stage']}")
        flash("Voting paused.", "ok")
    elif action == "close":
        d.execute("UPDATE elections SET status='closed' WHERE election_id=?", (el["election_id"],))
        log("Voting closed", f"stage: {el['stage']}")
        flash("Voting closed. You can now declare the results of this stage.", "ok")
    elif action == "declare":
        force = bool(request.form.get("force"))
        if el["status"] != "closed":
            flash("Stop the voting first, then declare.", "err")
        elif el["stage"] == "delegates":
            ok_, msg = declare_delegates(el, force)
            flash(msg, "ok" if ok_ else "err")
        elif el["stage"] == "executives":
            ok_, msg = declare_leaders(el, force)
            flash(msg, "ok" if ok_ else "err")
        else:
            flash("Results are already declared.", "err")
    elif action == "redeclare":
        try:
            n = int(request.form.get("seats", "1"))
            if not 1 <= n <= 50:
                raise ValueError
        except ValueError:
            n = None
            flash("Delegates per department must be a number from 1 to 50.", "err")
        if n is None:
            pass
        elif el["stage"] != "executives":
            flash("Delegates can only be re-declared after they were declared and before the leaders are declared.", "err")
        elif el["status"] == "open":
            flash("Stop the voting first, then re-declare.", "err")
        elif d.execute("SELECT COUNT(*) FROM votes WHERE election_id=?", (el["election_id"],)).fetchone()[0]:
            flash("Executive votes were already cast, so the delegates can no longer be changed.", "err")
        else:
            ok_, msg = declare_delegates(el, bool(request.form.get("force")), seats_override=n, replace=True)
            flash(msg, "ok" if ok_ else "err")
    elif action == "new":
        title = request.form.get("title", "").strip()
        if not title:
            flash("Give the new election a title.", "err")
        elif el["status"] == "open":
            flash("Close the current election first.", "err")
        else:
            d.execute("INSERT INTO elections (title, status, stage) VALUES (?, 'closed', 'delegates')", (title,))
            log("New election created", title)
            flash("New election created. Votes start from zero.", "ok")
    d.commit()
    return redirect("/admin")


@app.route("/admin/candidate/<int:cid>/<decision>", methods=["POST"])
@need("admin")
def admin_candidate(cid, decision):
    d = db()
    c = d.execute("""SELECT c.*, s.full_name FROM candidates c JOIN students s USING(student_id)
                     WHERE candidate_id=?""", (cid,)).fetchone()
    if not c:
        abort(404)
    if decision == "fee":
        d.execute("UPDATE candidates SET fee_compliance=? WHERE candidate_id=?",
                  (0 if c["fee_compliance"] else 1, cid))
        log("Fee compliance toggled", c["full_name"])
    elif decision in ("approve", "reject"):
        if decision == "approve" and not c["fee_compliance"]:
            flash("Mark fee compliance as verified before approving.", "err")
            return redirect("/admin#candidates")
        d.execute("UPDATE candidates SET status=? WHERE candidate_id=?",
                  ("approved" if decision == "approve" else "rejected", cid))
        log(f"Candidate {decision}d", c["full_name"])
        flash(f"{c['full_name']} {decision}d.", "ok")
    d.commit()
    return redirect("/admin#candidates")


@app.route("/admin/delegate/<int:cid>/<decision>", methods=["POST"])
@need("admin")
def admin_delegate(cid, decision):
    d = db()
    c = d.execute("""SELECT dc.*, s.full_name FROM delegate_candidates dc JOIN students s USING(student_id)
                     WHERE delegate_candidate_id=?""", (cid,)).fetchone()
    if not c or decision not in ("approve", "reject"):
        abort(404)
    d.execute("UPDATE delegate_candidates SET status=? WHERE delegate_candidate_id=?",
              ("approved" if decision == "approve" else "rejected", cid))
    log(f"Delegate candidate {decision}d", c["full_name"])
    d.commit()
    flash(f"{c['full_name']} {decision}d as a delegate candidate.", "ok")
    return redirect("/admin#delegates")


@app.route("/admin/department", methods=["POST"])
@need("admin")
def admin_department():
    name = request.form.get("name", "").strip()
    try:
        seats = int(request.form.get("seats", "1"))
        if not 1 <= seats <= 50:
            raise ValueError
    except ValueError:
        flash("Seats must be a number from 1 to 50.", "err")
        return redirect("/admin#departments")
    if not name:
        flash("Give the department a name.", "err")
    else:
        try:
            db().execute("INSERT INTO departments (department_name, delegate_seats) VALUES (?,?)", (name, seats))
            log("Department added", f"{name} ({seats} seats)")
            db().commit()
            flash(f"{name} added.", "ok")
        except INTEGRITY_ERRORS:
            flash("That department already exists.", "err")
    return redirect("/admin#departments")


@app.route("/admin/department/<int:did>/seats", methods=["POST"])
@need("admin")
def admin_seats(did):
    el = current_election()
    try:
        seats = int(request.form.get("seats", ""))
        if not 1 <= seats <= 50:
            raise ValueError
    except ValueError:
        flash("Seats must be a number from 1 to 50.", "err")
        return redirect("/admin#departments")
    if el["stage"] != "delegates" or el["status"] == "open":
        flash("Seats can only change before delegate voting starts.", "err")
    else:
        db().execute("UPDATE departments SET delegate_seats=? WHERE department_id=?", (seats, did))
        log("Delegate seats changed", f"department #{did}: {seats}")
        db().commit()
        flash("Seats updated.", "ok")
    return redirect("/admin#departments")


@app.route("/admin/student", methods=["POST"])
@need("admin")
def admin_student():
    f = request.form
    vals = [f.get(k, "").strip() for k in ("admission_number", "full_name", "email", "phone_number")]
    dept = f.get("department_id") or None
    if not all(vals):
        flash("Fill in every student field.", "err")
    else:
        try:
            db().execute("""INSERT INTO students (admission_number, full_name, email, phone_number, department_id)
                            VALUES (?,?,?,?,?)""", vals + [dept])
            log("Student added", vals[0])
            db().commit()
            flash(f"{vals[1]} added to the voter list.", "ok")
        except INTEGRITY_ERRORS:
            db().rollback()
            flash("That admission number or email already exists, or the department is invalid.", "err")
    return redirect("/admin#voters")


@app.route("/admin/student/<int:sid>/department", methods=["POST"])
@need("admin")
def admin_student_dept(sid):
    d, el = db(), current_election()
    s = d.execute("SELECT * FROM students WHERE student_id=?", (sid,)).fetchone()
    if not s:
        abort(404)
    dept = request.form.get("department_id") or None
    if d.execute("SELECT 1 FROM delegate_votes WHERE student_id=? AND election_id=?",
                 (sid, el["election_id"])).fetchone():
        flash("This student already cast a delegate vote, so the department is locked.", "err")
    else:
        try:
            d.execute("UPDATE students SET department_id=? WHERE student_id=?", (dept, sid))
            log("Student department changed", s["admission_number"])
            d.commit()
            flash(f"Department updated for {s['full_name']}.", "ok")
        except INTEGRITY_ERRORS:
            d.rollback()
            flash("That department doesn't exist.", "err")
    return redirect("/admin#voters")


@app.route("/admin/student/<int:sid>/reset", methods=["POST"])
@need("admin")
def admin_reset(sid):
    s = db().execute("SELECT * FROM students WHERE student_id=?", (sid,)).fetchone()
    if not s:
        abort(404)
    db().execute("UPDATE students SET password=NULL WHERE student_id=?", (sid,))
    log("Student password reset", s["admission_number"])
    db().commit()
    flash(f"{s['full_name']} can activate a new password now.", "ok")
    return redirect("/admin#voters")


@app.route("/admin/feedback/<int:fid>/<action>", methods=["POST"])
@need("admin")
def admin_feedback(fid, action):
    d = db()
    if action not in ("new", "read", "resolved", "delete") or not d.execute(
            "SELECT 1 FROM feedback WHERE feedback_id=?", (fid,)).fetchone():
        abort(404)
    if action == "delete":
        d.execute("DELETE FROM feedback WHERE feedback_id=?", (fid,))
        log("Feedback deleted", f"#{fid}")
        flash("Message deleted.", "ok")
    else:
        d.execute("UPDATE feedback SET status=? WHERE feedback_id=?", (action, fid))
        log("Feedback status changed", f"#{fid} -> {action}")
    d.commit()
    return redirect("/admin#feedback")


@app.route("/admin/feedback.csv")
@need("admin")
def feedback_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ID", "Received (UTC)", "Type", "From", "Admission number (as typed, not verified)", "Status", "Message"])
    for r in db().execute("SELECT * FROM feedback ORDER BY feedback_id"):
        w.writerow([r["feedback_id"], r["created_at"], r["category"],
                    "Anonymous" if r["is_anonymous"] else (r["sender_name"] or ""),
                    "" if r["is_anonymous"] else (r["sender_admission"] or ""), r["status"], r["message"]])
    log("Feedback downloaded")
    db().commit()
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=feedback.csv"})


@app.route("/admin/password", methods=["POST"])
@need("admin")
def admin_password():
    a = db().execute("SELECT * FROM admins WHERE admin_id=?", (session["uid"],)).fetchone()
    old, new = request.form.get("old", ""), request.form.get("new", "")
    if not check_password_hash(a["password"], old):
        flash("Your current password is wrong.", "err")
    elif len(new) < 6:
        flash("New password needs at least 6 characters.", "err")
    else:
        db().execute("UPDATE admins SET password=? WHERE admin_id=?",
                     (generate_password_hash(new), a["admin_id"]))
        log("Admin password changed")
        db().commit()
        flash("Password changed.", "ok")
    return redirect("/admin#settings")


@app.route("/admin/report.csv")
@need("admin")
def report():
    el = current_election()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([el["title"], "stage: " + el["stage"], "status: " + el["status"]])
    w.writerow([])
    w.writerow(["STAGE 1 - DELEGATE ELECTION (every student votes for any delegate candidate)"])
    w.writerow(["Department", "Candidate", "Votes", "Share %", "Seats"])
    for g in tally_delegates(el["election_id"]):
        for c in g["candidates"]:
            w.writerow([g["name"], c["name"], c["votes"], c["pct"], g["seats"]])
    w.writerow([])
    w.writerow(["DECLARED DELEGATES"])
    for r in db().execute("""SELECT dp.department_name, s.full_name, dl.votes FROM delegates dl
                             JOIN students s ON s.student_id=dl.student_id
                             JOIN departments dp ON dp.department_id=dl.department_id
                             WHERE dl.election_id=? ORDER BY dp.department_name""", (el["election_id"],)):
        w.writerow([r[0], r[1], r[2]])
    w.writerow([])
    w.writerow(["STAGE 2 - EXECUTIVE ELECTION (delegates vote)"])
    w.writerow(["Position", "Candidate", "Votes", "Share %"])
    for p in tally(el["election_id"]):
        for c in p["candidates"]:
            w.writerow([p["name"], c["name"], c["votes"], c["pct"]])
    w.writerow([])
    w.writerow(["DECLARED LEADERS"])
    for r in db().execute("""SELECT p.position_name, s.full_name, l.votes FROM leaders l
                             JOIN positions p ON p.position_id=l.position_id
                             JOIN candidates c ON c.candidate_id=l.candidate_id
                             JOIN students s ON s.student_id=c.student_id
                             WHERE l.election_id=? ORDER BY l.position_id""", (el["election_id"],)):
        w.writerow([r[0], r[1], r[2]])
    log("Report downloaded")
    db().commit()
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=election-report.csv"})


app.secret_key = init_db()

if __name__ == "__main__":
    # Debug mode lets anyone who can reach the site run code on your PC, so it is OFF by default.
    # For private editing only, set FLASK_DEBUG=1 before running.
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("FLASK_DEBUG") == "1")
