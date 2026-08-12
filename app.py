"""
Lotwise — Main Application
Run with: python app.py
Visit: http://localhost:5000
"""
import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, abort
from flask_login import (LoginManager, login_user, logout_user, login_required,
                          current_user)
from werkzeug.utils import secure_filename

from models import (db, User, VerificationDocument, Listing, SourcingRequest,
                     Inquiry, Connection, Message, TIER_LISTING_LIMITS,
                     TIER_PRICE_USD, BUYER_TIER_PRICE_USD)
import billing

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
database_url = os.environ.get("DATABASE_URL", "")
if database_url.startswith("postgres://"):
    # Render (and some other hosts) hand back the old-style prefix;
    # SQLAlchemy needs the postgresql:// form.
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url or ("sqlite:///" + os.path.join(BASE_DIR, "lotwise.db"))
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = UPLOAD_DIR
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10MB

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


ALLOWED_DOC_EXT = {"pdf", "png", "jpg", "jpeg"}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_DOC_EXT


def admin_required(view_func):
    from functools import wraps

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return view_func(*args, **kwargs)
    return wrapper


def verified_required(view_func):
    from functools import wraps

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("login"))
        if not current_user.is_verified():
            flash("Your account is still pending verification.", "warning")
            return redirect(url_for("dashboard"))
        return view_func(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------
@app.route("/")
def home():
    stats = {
        "deals_closed": 2140,
        "active_suppliers": 860,
        "active_buyers": 1300,
    }
    sample_listings = Listing.query.filter_by(is_active=True).order_by(
        Listing.created_at.desc()).limit(3).all()
    return render_template("public/home.html", stats=stats, listings=sample_listings)


@app.route("/pricing")
def pricing():
    return render_template("public/pricing.html",
                            supplier_prices=TIER_PRICE_USD,
                            buyer_prices=BUYER_TIER_PRICE_USD)


@app.route("/terms")
def terms():
    return render_template("public/terms.html")


@app.route("/privacy")
def privacy():
    return render_template("public/privacy.html")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        company_name = request.form.get("company_name", "").strip()
        account_type = request.form.get("account_type")
        country = request.form.get("country", "").strip()

        if not email or not password or not company_name or account_type not in ("supplier", "buyer"):
            flash("Please fill in all required fields.", "error")
            return redirect(url_for("signup"))

        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "error")
            return redirect(url_for("signup"))

        user = User(
            email=email,
            company_name=company_name,
            account_type=account_type,
            country=country,
            auth_provider="manual",
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        login_user(user)
        flash("Account created. Submit your verification documents to get listed.", "success")
        return redirect(url_for("verification_upload"))

    return render_template("auth/signup.html")


# Placeholder routes for OAuth — wire up real credentials when ready.
@app.route("/auth/google")
def auth_google():
    flash("Google sign-in needs API credentials from Google Cloud Console before this works.", "info")
    return redirect(url_for("login"))


@app.route("/auth/apple")
def auth_apple():
    flash("Apple sign-in needs credentials from the Apple Developer portal before this works.", "info")
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "error")
    return render_template("auth/login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("home"))


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
@app.route("/verification/upload", methods=["GET", "POST"])
@login_required
def verification_upload():
    if request.method == "POST":
        doc_type = request.form.get("doc_type")
        file = request.files.get("document")
        if not file or not allowed_file(file.filename):
            flash("Please upload a valid PDF or image file.", "error")
            return redirect(url_for("verification_upload"))

        filename = secure_filename(f"{current_user.id}_{doc_type}_{file.filename}")
        path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(path)

        doc = VerificationDocument(user_id=current_user.id, doc_type=doc_type,
                                    file_path=filename)
        db.session.add(doc)
        current_user.verification_status = "pending"
        db.session.commit()
        flash("Document uploaded. We review submissions within 48 hours.", "success")
        return redirect(url_for("dashboard"))

    return render_template("dashboard/verification_upload.html")


# ---------------------------------------------------------------------------
# Dashboard (supplier + buyer)
# ---------------------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    if current_user.account_type == "supplier":
        items = Listing.query.filter_by(user_id=current_user.id).order_by(
            Listing.created_at.desc()).all()
    else:
        items = SourcingRequest.query.filter_by(user_id=current_user.id).order_by(
            SourcingRequest.created_at.desc()).all()

    connections = Connection.query.filter(
        (Connection.supplier_id == current_user.id) | (Connection.buyer_id == current_user.id)
    ).order_by(Connection.created_at.desc()).all()

    return render_template("dashboard/index.html", items=items, connections=connections)


@app.route("/dashboard/listings/new", methods=["GET", "POST"])
@verified_required
def new_listing():
    if current_user.account_type != "supplier":
        abort(403)
    if not current_user.can_create_listing():
        flash(f"You've reached your plan's limit of {current_user.listing_limit()} listings. Upgrade to add more.", "warning")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        listing = Listing(
            user_id=current_user.id,
            category=request.form.get("category"),
            title=request.form.get("title"),
            description=request.form.get("description"),
            grade_spec=request.form.get("grade_spec"),
            quantity_fcl=request.form.get("quantity_fcl", type=int),
            origin_country=request.form.get("origin_country"),
            is_urgent=bool(request.form.get("is_urgent")) and current_user.subscription_tier != "standard",
        )
        db.session.add(listing)
        db.session.commit()
        flash("Listing created.", "success")
        return redirect(url_for("dashboard"))

    return render_template("dashboard/new_listing.html", limit=current_user.listing_limit())


@app.route("/dashboard/requests/new", methods=["GET", "POST"])
@verified_required
def new_sourcing_request():
    if current_user.account_type != "buyer":
        abort(403)

    if request.method == "POST":
        req = SourcingRequest(
            user_id=current_user.id,
            category=request.form.get("category"),
            title=request.form.get("title"),
            description=request.form.get("description"),
            quantity_fcl=request.form.get("quantity_fcl", type=int),
            target_price_note=request.form.get("target_price_note"),
            is_urgent=bool(request.form.get("is_urgent")) and current_user.subscription_tier != "standard",
        )
        db.session.add(req)
        db.session.commit()
        flash("Sourcing request posted.", "success")
        return redirect(url_for("dashboard"))

    return render_template("dashboard/new_request.html")


# ---------------------------------------------------------------------------
# Browse (verified users only — nothing public per platform policy)
# ---------------------------------------------------------------------------
@app.route("/browse")
@verified_required
def browse():
    if current_user.account_type == "buyer":
        items = Listing.query.filter_by(is_active=True).order_by(
            Listing.is_urgent.desc(), Listing.created_at.desc()).all()
        return render_template("dashboard/browse_listings.html", items=items)
    else:
        items = SourcingRequest.query.filter_by(is_active=True).order_by(
            SourcingRequest.is_urgent.desc(), SourcingRequest.created_at.desc()).all()
        return render_template("dashboard/browse_requests.html", items=items)


@app.route("/listing/<int:listing_id>/inquire", methods=["POST"])
@verified_required
def inquire(listing_id):
    if current_user.account_type != "buyer":
        abort(403)
    listing = Listing.query.get_or_404(listing_id)

    inquiry = Inquiry(listing_id=listing.id, buyer_id=current_user.id,
                       message=request.form.get("message", ""))
    db.session.add(inquiry)
    db.session.flush()

    # Both verified -> auto-create the connection (in-platform thread, no contact info shared)
    connection = Connection(inquiry_id=inquiry.id, supplier_id=listing.user_id,
                             buyer_id=current_user.id)
    db.session.add(connection)
    db.session.commit()

    flash("Inquiry sent. A connection thread has been opened with the supplier.", "success")
    return redirect(url_for("dashboard"))


@app.route("/connection/<int:connection_id>")
@verified_required
def connection_thread(connection_id):
    conn = Connection.query.get_or_404(connection_id)
    if current_user.id not in (conn.supplier_id, conn.buyer_id):
        abort(403)
    messages = Message.query.filter_by(connection_id=conn.id).order_by(Message.sent_at).all()
    return render_template("dashboard/connection_thread.html", connection=conn, messages=messages)


@app.route("/connection/<int:connection_id>/message", methods=["POST"])
@verified_required
def send_message(connection_id):
    conn = Connection.query.get_or_404(connection_id)
    if current_user.id not in (conn.supplier_id, conn.buyer_id):
        abort(403)
    body = request.form.get("body", "").strip()
    if body:
        db.session.add(Message(connection_id=conn.id, sender_id=current_user.id, body=body))
        db.session.commit()
    return redirect(url_for("connection_thread", connection_id=conn.id))


@app.route("/connection/<int:connection_id>/report", methods=["POST"])
@verified_required
def report_connection(connection_id):
    """Self-reporting loop that feeds the tier grading system."""
    conn = Connection.query.get_or_404(connection_id)
    if current_user.id not in (conn.supplier_id, conn.buyer_id):
        abort(403)

    outcome = request.form.get("outcome")  # "closed" or "no_deal"
    conn.status = "reported_closed" if outcome == "closed" else "reported_no_deal"
    conn.reported_volume_fcl = request.form.get("volume_fcl", type=int)
    conn.reported_at = datetime.utcnow()
    conn.reported_by_user_id = current_user.id

    if conn.status == "reported_closed":
        supplier = User.query.get(conn.supplier_id)
        buyer = User.query.get(conn.buyer_id)
        for u in (supplier, buyer):
            u.closed_deals_count = (u.closed_deals_count or 0) + 1
            # simple grading thresholds — tune later
            if u.closed_deals_count >= 15:
                u.tier_grade = "A"
            elif u.closed_deals_count >= 5:
                u.tier_grade = "B"
            u.last_activity_at = datetime.utcnow()

    db.session.commit()
    flash("Thanks — this updates your tier standing.", "success")
    return redirect(url_for("connection_thread", connection_id=conn.id))


# ---------------------------------------------------------------------------
# Billing (Paddle)
# ---------------------------------------------------------------------------
@app.route("/billing/upgrade")
@login_required
def billing_upgrade():
    """Shows tier options with a Paddle checkout button per tier.
    Renders a 'not configured yet' notice until PADDLE_* env vars are set."""
    prices = TIER_PRICE_USD if current_user.account_type == "supplier" else BUYER_TIER_PRICE_USD
    price_ids = {tier: billing.get_price_id(current_user.account_type, tier) for tier in prices}
    return render_template(
        "dashboard/billing.html",
        prices=prices,
        price_ids=price_ids,
        configured=billing.is_paddle_configured(),
        client_token=billing.PADDLE_CLIENT_TOKEN,
        environment=billing.PADDLE_ENV,
    )


@app.route("/billing/webhook", methods=["POST"])
def billing_webhook():
    """Paddle calls this on subscription/transaction events. Verifies the
    signature, then updates the matching user's subscription fields."""
    raw_body = request.get_data()
    signature = request.headers.get("Paddle-Signature", "")

    if not billing.verify_webhook_signature(raw_body, signature):
        return jsonify({"error": "invalid signature"}), 401

    event = billing.parse_webhook_event(raw_body)
    event_type = event.get("event_type", "")
    data = event.get("data", {})

    # Paddle lets you attach custom_data at checkout time — we pass the
    # internal user_id and tier there so we can match it back here.
    custom_data = data.get("custom_data") or {}
    user_id = custom_data.get("user_id")
    tier = custom_data.get("tier")

    if user_id:
        user = User.query.get(int(user_id))
        if user:
            if event_type in ("subscription.activated", "subscription.trialing", "transaction.completed"):
                user.subscription_status = "active"
                if tier in ("standard", "plus", "premium"):
                    user.subscription_tier = tier
            elif event_type == "subscription.past_due":
                user.subscription_status = "past_due"
            elif event_type in ("subscription.canceled", "subscription.paused"):
                user.subscription_status = "cancelled"
            db.session.commit()

    return jsonify({"received": True}), 200


# ---------------------------------------------------------------------------
# Admin (verification queue + connection oversight)
# ---------------------------------------------------------------------------
@app.route("/admin")
@admin_required
def admin_dashboard():
    pending = User.query.filter_by(verification_status="pending").order_by(User.created_at).all()
    stats = {
        "pending_count": len(pending),
        "approved_count": User.query.filter_by(verification_status="approved").count(),
        "total_listings": Listing.query.count(),
        "total_connections": Connection.query.count(),
    }
    return render_template("admin/dashboard.html", pending=pending, stats=stats)


@app.route("/admin/user/<int:user_id>")
@admin_required
def admin_user_detail(user_id):
    user = User.query.get_or_404(user_id)
    return render_template("admin/user_detail.html", user=user)


@app.route("/admin/user/<int:user_id>/approve", methods=["POST"])
@admin_required
def admin_approve(user_id):
    user = User.query.get_or_404(user_id)
    user.verification_status = "approved"
    user.verification_reviewed_at = datetime.utcnow()
    db.session.commit()
    flash(f"{user.company_name} approved.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/user/<int:user_id>/disapprove", methods=["POST"])
@admin_required
def admin_disapprove(user_id):
    user = User.query.get_or_404(user_id)
    user.verification_status = "disapproved"
    user.verification_reviewed_at = datetime.utcnow()
    user.verification_reviewer_note = request.form.get("note", "")
    db.session.commit()
    flash(f"{user.company_name} disapproved.", "info")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/connections")
@admin_required
def admin_connections():
    connections = Connection.query.order_by(Connection.created_at.desc()).all()
    return render_template("admin/connections.html", connections=connections)


# ---------------------------------------------------------------------------
# CLI helper — create the first admin account
# ---------------------------------------------------------------------------
@app.cli.command("create-admin")
def create_admin():
    email = input("Admin email: ").strip().lower()
    password = input("Admin password: ").strip()
    existing = User.query.filter_by(email=email).first()
    if existing:
        existing.is_admin = True
        existing.verification_status = "approved"
        db.session.commit()
        print(f"Existing user {email} promoted to admin.")
        return
    admin = User(email=email, company_name="Lotwise Admin", account_type="supplier",
                 is_admin=True, verification_status="approved")
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    print(f"Admin account created: {email}")


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5000)
