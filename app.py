"""
Lotwise — Main Application
Run with: python app.py
Visit: http://localhost:5000

Lotwise is a personal sourcing and trade advisory front door: every
visitor action becomes a Lead for me to follow up on directly. There is
no public login, no self-serve marketplace, and no payment is ever
processed on the site.
"""
import io
import os
import secrets
import uuid
from datetime import datetime
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for, flash,
                    abort, Response, send_from_directory)
from flask_login import (LoginManager, login_user, logout_user, login_required,
                          current_user)
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from PIL import Image, UnidentifiedImageError

from models import db, AdminUser, Lead, LeadPhoto, LEAD_TYPE_LABELS, COUNTRIES, country_flag_emoji

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Environment / config
#
# IS_PRODUCTION is inferred from env vars Render sets automatically, so
# nothing here has to be flipped by hand on deploy. Locally (no RENDER var,
# no explicit FLASK_ENV=production) the app runs in dev mode.
# ---------------------------------------------------------------------------
IS_PRODUCTION = bool(os.environ.get("RENDER")) or os.environ.get("FLASK_ENV") == "production"

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    if IS_PRODUCTION:
        # Refuse to boot with a guessable key in production instead of
        # silently signing sessions/cookies with a public default.
        raise RuntimeError(
            "SECRET_KEY environment variable is not set. Set a long random "
            "value in Render's environment settings before deploying."
        )
    SECRET_KEY = "dev-only-insecure-key-do-not-use-in-production"

app = Flask(__name__)
# Render (and most PaaS hosts) terminate TLS at a reverse proxy and forward
# plain HTTP internally, setting X-Forwarded-Proto/X-Forwarded-For instead.
# Without this, Flask thinks every request is insecure HTTP — which breaks
# secure cookies, canonical/OG URLs, and HSTS above.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.jinja_env.filters["flag"] = country_flag_emoji
app.config["SECRET_KEY"] = SECRET_KEY

database_url = os.environ.get("DATABASE_URL", "")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
if not database_url and IS_PRODUCTION:
    raise RuntimeError("DATABASE_URL environment variable is not set.")
app.config["SQLALCHEMY_DATABASE_URI"] = database_url or ("sqlite:///" + os.path.join(BASE_DIR, "lotwise.db"))
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = UPLOAD_DIR
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10MB request body cap

# --- Cookie hardening -------------------------------------------------------
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = IS_PRODUCTION
app.config["REMEMBER_COOKIE_HTTPONLY"] = True
app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
app.config["REMEMBER_COOKIE_SECURE"] = IS_PRODUCTION

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "admin_login"

# CSRF protection for every POST form on the site (public intake forms and
# the admin panel alike).
csrf = CSRFProtect(app)

# Rate limiting — a low default so scraping/abuse can't hammer every route,
# with tighter per-route limits set below on login and the intake forms.
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per hour", "50 per minute"],
    storage_uri="memory://",
)

# One-time setup token for /setup-migrate (see route below). Unset in
# production means the route is disabled outright.
SETUP_TOKEN = os.environ.get("SETUP_TOKEN")


@login_manager.user_loader
def load_user(user_id):
    return AdminUser.query.get(int(user_id))


ALLOWED_IMG_EXT = {"png", "jpg", "jpeg"}
ALLOWED_IMG_FORMATS = {"PNG", "JPEG"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024  # per-file cap, under the 10MB request cap


def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMG_EXT


def save_upload(file_storage):
    """Validates and saves an uploaded image, returns the stored relative
    filename or None if nothing usable was uploaded.

    Validation goes beyond the file extension: the bytes are decoded with
    Pillow and re-encoded from scratch, so a renamed script or a file with
    an image extension but non-image/malicious payload never reaches disk,
    and any embedded EXIF/metadata is stripped in the process. The stored
    filename is a random UUID, never derived from user input."""
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_image(file_storage.filename):
        return None

    raw = file_storage.read()
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        return None

    try:
        img = Image.open(io.BytesIO(raw))
        img.verify()
        # verify() leaves the file unusable for a second load, so reopen.
        img = Image.open(io.BytesIO(raw))
        if img.format not in ALLOWED_IMG_FORMATS:
            return None
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        return None

    ext = "jpg" if img.format == "JPEG" else "png"
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    dest_path = os.path.join(UPLOAD_DIR, stored_name)
    save_kwargs = {"format": img.format}
    if img.format == "JPEG":
        save_kwargs["quality"] = 88
    img.save(dest_path, **save_kwargs)
    return stored_name


def admin_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("admin_login"))
        return view_func(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Security headers — applied to every response. CSP is intentionally
# pragmatic: 'unsafe-inline' stays open for style/script because the site
# ships hand-written inline <style>/<script> blocks rather than a build
# pipeline with nonces; tightening that later means moving those inline
# blocks into static files and adding a nonce.
# ---------------------------------------------------------------------------
@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    if IS_PRODUCTION:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def _lead_common_fields(form):
    """Pulls the fields every intake form shares."""
    return {
        "name": form.get("name", "").strip(),
        "company_name": form.get("company_name", "").strip(),
        "email": form.get("email", "").strip(),
        "phone": form.get("phone", "").strip(),
        "country": form.get("country", "").strip(),
    }


def _require(fields, *names):
    return all((fields.get(n) or "").strip() for n in names)


# ---------------------------------------------------------------------------
# Public marketing pages
# ---------------------------------------------------------------------------
@app.route("/")
def home():
    return render_template("public/home.html")


@app.route("/about")
def about():
    return render_template("public/about.html")


@app.route("/how-it-works")
def how_it_works():
    return render_template("public/how-it-works.html")


@app.route("/faq")
def faq():
    return render_template("public/faq.html")


@app.route("/terms")
def terms():
    return render_template("public/terms.html")


@app.route("/privacy")
def privacy():
    return render_template("public/privacy.html")


# ---------------------------------------------------------------------------
# Intake forms — every one of these just writes a Lead and redirects to a
# thank-you page. Nothing here requires an account and nothing here takes
# a payment.
# ---------------------------------------------------------------------------
@app.route("/contact", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def contact():
    if request.method == "POST":
        common = _lead_common_fields(request.form)
        message = request.form.get("message", "").strip()
        if not _require(common, "name", "email") or not message:
            flash("Please fill in your name, email, and a message.", "error")
            return redirect(url_for("contact"))
        lead = Lead(lead_type="contact", description=message, **common)
        db.session.add(lead)
        db.session.commit()
        return redirect(url_for("thank_you", type="contact"))
    return render_template("public/contact.html")


@app.route("/sourcing-request", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def sourcing_request():
    if request.method == "POST":
        common = _lead_common_fields(request.form)
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()
        description = request.form.get("description", "").strip()
        quantity_note = request.form.get("quantity_note", "").strip()
        needed_by_raw = request.form.get("needed_by", "").strip()
        needed_by = None
        if needed_by_raw:
            try:
                needed_by = datetime.strptime(needed_by_raw, "%Y-%m-%d").date()
            except ValueError:
                needed_by = None
        if not _require(common, "name", "email") or not title:
            flash("Please fill in your name, email, and what you're sourcing.", "error")
            return redirect(url_for("sourcing_request"))
        lead = Lead(lead_type="sourcing", title=title, category=category,
                    description=description, quantity_note=quantity_note,
                    needed_by=needed_by, **common)
        db.session.add(lead)
        db.session.flush()
        for f in request.files.getlist("photos")[:5]:
            stored = save_upload(f)
            if stored:
                db.session.add(LeadPhoto(lead_id=lead.id, file_path=stored))
        db.session.commit()
        return redirect(url_for("thank_you", type="sourcing"))
    return render_template("public/sourcing-request.html", countries=COUNTRIES)


@app.route("/consultation", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def consultation():
    if request.method == "POST":
        common = _lead_common_fields(request.form)
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "").strip()
        if not _require(common, "name", "email"):
            flash("Please fill in your name and email.", "error")
            return redirect(url_for("consultation"))
        lead = Lead(lead_type="consultation", category=category,
                    description=description, **common)
        db.session.add(lead)
        db.session.commit()
        return redirect(url_for("thank_you", type="consultation"))
    return render_template("public/consultation.html", countries=COUNTRIES)


@app.route("/book-a-meeting", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def book_a_meeting():
    if request.method == "POST":
        common = _lead_common_fields(request.form)
        preferred_time_note = request.form.get("preferred_time_note", "").strip()
        description = request.form.get("description", "").strip()
        if not _require(common, "name", "email") or not preferred_time_note:
            flash("Please fill in your name, email, and a preferred time.", "error")
            return redirect(url_for("book_a_meeting"))
        lead = Lead(lead_type="meeting", preferred_time_note=preferred_time_note,
                    description=description, **common)
        db.session.add(lead)
        db.session.commit()
        return redirect(url_for("thank_you", type="meeting"))
    return render_template("public/book-a-meeting.html", countries=COUNTRIES)


@app.route("/sell-your-supply", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def sell_your_supply():
    if request.method == "POST":
        common = _lead_common_fields(request.form)
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()
        description = request.form.get("description", "").strip()
        quantity_note = request.form.get("quantity_note", "").strip()
        origin_country = request.form.get("origin_country", "").strip()
        grade_spec = request.form.get("grade_spec", "").strip()
        if not _require(common, "name", "email", "company_name") or not title:
            flash("Please fill in your name, company, email, and what you're offering.", "error")
            return redirect(url_for("sell_your_supply"))
        lead = Lead(lead_type="supply", title=title, category=category,
                    description=description, quantity_note=quantity_note,
                    origin_country=origin_country, grade_spec=grade_spec, **common)
        db.session.add(lead)
        db.session.flush()
        for f in request.files.getlist("photos")[:6]:
            stored = save_upload(f)
            if stored:
                db.session.add(LeadPhoto(lead_id=lead.id, file_path=stored))
        db.session.commit()
        return redirect(url_for("thank_you", type="supply"))
    return render_template("public/sell-your-supply.html", countries=COUNTRIES)


@app.route("/thank-you")
def thank_you():
    lead_type = request.args.get("type", "")
    return render_template("public/thank-you.html",
                            label=LEAD_TYPE_LABELS.get(lead_type, "request"))


# ---------------------------------------------------------------------------
# Admin — my own inbox for everything the public forms submit. No public
# signup exists; accounts are created with create_admin.py.
# ---------------------------------------------------------------------------
@app.route("/admin/login", methods=["GET", "POST"])
@limiter.limit("8 per minute", methods=["POST"])
def admin_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = AdminUser.query.filter_by(email=email).first()
        # check_password still runs against a dummy hash when no user is
        # found, so the response time doesn't leak which emails exist.
        password_ok = user.check_password(password) if user else AdminUser.dummy_check(password)
        if user and password_ok:
            login_user(user)
            return redirect(url_for("admin_dashboard"))
        flash("Incorrect email or password.", "error")
        return redirect(url_for("admin_login"))
    return render_template("admin/login.html")


@app.route("/admin/logout")
@login_required
def admin_logout():
    logout_user()
    return redirect(url_for("home"))


@app.route("/admin")
@admin_required
def admin_dashboard():
    counts = {
        lead_type: Lead.query.filter_by(lead_type=lead_type, status="new").count()
        for lead_type in LEAD_TYPE_LABELS
    }
    recent = Lead.query.order_by(Lead.created_at.desc()).limit(8).all()
    return render_template("admin/dashboard.html", counts=counts,
                            labels=LEAD_TYPE_LABELS, recent=recent)


@app.route("/admin/leads")
@admin_required
def admin_leads():
    lead_type = request.args.get("type", "")
    status = request.args.get("status", "")
    query = Lead.query
    if lead_type:
        query = query.filter_by(lead_type=lead_type)
    if status:
        query = query.filter_by(status=status)
    leads = query.order_by(Lead.created_at.desc()).all()
    return render_template("admin/leads.html", leads=leads, labels=LEAD_TYPE_LABELS,
                            active_type=lead_type, active_status=status)


@app.route("/admin/leads/<int:lead_id>", methods=["GET", "POST"])
@admin_required
def admin_lead_detail(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    if request.method == "POST":
        lead.status = request.form.get("status", lead.status)
        lead.admin_note = request.form.get("admin_note", lead.admin_note)
        db.session.commit()
        flash("Lead updated.", "success")
        return redirect(url_for("admin_lead_detail", lead_id=lead.id))
    return render_template("admin/lead_detail.html", lead=lead, label=lead.type_label)


@app.route("/admin/leads/<int:lead_id>/delete", methods=["POST"])
@admin_required
def admin_lead_delete(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    db.session.delete(lead)
    db.session.commit()
    flash("Lead deleted.", "success")
    return redirect(url_for("admin_leads"))


# ---------------------------------------------------------------------------
# One-time setup helper — mirrors the old setup route so a fresh Render
# deploy can create tables without shell access. Gated behind a token so it
# can't be triggered by anyone who finds the URL: set SETUP_TOKEN in the
# environment before you need it, unset it (or redeploy without it)
# afterwards, and it disables itself.
# ---------------------------------------------------------------------------
@app.route("/setup-migrate")
def setup_migrate():
    if not SETUP_TOKEN:
        abort(404)
    supplied = request.args.get("token", "")
    if not secrets.compare_digest(supplied, SETUP_TOKEN):
        abort(404)
    db.create_all()
    return "Tables created/verified."


# ---------------------------------------------------------------------------
# SEO / crawling
# ---------------------------------------------------------------------------
@app.route("/robots.txt")
def robots_txt():
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin",
        f"Sitemap: {url_for('sitemap_xml', _external=True)}",
    ]
    return Response("\n".join(lines), mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    public_routes = [
        "home", "about", "how_it_works", "faq", "terms", "privacy",
        "contact", "sourcing_request", "consultation", "book_a_meeting",
        "sell_your_supply",
    ]
    urls = [url_for(r, _external=True) for r in public_routes]
    body = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        body.append(f"  <url><loc>{u}</loc></url>")
    body.append("</urlset>")
    return Response("\n".join(body), mimetype="application/xml")


# ---------------------------------------------------------------------------
# Error pages
# ---------------------------------------------------------------------------
@app.errorhandler(CSRFError)
def csrf_error(e):
    flash("Your session expired before this form was submitted. Please try again.", "error")
    return redirect(request.referrer or url_for("home"))


@app.errorhandler(404)
def not_found(e):
    return render_template("public/404.html"), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("public/500.html"), 500


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=not IS_PRODUCTION, port=5000)
