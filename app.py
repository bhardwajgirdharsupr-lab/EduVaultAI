import hashlib
import json
import logging
import os
import re
import secrets
import smtplib
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from markupsafe import Markup, escape
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

from connectors import import_url_metadata

try:
    from authlib.integrations.flask_client import OAuth
except Exception:  # Authlib is installed in Docker/production via requirements.txt.
    OAuth = None


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = BASE_DIR / "data" / "eduvault.db"
DEFAULT_UPLOAD_DIR = BASE_DIR / "uploads"
ALLOWED_UPLOADS = {"pdf", "png", "jpg", "jpeg", "doc", "docx"}
logger = logging.getLogger(__name__)


def load_env_file(path=BASE_DIR / ".env"):
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_env_file()


COURSE_CATALOG = [
    {
        "slug": "python-data-science",
        "title": "Python for Data Science",
        "platform": "Coursera",
        "source": "coursera",
        "description": "Build practical Python, pandas, visualization, and analytics foundations.",
        "skills": "Python, pandas, data cleaning, visualization",
        "duration": "6 weeks",
        "level": "Beginner",
        "badge": "PY",
    },
    {
        "slug": "react-mastery",
        "title": "React Mastery",
        "platform": "Udemy",
        "source": "udemy",
        "description": "Learn component thinking, state, routing, and modern UI workflows.",
        "skills": "Components, state, routing, UI architecture",
        "duration": "18 hours",
        "level": "Intermediate",
        "badge": "RX",
    },
    {
        "slug": "sql-edx",
        "title": "SQL for Data Work",
        "platform": "edX",
        "source": "edx",
        "description": "Practice joins, aggregations, schema design, and reporting queries.",
        "skills": "SQL, joins, analytics, reporting",
        "duration": "4 weeks",
        "level": "Beginner",
        "badge": "SQL",
    },
    {
        "slug": "freecodecamp-python",
        "title": "Python Beginner Guide",
        "platform": "freeCodeCamp",
        "source": "youtube",
        "description": "A compact video-first path through Python fundamentals.",
        "skills": "Variables, loops, functions, problem solving",
        "duration": "1 hour",
        "level": "Beginner",
        "badge": "YT",
    },
    {
        "slug": "ux-design",
        "title": "Advanced UX Design",
        "platform": "EduVault Picks",
        "source": "web",
        "description": "Improve product flows, research synthesis, and high-quality interface critique.",
        "skills": "UX research, wireframes, prototyping, accessibility",
        "duration": "5 weeks",
        "level": "Advanced",
        "badge": "UX",
    },
    {
        "slug": "google-data-analytics",
        "title": "Google Data Analytics Certificate",
        "platform": "Google",
        "source": "coursera",
        "description": "A portfolio-ready analytics path covering spreadsheets, SQL, and dashboards.",
        "skills": "Analytics, SQL, spreadsheets, dashboards",
        "duration": "8 weeks",
        "level": "Beginner",
        "badge": "GA",
    },
]


STATIC_PAGES = {
    "features": {
        "title": "Features",
        "headline": "Everything your learning record needs in one place.",
        "body": [
            "Import course links, upload certificates, verify SHA-256 fingerprints, and keep a professional learning timeline.",
            "EduVault is designed for students and professionals who want a clean record they can share without rebuilding a portfolio every time.",
        ],
    },
    "pricing": {
        "title": "Pricing",
        "headline": "Start free, upgrade when your vault grows.",
        "body": [
            "The v1 app ships with a Free plan UI and coming-soon Pro actions. Payment processing is intentionally deferred.",
            "The interface is ready for future billing without adding payment-provider risk to the first deployment.",
        ],
    },
    "security": {
        "title": "Security",
        "headline": "Verification-first learning records.",
        "body": [
            "Each uploaded certificate receives a SHA-256 fingerprint so users can confirm the file has not changed.",
            "Private dashboards, role-based admin access, safe file storage, and form tokens protect the core vault workflow.",
        ],
    },
    "about": {
        "title": "About",
        "headline": "EduVault helps learning stay portable.",
        "body": [
            "Courses, certificates, notes, and proof of progress often live across many platforms. EduVault gives them one calm home.",
            "The product is built as a practical Flask app that can be deployed on a real domain.",
        ],
    },
    "contact": {
        "title": "Contact",
        "headline": "Questions, feedback, or deployment help?",
        "body": [
            "Use this page as the public contact destination for your deployed domain.",
            "In v1, contact handling is static; wire it to email or a support tool when the domain goes live.",
        ],
    },
    "terms": {
        "title": "Terms of Service",
        "headline": "Use EduVault responsibly.",
        "body": [
            "Users are responsible for uploading files they have the right to store and share.",
            "Administrators may remove unsafe or abusive content to protect the platform.",
        ],
    },
    "privacy": {
        "title": "Privacy Policy",
        "headline": "Your vault is private by default.",
        "body": [
            "EduVault stores profile details, course records, certificate metadata, and uploaded files for authenticated users.",
            "Public sharing happens only through generated share links or portfolio links.",
        ],
    },
    "help": {
        "title": "Help",
        "headline": "How to use your vault.",
        "body": [
            "Import a course URL, upload certificates as you complete learning, and share verified records when needed.",
            "Admins can manage users and content from the admin area.",
        ],
    },
}


class TitleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.title = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data.strip())

    @property
    def value(self):
        return " ".join(part for part in self.title if part).strip()


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_email(email):
    return (email or "").strip().lower()


def get_database_path():
    return Path(os.environ.get("DATABASE_PATH", DEFAULT_DATABASE))


def get_upload_dir():
    return Path(os.environ.get("UPLOAD_DIR", DEFAULT_UPLOAD_DIR))


def ensure_directories():
    get_database_path().parent.mkdir(parents=True, exist_ok=True)
    get_upload_dir().mkdir(parents=True, exist_ok=True)


def get_db():
    if "db" not in g:
        ensure_directories()
        db = sqlite3.connect(get_database_path())
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        g.db = db
    return g.db


def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def row_to_dict(row):
    return dict(row) if row else None


def init_db():
    ensure_directories()
    db = sqlite3.connect(get_database_path())
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            phone TEXT,
            date_of_birth TEXT,
            institution TEXT,
            location TEXT,
            language TEXT DEFAULT 'English',
            password_hash TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            status TEXT NOT NULL DEFAULT 'active',
            google_sub TEXT UNIQUE,
            oauth_provider TEXT,
            avatar_color TEXT DEFAULT '#bfe3ff',
            preferred_aggregator TEXT DEFAULT 'EduVault Aggregator',
            auto_import_youtube INTEGER DEFAULT 0,
            default_note_format TEXT DEFAULT 'Course format',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_login_at TEXT
        );

        CREATE TABLE IF NOT EXISTS pending_registrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            payload TEXT NOT NULL,
            otp_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS learning_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'web',
            url TEXT,
            description TEXT,
            platform TEXT,
            progress INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'in-progress',
            skills TEXT,
            notes TEXT,
            certificate_id INTEGER,
            in_portfolio INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (certificate_id) REFERENCES certificates(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            external_course_id TEXT,
            title TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT 'Udemy',
            source TEXT NOT NULL DEFAULT 'udemy',
            url TEXT,
            description TEXT,
            skills TEXT,
            duration TEXT,
            level TEXT,
            badge TEXT,
            subject TEXT,
            is_paid INTEGER NOT NULL DEFAULT 0,
            price REAL,
            num_subscribers INTEGER NOT NULL DEFAULT 0,
            num_reviews INTEGER NOT NULL DEFAULT 0,
            num_lectures INTEGER NOT NULL DEFAULT 0,
            published_timestamp TEXT,
            created_by_user_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS certificates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            learning_item_id INTEGER,
            original_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            issuer TEXT,
            course_title TEXT,
            verified INTEGER NOT NULL DEFAULT 1,
            in_portfolio INTEGER NOT NULL DEFAULT 0,
            uploaded_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (learning_item_id) REFERENCES learning_items(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS share_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            certificate_id INTEGER,
            token TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL DEFAULT 'portfolio',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (certificate_id) REFERENCES certificates(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id INTEGER,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id INTEGER,
            detail TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE SET NULL
        );
        """
    )
    ensure_column(db, "courses", "created_by_user_id", "INTEGER")
    migrate_learning_items_to_courses(db)
    db.commit()
    db.close()


def ensure_column(db, table, column, definition):
    columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def migrate_learning_items_to_courses(db):
    now = utc_now()
    for item in db.execute("SELECT * FROM learning_items").fetchall():
        existing = None
        if item["url"]:
            existing = db.execute("SELECT 1 FROM courses WHERE url = ? LIMIT 1", (item["url"],)).fetchone()
        if not existing:
            existing = db.execute(
                "SELECT 1 FROM courses WHERE lower(title) = lower(?) AND lower(platform) = lower(?) LIMIT 1",
                (item["title"], item["platform"] or item["source"]),
            ).fetchone()
        if existing:
            continue
        base_slug = slugify(item["title"])
        slug = base_slug
        index = 2
        while db.execute("SELECT 1 FROM courses WHERE slug = ?", (slug,)).fetchone():
            slug = f"{base_slug}-{index}"
            index += 1
        db.execute(
            """
            INSERT INTO courses (
                slug, title, platform, source, url, description, skills, duration, level,
                badge, subject, is_paid, price, num_subscribers, num_reviews, num_lectures,
                created_by_user_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'Self-paced', 'All Levels', ?, ?, 0, NULL, 0, 0, 0, ?, ?, ?)
            """,
            (
                slug,
                item["title"],
                item["platform"] or item["source"].title(),
                item["source"],
                item["url"],
                item["description"],
                item["skills"],
                badge_for_text(item["platform"] or item["source"]),
                item["platform"] or item["source"].title(),
                item["user_id"],
                now,
                now,
            ),
        )


def bootstrap_admin():
    email = normalize_email(os.environ.get("ADMIN_EMAIL"))
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not email or not password:
        return
    db = sqlite3.connect(get_database_path())
    db.row_factory = sqlite3.Row
    existing = db.execute("SELECT id, role FROM users WHERE email = ?", (email,)).fetchone()
    now = utc_now()
    if existing:
        db.execute(
            "UPDATE users SET role = 'admin', status = 'active', updated_at = ? WHERE id = ?",
            (now, existing["id"]),
        )
    else:
        db.execute(
            """
            INSERT INTO users (full_name, email, password_hash, role, status, created_at, updated_at)
            VALUES (?, ?, ?, 'admin', 'active', ?, ?)
            """,
            ("EduVault Admin", email, generate_password_hash(password), now, now),
        )
    db.commit()
    db.close()


def log_action(action, target_type=None, target_id=None, detail=None, actor_id=None):
    try:
        if actor_id is None:
            actor_id = g.user["id"] if getattr(g, "user", None) else None
        db = get_db()
        db.execute(
            """
            INSERT INTO audit_logs (actor_user_id, action, target_type, target_id, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (actor_id, action, target_type, target_id, detail, utc_now()),
        )
        db.commit()
    except Exception:
        pass


def create_app():
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-change-me-eduvault")
    app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", "10")) * 1024 * 1024
    app.config["APP_BASE_URL"] = os.environ.get("APP_BASE_URL", "").strip().rstrip("/")
    app.teardown_appcontext(close_db)

    init_db()
    bootstrap_admin()

    oauth = configure_oauth(app)

    @app.before_request
    def load_user():
        user_id = session.get("user_id")
        g.user = None
        if user_id:
            g.user = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not g.user or g.user["status"] != "active":
                session.clear()
                g.user = None

    @app.context_processor
    def inject_globals():
        return {
            "current_user": g.user,
            "app_shell": should_use_app_shell(),
            "csrf_field": csrf_field,
            "oauth_enabled": oauth is not None,
            "static_pages": STATIC_PAGES,
            "current_year": datetime.now().year,
        }

    @app.route("/")
    def home():
        return render_template("home.html")

    @app.route("/start-import", methods=["POST"])
    def start_import():
        require_csrf()
        url = request.form.get("url", "").strip()
        if not url:
            flash("Paste a course URL or certificate lookup before importing.", "warning")
            return redirect(url_for("home"))
        session["pending_import_url"] = url
        if g.user:
            return redirect(url_for("import_pending"))
        flash("Create an account or sign in to save this resource to your vault.", "info")
        return redirect(url_for("register"))

    @app.route("/import-pending")
    @login_required
    def import_pending():
        url = session.pop("pending_import_url", None)
        if url:
            create_learning_item_from_url(g.user["id"], url)
            flash("Imported your resource into your vault and the shared course collection.", "success")
        return redirect(url_for("dashboard"))

    @app.route("/courses")
    def courses():
        q = request.args.get("q", "").strip().lower()
        courses_list = query_courses(q)
        return render_template("courses.html", courses=courses_list, q=q)

    @app.route("/courses/<slug>")
    def course_detail(slug):
        course = find_course(slug)
        if not course:
            abort(404)
        vault_item = user_learning_item_for_course(g.user["id"], course) if g.user else None
        return render_template("course_detail.html", course=course, vault_item=vault_item)

    @app.route("/courses/<slug>/add", methods=["POST"])
    @login_required
    def add_course(slug):
        require_csrf()
        course = find_course(slug)
        if not course:
            abort(404)
        now = utc_now()
        db = get_db()
        course_url = course["url"]
        existing = db.execute(
            """
            SELECT id FROM learning_items
            WHERE user_id = ? AND (url = ? OR title = ?)
            LIMIT 1
            """,
            (g.user["id"], course_url, course["title"]),
        ).fetchone()
        if existing:
            flash("This course is already in your learning vault.", "info")
        else:
            db.execute(
                """
                INSERT INTO learning_items
                    (user_id, title, source, url, description, platform, progress, status, skills, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    g.user["id"],
                    course["title"],
                    course["source"],
                    course_url,
                    course["description"],
                    course["platform"],
                    0,
                    "in-progress",
                    course["skills"],
                    now,
                    now,
                ),
            )
            db.commit()
            log_action("course_added", "course", course["id"], course["title"])
            flash("Course added to your learning vault.", "success")
        return redirect(url_for("resources"))

    @app.route("/feedback", methods=["GET", "POST"])
    def feedback():
        if request.method == "POST":
            require_csrf()
            name = request.form.get("name", "").strip()
            email = normalize_email(request.form.get("email"))
            category = request.form.get("category", "General feedback").strip()
            message = request.form.get("message", "").strip()
            if not name or not email or not message:
                flash("Name, email, and feedback message are required.", "danger")
                return render_template("feedback.html"), 400
            sent, error = send_feedback_email(name, email, category, message)
            if sent:
                log_action("feedback_submitted", "feedback", None, email)
                flash("Thanks for the feedback. It has been emailed to EduVault.", "success")
                return redirect(url_for("feedback"))
            flash(error or "Feedback email could not be sent right now.", "danger")
            return render_template("feedback.html")
        return render_template("feedback.html")

    @app.route("/<page>")
    def static_page(page):
        content = STATIC_PAGES.get(page)
        if not content:
            abort(404)
        return render_template("static_page.html", page=content)

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if g.user:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            require_csrf()
            full_name = request.form.get("full_name", "").strip()
            email = normalize_email(request.form.get("email"))
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")
            if not full_name or not email or not password:
                flash("Full name, email, and password are required.", "danger")
                return render_template("register.html"), 400
            if password != confirm:
                flash("Passwords do not match.", "danger")
                return render_template("register.html"), 400
            if len(password) < 8:
                flash("Use at least 8 characters for your password.", "danger")
                return render_template("register.html"), 400
            if get_db().execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
                flash("That email is already registered. Please sign in.", "warning")
                return render_template("register.html"), 409

            otp = f"{secrets.randbelow(1_000_000):06d}"
            pending_data = {
                "full_name": full_name,
                "email": email,
                "phone": request.form.get("phone", "").strip(),
                "date_of_birth": request.form.get("date_of_birth", "").strip(),
                "institution": request.form.get("institution", "").strip(),
                "password_hash": generate_password_hash(password),
            }
            save_pending_registration(pending_data, otp)
            sent, error = send_registration_otp(email, full_name, otp)
            if not sent:
                delete_pending_registration(email)
                flash(error or "Verification email could not be sent right now.", "danger")
                return render_template("register.html"), 503

            session["pending_registration_email"] = email
            flash("We sent a 6-digit verification code to your email.", "success")
            return redirect(url_for("verify_email"))
        return render_template("register.html")

    @app.route("/verify-email", methods=["GET", "POST"])
    def verify_email():
        if g.user:
            return redirect(url_for("dashboard"))

        email = normalize_email(session.get("pending_registration_email") or request.form.get("email"))
        if not email:
            flash("Start registration first so we know where to send your code.", "warning")
            return redirect(url_for("register"))

        pending = pending_registration_for(email)
        if not pending:
            flash("No pending verification was found. Please register again.", "warning")
            session.pop("pending_registration_email", None)
            return redirect(url_for("register"))

        if request.method == "POST":
            require_csrf()
            otp = re.sub(r"\D", "", request.form.get("otp", ""))
            if is_expired(pending["expires_at"]):
                flash("That verification code has expired. Request a new code.", "warning")
                return render_template("verify_email.html", email=email), 400
            if len(otp) != 6 or not check_password_hash(pending["otp_hash"], otp):
                flash("Invalid verification code.", "danger")
                return render_template("verify_email.html", email=email), 401
            if get_db().execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
                delete_pending_registration(email)
                session.pop("pending_registration_email", None)
                flash("That email is already registered. Please sign in.", "warning")
                return redirect(url_for("login"))

            data = json.loads(pending["payload"])
            now = utc_now()
            db = get_db()
            try:
                cursor = db.execute(
                    """
                    INSERT INTO users
                        (full_name, email, phone, date_of_birth, institution, password_hash, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        data["full_name"],
                        data["email"],
                        data.get("phone", ""),
                        data.get("date_of_birth", ""),
                        data.get("institution", ""),
                        data["password_hash"],
                        now,
                        now,
                    ),
                )
                db.execute("DELETE FROM pending_registrations WHERE email = ?", (email,))
                db.commit()
            except sqlite3.IntegrityError:
                db.rollback()
                flash("That email is already registered. Please sign in.", "warning")
                return redirect(url_for("login"))

            session.pop("pending_registration_email", None)
            user_id = cursor.lastrowid
            login_user(user_id)
            log_action("user_registered", "user", user_id, email, actor_id=user_id)
            flash("Email verified. Welcome to EduVault.", "success")
            return redirect(url_for("dashboard"))

        return render_template("verify_email.html", email=email)

    @app.route("/resend-verification-code", methods=["POST"])
    def resend_verification_code():
        if g.user:
            return redirect(url_for("dashboard"))
        require_csrf()
        email = normalize_email(session.get("pending_registration_email") or request.form.get("email"))
        pending = pending_registration_for(email) if email else None
        if not pending:
            flash("No pending verification was found. Please register again.", "warning")
            return redirect(url_for("register"))

        data = json.loads(pending["payload"])
        otp = f"{secrets.randbelow(1_000_000):06d}"
        save_pending_registration(data, otp)
        sent, error = send_registration_otp(email, data["full_name"], otp)
        if not sent:
            flash(error or "Verification email could not be sent right now.", "danger")
            return redirect(url_for("verify_email"))
        session["pending_registration_email"] = email
        flash("A new verification code has been sent.", "success")
        return redirect(url_for("verify_email"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            require_csrf()
            email = normalize_email(request.form.get("email"))
            password = request.form.get("password", "")
            user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if not user or not user["password_hash"] or not check_password_hash(user["password_hash"], password):
                flash("Invalid email or password.", "danger")
                return render_template("login.html"), 401
            if user["status"] != "active":
                flash("This account is inactive. Contact the administrator.", "danger")
                return render_template("login.html"), 403
            login_user(user["id"])
            log_action("user_logged_in", "user", user["id"], email, actor_id=user["id"])
            return redirect(url_for("import_pending" if session.get("pending_import_url") else "dashboard"))
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        flash("Signed out successfully.", "info")
        return redirect(url_for("home"))

    @app.route("/auth/google")
    def google_login():
        if oauth is None:
            flash("Google OAuth is not configured yet. Add Google client credentials to enable it.", "warning")
            return redirect(url_for("login"))
        redirect_uri = external_url_for("google_callback")
        logger.info("Starting Google OAuth redirect_uri=%s", redirect_uri)
        return oauth.google.authorize_redirect(redirect_uri)

    @app.route("/auth/google/callback")
    def google_callback():
        if oauth is None:
            flash("Google OAuth is not configured.", "warning")
            return redirect(url_for("login"))
        try:
            token = oauth.google.authorize_access_token()
            info = token.get("userinfo") or oauth.google.parse_id_token(token)
        except Exception:
            logger.exception("Google OAuth callback failed")
            flash("Google sign-in could not be completed. Try again.", "danger")
            return redirect(url_for("login"))
        email = normalize_email(info.get("email"))
        if not email:
            flash("Google did not return an email address.", "danger")
            return redirect(url_for("login"))
        user_id = upsert_google_user(info)
        login_user(user_id)
        log_action("google_oauth_login", "user", user_id, email, actor_id=user_id)
        return redirect(url_for("import_pending" if session.get("pending_import_url") else "dashboard"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        data = vault_data(g.user["id"])
        portfolio_link = get_active_portfolio_link(g.user["id"])
        return render_template("dashboard.html", **data, portfolio_link=portfolio_link)

    @app.route("/import", methods=["POST"])
    @login_required
    def import_url():
        require_csrf()
        url = request.form.get("url", "").strip()
        if not url:
            flash("Paste a URL to import.", "warning")
            return redirect(request.referrer or url_for("dashboard"))
        item_id, title, warning = create_learning_item_from_url(g.user["id"], url)
        if warning:
            flash(warning, "warning")
        else:
            flash(f"Imported {title} into your vault and the shared course collection.", "success")
        log_action("url_imported", "learning_item", item_id, url)
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/items/<int:item_id>/delete", methods=["POST"])
    @login_required
    def delete_item(item_id):
        require_csrf()
        item = owned_learning_item(item_id)
        if not item:
            abort(404)
        get_db().execute("DELETE FROM learning_items WHERE id = ?", (item_id,))
        get_db().commit()
        log_action("learning_item_deleted", "learning_item", item_id, item["title"])
        flash("Course or learning material removed from your vault.", "info")
        return redirect(url_for("resources"))

    @app.route("/items/<int:item_id>/progress", methods=["POST"])
    @login_required
    def update_progress(item_id):
        require_csrf()
        item = owned_learning_item(item_id)
        if not item:
            abort(404)
        progress = max(0, min(100, int(request.form.get("progress", item["progress"] or 0))))
        status = "verified" if progress == 100 else "in-progress"
        now = utc_now()
        get_db().execute(
            "UPDATE learning_items SET progress = ?, status = ?, updated_at = ? WHERE id = ?",
            (progress, status, now, item_id),
        )
        get_db().commit()
        flash("Progress updated.", "success")
        return redirect(request.referrer or url_for("resources"))

    @app.route("/certificates")
    @login_required
    def certificates():
        q = request.args.get("q", "").strip()
        certs = query_certificates(g.user["id"], q)
        selected = certs[0] if certs else None
        return render_template("certificates.html", certificates=certs, selected=selected, q=q)

    @app.route("/certificates/upload", methods=["POST"])
    @login_required
    def upload_certificate():
        require_csrf()
        uploaded = request.files.get("certificate")
        if not uploaded or not uploaded.filename:
            flash("Choose a certificate file to upload.", "warning")
            return redirect(url_for("certificates"))
        if not allowed_file(uploaded.filename):
            flash("Allowed certificate files: PDF, PNG, JPG, DOC, DOCX.", "danger")
            return redirect(url_for("certificates"))
        file_bytes = uploaded.read()
        if not file_bytes:
            flash("The selected file is empty.", "danger")
            return redirect(url_for("certificates"))
        original = secure_filename(uploaded.filename)
        stored = f"{g.user['id']}_{uuid.uuid4().hex}_{original}"
        path = get_upload_dir() / stored
        path.write_bytes(file_bytes)
        sha = hashlib.sha256(file_bytes).hexdigest()
        title = request.form.get("course_title", "").strip() or Path(original).stem.replace("_", " ").title()
        issuer = request.form.get("issuer", "").strip() or "Self uploaded"
        now = utc_now()
        db = get_db()
        cursor = db.execute(
            """
            INSERT INTO certificates
                (user_id, original_filename, stored_filename, file_path, file_size, sha256, issuer, course_title, uploaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (g.user["id"], original, stored, str(path), len(file_bytes), sha, issuer, title, now),
        )
        cert_id = cursor.lastrowid
        item_cursor = db.execute(
            """
            INSERT INTO learning_items
                (user_id, title, source, description, platform, progress, status, certificate_id, created_at, updated_at)
            VALUES (?, ?, 'upload', ?, ?, 100, 'verified', ?, ?, ?)
            """,
            (g.user["id"], title, f"Uploaded certificate: {original}", issuer, cert_id, now, now),
        )
        item_id = item_cursor.lastrowid
        db.execute("UPDATE certificates SET learning_item_id = ? WHERE id = ?", (item_id, cert_id))
        db.commit()
        log_action("certificate_uploaded", "certificate", cert_id, original)
        flash("Certificate uploaded and SHA-256 verified.", "success")
        return redirect(url_for("certificates"))

    @app.route("/certificates/<int:cert_id>/download")
    @login_required
    def download_certificate(cert_id):
        cert = certificate_for_access(cert_id)
        if not cert:
            abort(404)
        return send_file(cert["file_path"], as_attachment=True, download_name=cert["original_filename"])

    @app.route("/certificates/<int:cert_id>/portfolio", methods=["POST"])
    @login_required
    def toggle_certificate_portfolio(cert_id):
        require_csrf()
        cert = owned_certificate(cert_id)
        if not cert:
            abort(404)
        next_value = 0 if cert["in_portfolio"] else 1
        get_db().execute("UPDATE certificates SET in_portfolio = ? WHERE id = ?", (next_value, cert_id))
        if cert["learning_item_id"]:
            get_db().execute(
                "UPDATE learning_items SET in_portfolio = ? WHERE id = ?",
                (next_value, cert["learning_item_id"]),
            )
        get_db().commit()
        flash("Portfolio visibility updated.", "success")
        return redirect(url_for("certificates"))

    @app.route("/certificates/<int:cert_id>/share", methods=["POST"])
    @login_required
    def share_certificate(cert_id):
        require_csrf()
        cert = owned_certificate(cert_id)
        if not cert:
            abort(404)
        link = get_or_create_share_link(g.user["id"], "certificate", cert_id)
        flash(f"Share link ready: {url_for('public_certificate', token=link['token'], _external=True)}", "success")
        return redirect(url_for("certificates"))

    @app.route("/portfolio/share", methods=["POST"])
    @login_required
    def share_portfolio():
        require_csrf()
        link = get_or_create_share_link(g.user["id"], "portfolio")
        flash(f"Portfolio link ready: {url_for('public_portfolio', token=link['token'], _external=True)}", "success")
        return redirect(url_for("dashboard"))

    @app.route("/resources")
    @login_required
    def resources():
        items = query_learning_items(g.user["id"], request.args.get("vault_q", ""))
        selected = items[0] if items else None
        return render_template("resources.html", items=items, selected=selected, q=request.args.get("vault_q", ""))

    @app.route("/resource-collection")
    @login_required
    def resource_collection():
        q = request.args.get("q", "").strip().lower()
        suggestions = query_courses(q, limit=120)
        vault_course_ids = user_course_ids(g.user["id"])
        return render_template(
            "resource_collection.html",
            suggestions=suggestions,
            q=q,
            vault_course_ids=vault_course_ids,
        )

    @app.route("/analytics")
    @login_required
    def analytics():
        data = vault_data(g.user["id"])
        source_counts = get_db().execute(
            """
            SELECT source, COUNT(*) AS count
            FROM learning_items
            WHERE user_id = ?
            GROUP BY source
            ORDER BY count DESC
            """,
            (g.user["id"],),
        ).fetchall()
        return render_template("analytics.html", **data, source_counts=source_counts)

    @app.route("/settings", methods=["GET", "POST"])
    @login_required
    def settings():
        if request.method == "POST":
            require_csrf()
            action = request.form.get("action")
            if action == "profile":
                db = get_db()
                db.execute(
                    """
                    UPDATE users
                    SET full_name = ?, phone = ?, institution = ?, location = ?, language = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        request.form.get("full_name", "").strip() or g.user["full_name"],
                        request.form.get("phone", "").strip(),
                        request.form.get("institution", "").strip(),
                        request.form.get("location", "").strip(),
                        request.form.get("language", "").strip() or "English",
                        utc_now(),
                        g.user["id"],
                    ),
                )
                db.commit()
                flash("Profile updated.", "success")
            elif action == "password":
                current = request.form.get("current_password", "")
                new_password = request.form.get("new_password", "")
                if g.user["password_hash"] and not check_password_hash(g.user["password_hash"], current):
                    flash("Current password is incorrect.", "danger")
                elif len(new_password) < 8:
                    flash("New password must be at least 8 characters.", "danger")
                else:
                    get_db().execute(
                        "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                        (generate_password_hash(new_password), utc_now(), g.user["id"]),
                    )
                    get_db().commit()
                    flash("Password changed.", "success")
            elif action == "preferences":
                get_db().execute(
                    """
                    UPDATE users
                    SET preferred_aggregator = ?, auto_import_youtube = ?, default_note_format = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        request.form.get("preferred_aggregator", "EduVault Aggregator"),
                        1 if request.form.get("auto_import_youtube") else 0,
                        request.form.get("default_note_format", "Course format"),
                        utc_now(),
                        g.user["id"],
                    ),
                )
                get_db().commit()
                flash("Vault preferences saved.", "success")
            elif action == "deactivate":
                get_db().execute(
                    "UPDATE users SET status = 'inactive', updated_at = ? WHERE id = ?",
                    (utc_now(), g.user["id"]),
                )
                get_db().commit()
                session.clear()
                flash("Your account has been deactivated.", "info")
                return redirect(url_for("home"))
            return redirect(url_for("settings"))
        return render_template("settings.html")

    @app.route("/export")
    @login_required
    def export_json():
        user = row_to_dict(g.user)
        user.pop("password_hash", None)
        payload = {
            "exported_at": utc_now(),
            "user": user,
            "learning_items": [row_to_dict(row) for row in query_learning_items(g.user["id"])],
            "certificates": [row_to_dict(row) for row in query_certificates(g.user["id"])],
        }
        export_path = get_upload_dir() / f"eduvault_export_{g.user['id']}.json"
        export_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return send_file(export_path, as_attachment=True, download_name="eduvault_export.json")

    @app.route("/resume/export")
    @login_required
    def export_resume():
        resume_path = build_resume_document(g.user, query_learning_items(g.user["id"]), query_certificates(g.user["id"]))
        download_name = f"{slugify(g.user['full_name']) or 'eduvault'}_resume.doc"
        return send_file(resume_path, as_attachment=True, download_name=download_name)

    @app.route("/p/<token>")
    def public_portfolio(token):
        link = get_share_link(token, "portfolio")
        if not link:
            abort(404)
        user = get_db().execute("SELECT * FROM users WHERE id = ?", (link["user_id"],)).fetchone()
        certs = get_db().execute(
            """
            SELECT * FROM certificates
            WHERE user_id = ? AND in_portfolio = 1
            ORDER BY uploaded_at DESC
            """,
            (link["user_id"],),
        ).fetchall()
        items = get_db().execute(
            """
            SELECT * FROM learning_items
            WHERE user_id = ? AND in_portfolio = 1
            ORDER BY created_at DESC
            """,
            (link["user_id"],),
        ).fetchall()
        return render_template("public_portfolio.html", owner=user, certificates=certs, items=items)

    @app.route("/s/<token>")
    def public_certificate(token):
        link = get_share_link(token, "certificate")
        if not link:
            abort(404)
        cert = get_db().execute("SELECT * FROM certificates WHERE id = ?", (link["certificate_id"],)).fetchone()
        owner = get_db().execute("SELECT * FROM users WHERE id = ?", (link["user_id"],)).fetchone()
        if not cert:
            abort(404)
        return render_template("public_certificate.html", cert=cert, owner=owner)

    @app.route("/admin")
    @admin_required
    def admin_dashboard():
        db = get_db()
        stats = {
            "users": db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"],
            "active_users": db.execute("SELECT COUNT(*) AS c FROM users WHERE status = 'active'").fetchone()["c"],
            "items": db.execute("SELECT COUNT(*) AS c FROM learning_items").fetchone()["c"],
            "certificates": db.execute("SELECT COUNT(*) AS c FROM certificates").fetchone()["c"],
        }
        users = db.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT 50").fetchall()
        items = db.execute(
            """
            SELECT learning_items.*, users.email AS owner_email
            FROM learning_items
            JOIN users ON users.id = learning_items.user_id
            ORDER BY learning_items.created_at DESC
            LIMIT 30
            """
        ).fetchall()
        certs = db.execute(
            """
            SELECT certificates.*, users.email AS owner_email
            FROM certificates
            JOIN users ON users.id = certificates.user_id
            ORDER BY certificates.uploaded_at DESC
            LIMIT 30
            """
        ).fetchall()
        audits = db.execute(
            """
            SELECT audit_logs.*, users.email AS actor_email
            FROM audit_logs
            LEFT JOIN users ON users.id = audit_logs.actor_user_id
            ORDER BY audit_logs.created_at DESC
            LIMIT 40
            """
        ).fetchall()
        return render_template("admin.html", stats=stats, users=users, items=items, certs=certs, audits=audits)

    @app.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
    @admin_required
    def admin_toggle_user(user_id):
        require_csrf()
        if user_id == g.user["id"]:
            flash("You cannot deactivate your own admin account.", "warning")
            return redirect(url_for("admin_dashboard"))
        user = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            abort(404)
        status = "inactive" if user["status"] == "active" else "active"
        get_db().execute("UPDATE users SET status = ?, updated_at = ? WHERE id = ?", (status, utc_now(), user_id))
        get_db().commit()
        log_action("admin_user_status_changed", "user", user_id, status)
        flash("User status updated.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/items/<int:item_id>/delete", methods=["POST"])
    @admin_required
    def admin_delete_item(item_id):
        require_csrf()
        get_db().execute("DELETE FROM learning_items WHERE id = ?", (item_id,))
        get_db().commit()
        log_action("admin_item_deleted", "learning_item", item_id)
        flash("Learning item removed.", "info")
        return redirect(url_for("admin_dashboard"))

    @app.route("/healthz")
    def healthz():
        return jsonify({"ok": True, "time": utc_now()})

    return app


def configure_oauth(app):
    if OAuth is None:
        return None
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip().strip('"').strip("'")
    if not client_id or not client_secret:
        return None
    oauth = OAuth(app)
    oauth.register(
        name="google",
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth


def external_url_for(endpoint, **values):
    base_url = current_app.config.get("APP_BASE_URL")
    if base_url:
        return f"{base_url}{url_for(endpoint, **values)}"
    return url_for(endpoint, _external=True, _scheme="https", **values)


def should_use_app_shell():
    return bool(
        getattr(g, "user", None)
        and request.endpoint
        in {
            "dashboard",
            "certificates",
            "resources",
            "analytics",
            "settings",
                "admin_dashboard",
                "resource_collection",
            }
    )


def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def csrf_field():
    return Markup(f'<input type="hidden" name="csrf_token" value="{escape(csrf_token())}">')


def require_csrf():
    expected = session.get("_csrf_token")
    supplied = request.form.get("csrf_token", "")
    if not expected or not secrets.compare_digest(expected, supplied):
        abort(400, "Invalid form token")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not getattr(g, "user", None):
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def login_user(user_id):
    db = get_db()
    db.execute("UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?", (utc_now(), utc_now(), user_id))
    db.commit()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    session.clear()
    session["user_id"] = user_id
    session["role"] = user["role"]
    session["email"] = user["email"]


def upsert_google_user(info):
    email = normalize_email(info.get("email"))
    google_sub = info.get("sub")
    full_name = info.get("name") or email.split("@")[0]
    db = get_db()
    now = utc_now()
    user = None
    if google_sub:
        user = db.execute("SELECT * FROM users WHERE google_sub = ?", (google_sub,)).fetchone()
    if not user:
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if user:
        if user["status"] != "active":
            abort(403)
        db.execute(
            """
            UPDATE users
            SET google_sub = COALESCE(google_sub, ?), oauth_provider = 'google', full_name = ?, updated_at = ?
            WHERE id = ?
            """,
            (google_sub, full_name, now, user["id"]),
        )
        db.commit()
        return user["id"]
    cursor = db.execute(
        """
        INSERT INTO users
            (full_name, email, google_sub, oauth_provider, created_at, updated_at)
        VALUES (?, ?, ?, 'google', ?, ?)
        """,
        (full_name, email, google_sub, now, now),
    )
    db.commit()
    return cursor.lastrowid


def find_course(slug):
    return get_db().execute("SELECT * FROM courses WHERE slug = ?", (slug,)).fetchone()


def user_learning_item_for_course(user_id, course):
    course_url = course["url"] or ""
    return get_db().execute(
        """
        SELECT * FROM learning_items
        WHERE user_id = ?
          AND ((? != '' AND url = ?) OR title = ?)
        LIMIT 1
        """,
        (user_id, course_url, course_url, course["title"]),
    ).fetchone()


def user_course_ids(user_id):
    rows = get_db().execute(
        """
        SELECT courses.id
        FROM courses
        JOIN learning_items
          ON learning_items.user_id = ?
         AND (
              (courses.url IS NOT NULL AND courses.url != '' AND learning_items.url = courses.url)
              OR lower(learning_items.title) = lower(courses.title)
         )
        """,
        (user_id,),
    ).fetchall()
    return {row["id"] for row in rows}


def query_courses(q="", limit=None):
    q = (q or "").strip()
    db = get_db()
    params = []
    where = ""
    if q:
        like = f"%{q}%"
        where = """
            WHERE title LIKE ?
               OR platform LIKE ?
               OR description LIKE ?
               OR skills LIKE ?
               OR subject LIKE ?
               OR level LIKE ?
        """
        params.extend([like, like, like, like, like, like])
    sql = f"""
        SELECT * FROM courses
        {where}
        ORDER BY num_subscribers DESC, title COLLATE NOCASE
    """
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return db.execute(sql, params).fetchall()


def slugify(value):
    value = (value or "").lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or uuid.uuid4().hex[:10]


def unique_course_slug(db, title):
    base = slugify(title)
    slug = base
    index = 2
    while db.execute("SELECT 1 FROM courses WHERE slug = ?", (slug,)).fetchone():
        slug = f"{base}-{index}"
        index += 1
    return slug


def badge_for_text(value):
    words = [word for word in re.split(r"[^A-Za-z0-9]+", value or "") if word]
    if not words:
        return "EV"
    if len(words) == 1:
        return words[0][:2].upper()
    return "".join(word[0] for word in words[:3]).upper()


def get_or_create_shared_course(metadata, user_id=None):
    db = get_db()
    existing = None
    if metadata.url:
        existing = db.execute("SELECT * FROM courses WHERE url = ? LIMIT 1", (metadata.url,)).fetchone()
    if not existing:
        existing = db.execute(
            """
            SELECT * FROM courses
            WHERE lower(title) = lower(?) AND lower(platform) = lower(?)
            LIMIT 1
            """,
            (metadata.title, metadata.platform),
        ).fetchone()
    if existing:
        return existing

    now = utc_now()
    db.execute(
        """
        INSERT INTO courses (
            slug, title, platform, source, url, description, skills, duration, level,
            badge, subject, is_paid, price, num_subscribers, num_reviews, num_lectures,
            created_by_user_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, 0, 0, 0, ?, ?, ?)
        """,
        (
            unique_course_slug(db, metadata.title),
            metadata.title[:180],
            metadata.platform or metadata.source.title(),
            metadata.source,
            metadata.url,
            metadata.description,
            metadata.skills,
            "Self-paced",
            "All Levels",
            badge_for_text(metadata.platform or metadata.source),
            metadata.platform or metadata.source.title(),
            user_id,
            now,
            now,
        ),
    )
    db.commit()
    return db.execute("SELECT * FROM courses WHERE rowid = last_insert_rowid()").fetchone()


def classify_source(url):
    host = urlparse(url).netloc.lower()
    if "youtu.be" in host or "youtube.com" in host:
        return "youtube"
    if "coursera.org" in host:
        return "coursera"
    if "udemy.com" in host:
        return "udemy"
    if "edx.org" in host:
        return "edx"
    return "web"


def normalize_url(url):
    url = (url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
    return url


def fetch_page_title(url):
    url = normalize_url(url)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return url, "Invalid URL format. Saved the resource with the URL as its title."
    try:
        req = Request(url, headers={"User-Agent": "EduVaultBot/1.0"})
        with urlopen(req, timeout=6) as response:
            html = response.read(200_000).decode("utf-8", errors="ignore")
        parser = TitleParser()
        parser.feed(html)
        return parser.value or url, None
    except Exception:
        return url, "Could not fetch the page title, so EduVault saved the URL directly."


def create_learning_item_from_url(user_id, url):
    metadata = import_url_metadata(url)
    course = get_or_create_shared_course(metadata, user_id=user_id)
    now = utc_now()
    db = get_db()
    existing = db.execute(
        """
        SELECT id FROM learning_items
        WHERE user_id = ? AND (url = ? OR title = ?)
        LIMIT 1
        """,
        (user_id, course["url"], course["title"]),
    ).fetchone()
    if existing:
        return existing["id"], course["title"], "This resource is already in your vault. It is also available in the shared collection."
    cursor = db.execute(
        """
        INSERT INTO learning_items
            (user_id, title, source, url, description, platform, skills, progress, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'in-progress', ?, ?)
        """,
        (
            user_id,
            course["title"],
            course["source"],
            course["url"],
            course["description"],
            course["platform"],
            course["skills"],
            now,
            now,
        ),
    )
    db.commit()
    return cursor.lastrowid, course["title"], metadata.warning


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_UPLOADS


def owned_learning_item(item_id):
    return get_db().execute(
        "SELECT * FROM learning_items WHERE id = ? AND user_id = ?",
        (item_id, g.user["id"]),
    ).fetchone()


def owned_certificate(cert_id):
    return get_db().execute(
        "SELECT * FROM certificates WHERE id = ? AND user_id = ?",
        (cert_id, g.user["id"]),
    ).fetchone()


def certificate_for_access(cert_id):
    if g.user["role"] == "admin":
        return get_db().execute("SELECT * FROM certificates WHERE id = ?", (cert_id,)).fetchone()
    return owned_certificate(cert_id)


def query_learning_items(user_id, q=""):
    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        return get_db().execute(
            """
            SELECT * FROM learning_items
            WHERE user_id = ?
              AND (title LIKE ? OR source LIKE ? OR platform LIKE ? OR description LIKE ? OR skills LIKE ?)
            ORDER BY created_at DESC
            """,
            (user_id, like, like, like, like, like),
        ).fetchall()
    return get_db().execute(
        "SELECT * FROM learning_items WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()


def query_certificates(user_id, q=""):
    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        return get_db().execute(
            """
            SELECT * FROM certificates
            WHERE user_id = ?
              AND (course_title LIKE ? OR issuer LIKE ? OR original_filename LIKE ? OR sha256 LIKE ?)
            ORDER BY uploaded_at DESC
            """,
            (user_id, like, like, like, like),
        ).fetchall()
    return get_db().execute(
        "SELECT * FROM certificates WHERE user_id = ? ORDER BY uploaded_at DESC",
        (user_id,),
    ).fetchall()


def vault_data(user_id):
    items = query_learning_items(user_id)
    certificates = query_certificates(user_id)
    completed = len([item for item in items if item["status"] == "verified" or item["progress"] >= 100])
    total_progress = sum(item["progress"] or 0 for item in items)
    average_progress = round(total_progress / len(items)) if items else 0
    hours = max(0, len(items) * 12 + completed * 6)
    return {
        "items": items,
        "certificates": certificates,
        "stats": {
            "total_courses": len(items),
            "verified_certificates": len(certificates),
            "hours_learned": hours,
            "average_progress": average_progress,
            "portfolio_items": len([cert for cert in certificates if cert["in_portfolio"]]),
        },
    }


def build_resume_document(user, items, certificates):
    completed_items = [item for item in items if item["status"] == "verified" or (item["progress"] or 0) >= 100]
    active_items = [item for item in items if item not in completed_items]
    skill_names = []
    for item in items:
        for skill in (item["skills"] or "").split(","):
            skill = skill.strip()
            if skill and skill.lower() not in {existing.lower() for existing in skill_names}:
                skill_names.append(skill)

    def esc(value):
        return escape(value or "")

    def list_items(rows, renderer, empty_text):
        if not rows:
            return f"<p class='muted'>{esc(empty_text)}</p>"
        return "<ul>" + "".join(renderer(row) for row in rows) + "</ul>"

    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{esc(user['full_name'])} Resume</title>
  <style>
    body {{ color: #111827; font-family: Arial, sans-serif; line-height: 1.45; margin: 42px; }}
    h1 {{ color: #0d47a1; font-size: 30px; margin: 0 0 4px; }}
    h2 {{ border-bottom: 2px solid #2f80ed; color: #0b1736; font-size: 17px; margin: 24px 0 10px; padding-bottom: 4px; }}
    h3 {{ font-size: 14px; margin: 0 0 3px; }}
    p {{ margin: 0 0 8px; }}
    ul {{ margin: 0; padding-left: 20px; }}
    li {{ margin-bottom: 8px; }}
    .contact, .muted {{ color: #64748b; }}
    .meta {{ color: #475569; font-size: 12px; }}
    .summary {{ background: #eef6ff; border-left: 4px solid #2f80ed; padding: 10px 12px; }}
  </style>
</head>
<body>
  <h1>{esc(user['full_name'])}</h1>
  <p class="contact">{esc(user['email'])}{' | ' + esc(user['phone']) if user['phone'] else ''}{' | ' + esc(user['location']) if user['location'] else ''}</p>
  <p class="contact">{esc(user['institution']) if user['institution'] else 'Learning portfolio generated by EduVault'}</p>

  <h2>Professional Summary</h2>
  <p class="summary">Verified learning profile with {len(items)} saved courses or learning resources and {len(certificates)} uploaded certificates. Progress average: {vault_data(user['id'])['stats']['average_progress']}%.</p>

  <h2>Skills</h2>
  <p>{esc(', '.join(skill_names[:24]) if skill_names else 'Coursework, certificate verification, self-directed learning')}</p>

  <h2>Completed Courses</h2>
  {list_items(completed_items, lambda item: f"<li><h3>{esc(item['title'])}</h3><div class='meta'>{esc(item['platform'] or item['source'])} | Progress: {item['progress']}%</div><p>{esc(item['description'])}</p></li>", "No completed courses yet.")}

  <h2>Current Learning</h2>
  {list_items(active_items[:12], lambda item: f"<li><h3>{esc(item['title'])}</h3><div class='meta'>{esc(item['platform'] or item['source'])} | Progress: {item['progress']}%</div><p>{esc(item['description'])}</p></li>", "No active courses yet.")}

  <h2>Certificates</h2>
  {list_items(certificates, lambda cert: f"<li><h3>{esc(cert['course_title'])}</h3><div class='meta'>{esc(cert['issuer'])} | Uploaded: {esc(cert['uploaded_at'][:10])} | SHA-256: {esc(cert['sha256'][:16])}...</div></li>", "No certificates uploaded yet.")}
</body>
</html>"""
    resume_path = get_upload_dir() / f"eduvault_resume_{user['id']}.doc"
    resume_path.write_text(str(html), encoding="utf-8")
    return resume_path


def get_or_create_share_link(user_id, kind, certificate_id=None):
    db = get_db()
    existing = db.execute(
        """
        SELECT * FROM share_links
        WHERE user_id = ? AND kind = ? AND COALESCE(certificate_id, 0) = COALESCE(?, 0) AND is_active = 1
        """,
        (user_id, kind, certificate_id),
    ).fetchone()
    if existing:
        return existing
    token = secrets.token_urlsafe(18)
    db.execute(
        """
        INSERT INTO share_links (user_id, certificate_id, token, kind, is_active, created_at)
        VALUES (?, ?, ?, ?, 1, ?)
        """,
        (user_id, certificate_id, token, kind, utc_now()),
    )
    db.commit()
    return db.execute("SELECT * FROM share_links WHERE token = ?", (token,)).fetchone()


def get_active_portfolio_link(user_id):
    return get_db().execute(
        "SELECT * FROM share_links WHERE user_id = ? AND kind = 'portfolio' AND is_active = 1 ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()


def get_share_link(token, kind):
    return get_db().execute(
        "SELECT * FROM share_links WHERE token = ? AND kind = ? AND is_active = 1",
        (token, kind),
    ).fetchone()


def otp_expires_at(minutes=10):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).replace(microsecond=0).isoformat()


def is_expired(iso_value):
    try:
        return datetime.fromisoformat(iso_value) < datetime.now(timezone.utc)
    except ValueError:
        return True


def smtp_settings():
    recipient = os.environ.get("FEEDBACK_RECIPIENT", "eduvaultai.com@gmail.com")
    smtp_username = os.environ.get("SMTP_USERNAME", "").strip()
    return {
        "host": os.environ.get("SMTP_HOST", "").strip(),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "username": smtp_username,
        "password": os.environ.get("SMTP_PASSWORD", "").strip().strip('"').strip("'").replace(" ", ""),
        "from": os.environ.get("SMTP_FROM", smtp_username or recipient).strip(),
        "use_tls": os.environ.get("SMTP_USE_TLS", "1") != "0",
    }


def resend_settings():
    api_key = os.environ.get("RESEND_API_KEY", "").strip().strip('"').strip("'")
    email_from = (
        os.environ.get("EMAIL_FROM", "")
        or os.environ.get("SMTP_FROM", "")
        or os.environ.get("SMTP_USERNAME", "")
    )
    return {
        "api_key": api_key,
        "from": email_from.strip(),
        "allow_test_from": os.environ.get("RESEND_ALLOW_TEST_FROM", "0") == "1",
    }


def resend_error_message(body=""):
    text = (body or "").lower()
    if "domain" in text and ("verify" in text or "not found" in text):
        return "Email could not be sent through Resend. Set EMAIL_FROM to a sender on a verified Resend domain."
    if "onboarding@resend.dev" in text or "testing emails" in text:
        return "Email could not be sent through Resend. Replace onboarding@resend.dev with a verified EMAIL_FROM sender."
    if "1010" in text or "user-agent" in text:
        return "Email could not be sent through Resend. The Resend request was blocked before it reached the API; check the request User-Agent header."
    if "api key" in text or "unauthorized" in text or "forbidden" in text:
        return "Email could not be sent through Resend. Check RESEND_API_KEY and make sure it has email send access."
    return "Email could not be sent through Resend. Check the email API settings and try again."


def send_resend_message(email_message):
    settings = resend_settings()
    if not settings["api_key"]:
        return False, "Resend email is not configured yet. Add RESEND_API_KEY in the environment."
    if not settings["from"]:
        return False, "Resend email is not configured yet. Add EMAIL_FROM with a verified sender address."
    if settings["from"].lower() == "onboarding@resend.dev" and not settings["allow_test_from"]:
        return False, "Email could not be sent through Resend. Replace onboarding@resend.dev with a verified EMAIL_FROM sender."

    payload = {
        "from": settings["from"],
        "to": [address.strip() for address in email_message.get("To", "").split(",") if address.strip()],
        "subject": email_message.get("Subject", ""),
        "text": email_message.get_content(),
    }
    request = Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings['api_key']}",
            "Content-Type": "application/json",
            "User-Agent": "EduVault/1.0",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=15) as response:
            if response.status >= 400:
                body = response.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Resend returned HTTP {response.status}: {body}")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        logger.exception(
            "Resend send failed from=%s to=%s error=%s: %s body=%s",
            settings["from"],
            email_message.get("To"),
            exc.__class__.__name__,
            exc,
            body,
        )
        return False, resend_error_message(body)
    except Exception as exc:
        logger.exception(
            "Resend send failed from=%s to=%s error=%s: %s",
            settings["from"],
            email_message.get("To"),
            exc.__class__.__name__,
            exc,
        )
        return False, "Email could not be sent through Resend. Check the email API settings and try again."
    return True, None


def send_email_message(email_message):
    if os.environ.get("RESEND_API_KEY", "").strip():
        logger.info("Email provider selected: resend to=%s", email_message.get("To"))
        return send_resend_message(email_message)
    logger.info("Email provider selected: smtp to=%s", email_message.get("To"))
    return send_smtp_message(email_message)


def send_smtp_message(email_message):
    settings = smtp_settings()
    if not settings["host"] or not settings["username"] or not settings["password"]:
        return False, "Email is not configured yet. Add SMTP settings in the environment."

    try:
        with smtplib.SMTP(settings["host"], settings["port"], timeout=12) as smtp:
            if settings["use_tls"]:
                smtp.starttls()
            smtp.login(settings["username"], settings["password"])
            smtp.send_message(email_message)
    except Exception as exc:
        logger.exception(
            "SMTP send failed for host=%s port=%s username=%s from=%s to=%s error=%s: %s",
            settings["host"],
            settings["port"],
            settings["username"],
            settings["from"],
            email_message.get("To"),
            exc.__class__.__name__,
            exc,
        )
        return False, "Email could not be sent. Check the SMTP credentials and try again."
    return True, None


def send_registration_otp(email, full_name, otp):
    settings = resend_settings() if os.environ.get("RESEND_API_KEY", "").strip() else smtp_settings()
    email_message = EmailMessage()
    email_message["Subject"] = "Your EduVault verification code"
    email_message["From"] = settings["from"]
    email_message["To"] = email
    email_message.set_content(
        "\n".join(
            [
                f"Hi {full_name},",
                "",
                f"Your EduVault verification code is: {otp}",
                "",
                "This code expires in 10 minutes.",
                "If you did not request this account, you can ignore this email.",
            ]
        )
    )
    return send_email_message(email_message)


def save_pending_registration(data, otp):
    now = utc_now()
    get_db().execute(
        """
        INSERT INTO pending_registrations (email, payload, otp_hash, expires_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(email) DO UPDATE SET
            payload = excluded.payload,
            otp_hash = excluded.otp_hash,
            expires_at = excluded.expires_at,
            updated_at = excluded.updated_at
        """,
        (
            data["email"],
            json.dumps(data),
            generate_password_hash(otp),
            otp_expires_at(),
            now,
            now,
        ),
    )
    get_db().commit()


def pending_registration_for(email):
    return get_db().execute("SELECT * FROM pending_registrations WHERE email = ?", (email,)).fetchone()


def delete_pending_registration(email):
    get_db().execute("DELETE FROM pending_registrations WHERE email = ?", (email,))
    get_db().commit()


def send_feedback_email(name, email, category, message):
    recipient = os.environ.get("FEEDBACK_RECIPIENT", "eduvaultai.com@gmail.com")
    settings = smtp_settings()

    email_message = EmailMessage()
    email_message["Subject"] = f"EduVault feedback: {category or 'General feedback'}"
    email_message["From"] = settings["from"]
    email_message["To"] = recipient
    email_message["Reply-To"] = email
    email_message.set_content(
        "\n".join(
            [
                "New EduVault feedback",
                "",
                f"Name: {name}",
                f"Email: {email}",
                f"Category: {category or 'General feedback'}",
                "",
                "Message:",
                message,
            ]
        )
    )

    sent, error = send_email_message(email_message)
    return sent, None if sent else error.replace("Email", "Feedback email", 1)


app = create_app()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5055")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
        use_reloader=False,
    )
