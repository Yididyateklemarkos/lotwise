"""
Lotwise — Main Application
Run with: python app.py
Visit: http://localhost:5000

Lotwise is a personal sourcing and trade advisory front door: every
visitor action becomes a Lead for me to follow up on directly. There is
no public login, no self-serve marketplace, and no payment is ever
processed on the site.
"""
import os
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, abort
from flask_login import (LoginManager, login_user, logout_user, login_required,
                          current_user)
from werkzeug.utils import secure_filename

from models import db, AdminUser, Lead, LeadPhoto, LEAD_TYPE_LABELS, COUNTRIES, country_flag_emoji

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.jinja_env.filters["flag"] = country_flag_emoji
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
database_url = os.environ.get("DATABASE_URL", "")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url or ("sqlite:///" + os.path.join(BASE_DIR, "lotwise.db"))
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = UPLOAD_DIR
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10MB

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "admin_login"


@login_manager.user_loader
def load_user(user_id):
    return AdminUser.query.get(int(user_id))


ALLOWED_IMG_EXT = {"png", "jpg", "jpeg"}


def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMG_EXT


def save_upload(file_storage):
    """Saves an uploaded image with a collision-safe filename, returns the
    stored relative filename or None if nothing usable was uploaded."""
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_image(file_storage.filename):
        return None
    safe_name = secure_filename(file_storage.filename)
    stored_name = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}_{safe_name}"
    file_storage.save(os.path.join(UPLOAD_DIR, stored_name))
    return stored_name


def admin_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("admin_login"))
        return view_func(*args, **kwargs)
    return wrapper


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
    return render_template("public/contact.html", telegram_url="https://t.me/+csEU8zHsMyk4MTU0")


@app.route("/request-credentials", methods=["GET", "POST"])
def request_credentials():
    if request.method == "POST":
        common = _lead_common_fields(request.form)
        message = request.form.get("message", "").strip()
        if not _require(common, "name", "email", "company_name"):
            flash("Please fill in your name, company, and email.", "error")
            return redirect(url_for("request_credentials"))
        lead = Lead(lead_type="credentials", description=message, **common)
        db.session.add(lead)
        db.session.commit()
        return redirect(url_for("thank_you", type="credentials"))
    return render_template("public/request-credentials.html", countries=COUNTRIES)


@app.route("/sourcing-request", methods=["GET", "POST"])
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
def admin_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = AdminUser.query.filter_by(email=email).first()
        if user and user.check_password(password):
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
# One-time setup helpers (safe to keep — mirror the old setup routes so a
# fresh Render deploy can create tables without shell access).
# ---------------------------------------------------------------------------
@app.route("/setup-migrate")
def setup_migrate():
    db.create_all()
    return "Tables created/verified."


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5000)
