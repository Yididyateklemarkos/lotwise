"""
Lotwise — Main Application
Run with: python app.py
Visit: http://localhost:5000
"""
import os
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, abort
from flask_login import (LoginManager, login_user, logout_user, login_required,
                          current_user)
from werkzeug.utils import secure_filename

from models import (db, User, VerificationDocument, Listing, ListingPhoto, SourcingRequest,
                     SourcingRequestPhoto, Inquiry, Connection, Message, TrackRecordEntry, PaymentOrder,
                     TIER_LISTING_LIMITS, TIER_PRICE_USD, BUYER_TIER_PRICE_USD,
                     SUPPLIER_TIER_FEATURES, BUYER_TIER_FEATURES)
import nowpayments
import uuid

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
    """Requires the account to be approved (but not necessarily paid yet)."""
    from functools import wraps

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("login"))
        if not current_user.is_verified():
            flash("Your application is still under review.", "warning")
            return redirect(url_for("dashboard"))
        return view_func(*args, **kwargs)
    return wrapper


def member_required(view_func):
    """Requires approved AND an active paid plan — full marketplace access."""
    from functools import wraps

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("login"))
        if not current_user.is_verified():
            flash("Your application is still under review.", "warning")
            return redirect(url_for("dashboard"))
        current_user.sync_expiry()
        if not current_user.is_active_member():
            flash("Choose a plan to activate full marketplace access.", "info")
            return redirect(url_for("billing_upgrade"))
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
        "volume_usd": "150M+",
    }
    if current_user.is_authenticated and current_user.is_active_member():
        # Logged-in, active members land straight on the real marketplace —
        # not a separate trending preview.
        return redirect(url_for("browse"))

    sample_listings = Listing.query.filter_by(is_active=True, is_sold=False).order_by(
        Listing.created_at.desc()).limit(3).all()
    return render_template("public/home.html", stats=stats, listings=sample_listings)


@app.route("/about")
def about():
    return render_template("public/about.html")


@app.route("/faq")
def faq():
    return render_template("public/faq.html")


@app.route("/contact")
def contact():
    return render_template("public/contact.html")


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


@app.route("/refunds")
def refunds():
    return render_template("public/refunds.html")


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
        contact_method = request.form.get("contact_method", "").strip()
        contact_value = request.form.get("contact_value", "").strip()
        agree_terms = request.form.get("agree_terms")

        if not email or not password or not company_name or account_type not in ("supplier", "buyer"):
            flash("Please fill in all required fields.", "error")
            return redirect(url_for("signup"))

        if not contact_method or not contact_value:
            flash("Please provide a WhatsApp or Telegram number so verified matches can reach you.", "error")
            return redirect(url_for("signup"))

        if not agree_terms:
            flash("Please confirm your email is correct and agree to the Terms of Service to continue.", "error")
            return redirect(url_for("signup"))

        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "error")
            return redirect(url_for("signup"))

        user = User(
            email=email,
            company_name=company_name,
            account_type=account_type,
            country=country,
            contact_method=contact_method,
            contact_value=contact_value,
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
        agree_terms = request.form.get("agree_terms")

        if not agree_terms:
            flash("Please confirm the document is accurate and agree to the Terms of Service before submitting.", "error")
            return redirect(url_for("verification_upload"))

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
        flash("Document uploaded. Our team will review your application shortly.", "success")
        return redirect(url_for("dashboard"))

    return render_template("dashboard/verification_upload.html")


# ---------------------------------------------------------------------------
# Dashboard (supplier + buyer)
# ---------------------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    current_user.sync_expiry()
    if current_user.is_admin:
        items = (Listing.query.filter_by(user_id=current_user.id).all() +
                 SourcingRequest.query.filter_by(user_id=current_user.id).all())
        items.sort(key=lambda x: x.created_at, reverse=True)
    elif current_user.account_type == "supplier":
        items = Listing.query.filter_by(user_id=current_user.id).order_by(
            Listing.created_at.desc()).all()
    else:
        items = SourcingRequest.query.filter_by(user_id=current_user.id).order_by(
            SourcingRequest.created_at.desc()).all()

    connections = Connection.query.filter(
        (Connection.supplier_id == current_user.id) | (Connection.buyer_id == current_user.id)
    ).order_by(Connection.created_at.desc()).all()

    return render_template("dashboard/index.html", items=items, connections=connections)


@app.route("/account/photo", methods=["POST"])
@login_required
def update_profile_photo():
    photo = request.files.get("photo")
    if not photo or not photo.filename or not allowed_file(photo.filename):
        flash("Please choose a PNG or JPG image.", "error")
        return redirect(url_for("dashboard"))

    # Remove the old photo file if there was one, so we don't accumulate orphans.
    if current_user.profile_photo_path:
        old_path = os.path.join(app.config["UPLOAD_FOLDER"], current_user.profile_photo_path)
        if os.path.exists(old_path):
            os.remove(old_path)

    filename = secure_filename(f"profile_{current_user.id}_{uuid.uuid4().hex[:8]}_{photo.filename}")
    photo.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
    current_user.profile_photo_path = filename
    db.session.commit()
    flash("Profile photo updated.", "success")
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/dashboard/listings/new", methods=["GET", "POST"])
@member_required
def new_listing():
    if current_user.account_type != "supplier" and not current_user.is_admin:
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
        db.session.flush()  # get listing.id before saving photo rows

        photos = request.files.getlist("photos")[:6]  # cap at 6 per listing
        for photo in photos:
            if photo and photo.filename and allowed_file(photo.filename):
                filename = secure_filename(f"listing_{listing.id}_{uuid.uuid4().hex[:8]}_{photo.filename}")
                photo.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
                db.session.add(ListingPhoto(listing_id=listing.id, file_path=filename))

        db.session.commit()
        flash("Listing created.", "success")
        return redirect(url_for("dashboard"))

    return render_template("dashboard/new_listing.html", limit=current_user.listing_limit())


@app.route("/dashboard/listings/<int:listing_id>/toggle-sold", methods=["POST"])
@login_required
def toggle_sold(listing_id):
    listing = Listing.query.get_or_404(listing_id)
    if listing.user_id != current_user.id:
        abort(403)
    listing.is_sold = not listing.is_sold
    db.session.commit()
    flash("Marked as sold." if listing.is_sold else "Marked as available again.", "success")
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------------------
# Track record — self-reported past deals with optional proof documents
# ---------------------------------------------------------------------------
@app.route("/dashboard/track-record", methods=["GET", "POST"])
@verified_required
def track_record():
    if request.method == "POST":
        file = request.files.get("proof_file")
        proof_path = None
        if file and file.filename and allowed_file(file.filename):
            filename = secure_filename(f"{current_user.id}_proof_{file.filename}")
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
            proof_path = filename

        entry = TrackRecordEntry(
            user_id=current_user.id,
            title=request.form.get("title", "").strip(),
            counterparty_note=request.form.get("counterparty_note", "").strip(),
            volume_fcl=request.form.get("volume_fcl", type=int),
            year=request.form.get("year", type=int),
            proof_file_path=proof_path,
        )
        db.session.add(entry)
        db.session.commit()
        flash("Added to your track record.", "success")
        return redirect(url_for("track_record"))

    entries = TrackRecordEntry.query.filter_by(user_id=current_user.id).order_by(
        TrackRecordEntry.created_at.desc()).all()
    return render_template("dashboard/track_record.html", entries=entries)


@app.route("/dashboard/requests/new", methods=["GET", "POST"])
@member_required
def new_sourcing_request():
    if current_user.account_type != "buyer" and not current_user.is_admin:
        abort(403)

    if not current_user.can_create_request() and not current_user.is_admin:
        flash(f"You've reached your plan's limit of {current_user.listing_limit()} active requests. Upgrade to add more.", "warning")
        return redirect(url_for("billing_upgrade"))

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
        db.session.flush()

        photos = request.files.getlist("photos")[:6]
        for photo in photos:
            if photo and photo.filename and allowed_file(photo.filename):
                filename = secure_filename(f"request_{req.id}_{uuid.uuid4().hex[:8]}_{photo.filename}")
                photo.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
                db.session.add(SourcingRequestPhoto(sourcing_request_id=req.id, file_path=filename))

        db.session.commit()
        flash("Sourcing request posted.", "success")
        return redirect(url_for("dashboard"))

    return render_template("dashboard/new_request.html", limit=current_user.listing_limit())


# ---------------------------------------------------------------------------
# Marketplace (verified + paid members only — one shared feed for everyone)
# ---------------------------------------------------------------------------
MARKETPLACE_CATEGORIES = ["Minerals", "Agriculture", "Chemicals", "Construction materials", "Other bulk goods"]


@app.route("/browse")
@member_required
def browse():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    item_type = request.args.get("type", "all").strip()  # all / listings / requests

    listings = []
    requests_ = []

    if item_type in ("all", "listings"):
        query = Listing.query.filter_by(is_active=True, is_sold=False)
        if category:
            query = query.filter_by(category=category)
        if q:
            like = f"%{q}%"
            query = query.filter(db.or_(Listing.title.ilike(like), Listing.description.ilike(like)))
        listings = query.order_by(Listing.is_urgent.desc(), Listing.created_at.desc()).all()

    if item_type in ("all", "requests"):
        query = SourcingRequest.query.filter_by(is_active=True)
        if category:
            query = query.filter_by(category=category)
        if q:
            like = f"%{q}%"
            query = query.filter(db.or_(SourcingRequest.title.ilike(like), SourcingRequest.description.ilike(like)))
        requests_ = query.order_by(SourcingRequest.is_urgent.desc(), SourcingRequest.created_at.desc()).all()

    # Merge into one feed, newest/urgent first, tagged by type so the
    # template knows which card layout and action button to use.
    feed = (
        [{"kind": "listing", "item": item} for item in listings] +
        [{"kind": "request", "item": item} for item in requests_]
    )
    feed.sort(key=lambda x: (not x["item"].is_urgent, x["item"].created_at), reverse=True)

    return render_template(
        "dashboard/marketplace.html",
        feed=feed, q=q, category=category, item_type=item_type,
        categories=MARKETPLACE_CATEGORIES,
    )


@app.route("/listing/<int:listing_id>/inquire", methods=["POST"])
@member_required
def inquire(listing_id):
    listing = Listing.query.get_or_404(listing_id)
    if listing.user_id == current_user.id:
        abort(403)  # can't inquire on your own listing

    inquiry = Inquiry(listing_id=listing.id, buyer_id=current_user.id,
                       message=request.form.get("message", ""))
    db.session.add(inquiry)
    db.session.flush()

    # Both verified -> auto-create the connection (in-platform thread, no contact info shared)
    connection = Connection(inquiry_id=inquiry.id, supplier_id=listing.user_id,
                             buyer_id=current_user.id)
    db.session.add(connection)
    db.session.commit()

    flash("Inquiry sent — you now have this supplier's contact info in the connection thread.", "success")
    return redirect(url_for("connection_thread", connection_id=connection.id))


@app.route("/request/<int:request_id>/respond", methods=["POST"])
@member_required
def respond_to_request(request_id):
    """Mirror of inquire(), but for a supplier responding to a buyer's
    sourcing request instead of a buyer inquiring on a listing."""
    req = SourcingRequest.query.get_or_404(request_id)
    if req.user_id == current_user.id:
        abort(403)  # can't respond to your own request

    inquiry = Inquiry(sourcing_request_id=req.id, buyer_id=current_user.id,
                       message=request.form.get("message", ""))
    db.session.add(inquiry)
    db.session.flush()

    # Here current_user is the supplier responding; req.user_id is the buyer.
    connection = Connection(inquiry_id=inquiry.id, supplier_id=current_user.id,
                             buyer_id=req.user_id)
    db.session.add(connection)
    db.session.commit()

    flash("Response sent — you now have this buyer's contact info in the connection thread.", "success")
    return redirect(url_for("connection_thread", connection_id=connection.id))


@app.route("/connection/<int:connection_id>")
@member_required
def connection_thread(connection_id):
    conn = Connection.query.get_or_404(connection_id)
    if current_user.id not in (conn.supplier_id, conn.buyer_id):
        abort(403)
    messages = Message.query.filter_by(connection_id=conn.id).order_by(Message.sent_at).all()
    other_id = conn.buyer_id if current_user.id == conn.supplier_id else conn.supplier_id
    other_user = User.query.get(other_id)
    return render_template("dashboard/connection_thread.html", connection=conn, messages=messages,
                            other_user=other_user)


@app.route("/connection/<int:connection_id>/message", methods=["POST"])
@member_required
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
@member_required
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
# Billing (plan selection -> payment method -> NOWPayments crypto / PayPal)
# ---------------------------------------------------------------------------
@app.route("/billing/upgrade")
@verified_required
def billing_upgrade():
    """Shows the 3 tier options for this account type. Choosing one goes to
    the payment-method page, not straight to a checkout."""
    is_buyer = current_user.account_type == "buyer" and not current_user.is_admin
    prices = BUYER_TIER_PRICE_USD if is_buyer else TIER_PRICE_USD
    features = BUYER_TIER_FEATURES if is_buyer else SUPPLIER_TIER_FEATURES
    return render_template(
        "dashboard/billing.html",
        prices=prices,
        features=features,
    )


@app.route("/billing/pay/<tier>")
@verified_required
def billing_pay(tier):
    """Payment-method choice page: Crypto (NOWPayments) or PayPal, shown
    after a tier has been picked."""
    prices = TIER_PRICE_USD if current_user.account_type == "supplier" else BUYER_TIER_PRICE_USD
    if tier not in prices:
        abort(404)
    return render_template(
        "dashboard/payment_method.html",
        tier=tier,
        amount=prices[tier],
        crypto_configured=nowpayments.is_nowpayments_configured(),
    )


@app.route("/billing/pay/<tier>/crypto")
@verified_required
def billing_pay_crypto(tier):
    """Creates a NOWPayments invoice and redirects the user to it."""
    prices = TIER_PRICE_USD if current_user.account_type == "supplier" else BUYER_TIER_PRICE_USD
    if tier not in prices:
        abort(404)
    if not nowpayments.is_nowpayments_configured():
        flash("Crypto payment isn't connected yet — check back shortly.", "warning")
        return redirect(url_for("billing_pay", tier=tier))

    amount = prices[tier]
    order_id = f"lw-{current_user.id}-{tier}-{uuid.uuid4().hex[:10]}"

    order = PaymentOrder(
        order_id=order_id,
        user_id=current_user.id,
        tier=tier,
        amount_usd=amount,
        provider="nowpayments",
        status="pending",
    )
    db.session.add(order)
    db.session.commit()

    try:
        invoice = nowpayments.create_invoice(
            amount_usd=amount,
            order_id=order_id,
            order_description=f"Lotwise {current_user.account_type} {tier} plan — monthly",
            success_url=url_for("billing_success", order_id=order_id, _external=True),
            cancel_url=url_for("billing_failed", order_id=order_id, _external=True),
            ipn_callback_url=url_for("billing_webhook_nowpayments", _external=True),
        )
    except Exception:
        order.status = "failed"
        db.session.commit()
        flash("Couldn't start the crypto payment — please try again.", "error")
        return redirect(url_for("billing_pay", tier=tier))

    invoice_url = invoice.get("invoice_url")
    if not invoice_url:
        order.status = "failed"
        db.session.commit()
        flash("Couldn't start the crypto payment — please try again.", "error")
        return redirect(url_for("billing_pay", tier=tier))

    return redirect(invoice_url)


@app.route("/billing/success")
def billing_success():
    order_id = request.args.get("order_id", "")
    order = PaymentOrder.query.filter_by(order_id=order_id).first() if order_id else None
    # NOWPayments only gives us success_url/cancel_url, not a third "partial"
    # option — so even on the success redirect, check the real order status
    # in case the IPN already told us it was actually a partial payment.
    if order and order.status == "partially_paid":
        return render_template("dashboard/payment_partial.html", order=order)
    return render_template("dashboard/payment_success.html", order=order)


@app.route("/billing/failed")
def billing_failed():
    order_id = request.args.get("order_id", "")
    order = PaymentOrder.query.filter_by(order_id=order_id).first() if order_id else None
    if order and order.status == "partially_paid":
        return render_template("dashboard/payment_partial.html", order=order)
    return render_template("dashboard/payment_failed.html", order=order)


@app.route("/billing/webhook/nowpayments", methods=["POST"])
def billing_webhook_nowpayments():
    """NOWPayments calls this (IPN) on payment status changes. Verifies the
    HMAC signature before trusting anything in the payload."""
    signature = request.headers.get("x-nowpayments-sig", "")
    try:
        payload = request.get_json(force=True, silent=False)
    except Exception:
        return jsonify({"error": "invalid body"}), 400

    if not payload or not nowpayments.verify_ipn_signature(payload, signature):
        return jsonify({"error": "invalid signature"}), 401

    order_id = payload.get("order_id", "")
    payment_status = payload.get("payment_status", "")

    order = PaymentOrder.query.filter_by(order_id=order_id).first()
    if order:
        if payment_status in ("finished", "confirmed"):
            order.status = "finished"
            order.amount_received = payload.get("actually_paid") or order.amount_usd
            user = order.user
            # Crypto payments are one-time, not auto-recurring — extend 30
            # days from now, or from the current expiry if they're renewing
            # a still-active plan early (so early renewal doesn't cost them
            # days).
            now = datetime.utcnow()
            base = user.subscription_renews_at if (
                user.subscription_renews_at and user.subscription_renews_at > now
            ) else now
            user.subscription_status = "active"
            user.subscription_tier = order.tier
            user.subscription_renews_at = base + timedelta(days=30)
        elif payment_status == "partially_paid":
            # Underpaid — do NOT activate. Record how much came in so the
            # customer can see the shortfall on the status page.
            order.status = "partially_paid"
            order.amount_received = payload.get("actually_paid")
        elif payment_status in ("failed", "expired", "refunded"):
            order.status = "failed"
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


@app.route("/admin/users")
@admin_required
def admin_users():
    """Every user, with contact info visible and a payment-bypass toggle —
    separate from the pending-review queue on the main admin dashboard."""
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin/users.html", users=users)


@app.route("/admin/user/<int:user_id>/toggle-payment-exempt", methods=["POST"])
@admin_required
def admin_toggle_payment_exempt(user_id):
    user = User.query.get_or_404(user_id)
    user.payment_exempt = not user.payment_exempt
    db.session.commit()
    flash(f"{user.company_name} {'now bypasses payment' if user.payment_exempt else 'no longer bypasses payment'}.", "info")
    return redirect(request.referrer or url_for("admin_users"))


# ---------------------------------------------------------------------------
# Admin — listings & sourcing requests (view all, delete any, add on behalf)
# ---------------------------------------------------------------------------
@app.route("/admin/listings")
@admin_required
def admin_listings():
    listings = Listing.query.order_by(Listing.created_at.desc()).all()
    return render_template("admin/listings.html", listings=listings)


@app.route("/admin/listings/new", methods=["GET", "POST"])
@admin_required
def admin_new_listing():
    """Lets the admin post a listing directly — e.g. showcase/demo content,
    or on behalf of a supplier who can't do it themselves yet. Attributed to
    the admin's own account."""
    if request.method == "POST":
        listing = Listing(
            user_id=current_user.id,
            category=request.form.get("category"),
            title=request.form.get("title"),
            description=request.form.get("description"),
            grade_spec=request.form.get("grade_spec"),
            quantity_fcl=request.form.get("quantity_fcl", type=int),
            origin_country=request.form.get("origin_country"),
            is_urgent=bool(request.form.get("is_urgent")),
        )
        db.session.add(listing)
        db.session.flush()

        photos = request.files.getlist("photos")[:6]
        for photo in photos:
            if photo and photo.filename and allowed_file(photo.filename):
                filename = secure_filename(f"listing_{listing.id}_{uuid.uuid4().hex[:8]}_{photo.filename}")
                photo.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
                db.session.add(ListingPhoto(listing_id=listing.id, file_path=filename))

        db.session.commit()
        flash("Listing created.", "success")
        return redirect(url_for("admin_listings"))

    return render_template("admin/new_listing.html")


@app.route("/admin/listings/<int:listing_id>/delete", methods=["POST"])
@admin_required
def admin_delete_listing(listing_id):
    listing = Listing.query.get_or_404(listing_id)
    if listing.inquiries:
        flash("Can't remove this listing — it has inquiries/connections attached, which are part of the trade record. Mark it sold or inactive instead.", "error")
        return redirect(url_for("admin_listings"))
    for photo in listing.photos:
        photo_path = os.path.join(app.config["UPLOAD_FOLDER"], photo.file_path)
        if os.path.exists(photo_path):
            os.remove(photo_path)
    db.session.delete(listing)
    db.session.commit()
    flash("Listing removed.", "info")
    return redirect(url_for("admin_listings"))


@app.route("/admin/requests")
@admin_required
def admin_requests():
    requests_list = SourcingRequest.query.order_by(SourcingRequest.created_at.desc()).all()
    return render_template("admin/requests.html", requests_list=requests_list)


@app.route("/admin/requests/new", methods=["GET", "POST"])
@admin_required
def admin_new_request():
    """Lets the admin post a sourcing request directly, attributed to the
    admin's own account — same rationale as admin_new_listing."""
    if request.method == "POST":
        req = SourcingRequest(
            user_id=current_user.id,
            category=request.form.get("category"),
            title=request.form.get("title"),
            description=request.form.get("description"),
            quantity_fcl=request.form.get("quantity_fcl", type=int),
            target_price_note=request.form.get("target_price_note"),
            is_urgent=bool(request.form.get("is_urgent")),
        )
        db.session.add(req)
        db.session.commit()
        flash("Sourcing request posted.", "success")
        return redirect(url_for("admin_requests"))

    return render_template("admin/new_request.html")


@app.route("/admin/requests/<int:request_id>/delete", methods=["POST"])
@admin_required
def admin_delete_request(request_id):
    req = SourcingRequest.query.get_or_404(request_id)
    db.session.delete(req)
    db.session.commit()
    flash("Sourcing request removed.", "info")
    return redirect(url_for("admin_requests"))


@app.route("/admin/connections")
@admin_required
def admin_connections():
    connections = Connection.query.order_by(Connection.created_at.desc()).all()
    return render_template("admin/connections.html", connections=connections)


# ---------------------------------------------------------------------------
# CLI helper — create the first admin account
# ---------------------------------------------------------------------------
@app.route("/setup-admin")
def setup_admin():
    """One-time browser-based admin promotion for hosts without shell access
    (e.g. Render's free tier). Protected by a secret key set as an env var.
    Remove the SETUP_ADMIN_KEY env var (or this route) once you've used it."""
    setup_key = os.environ.get("SETUP_ADMIN_KEY", "")
    if not setup_key:
        abort(404)  # disabled unless explicitly configured
    if request.args.get("key", "") != setup_key:
        abort(403)

    email = request.args.get("email", "").strip().lower()
    if not email:
        return "Add &email=you@example.com to the URL.", 400

    user = User.query.filter_by(email=email).first()
    if not user:
        return f"No account found for {email}. Sign up on the site first, then reload this link.", 404

    user.is_admin = True
    user.verification_status = "approved"
    db.session.commit()
    return f"{email} is now an admin. You can remove SETUP_ADMIN_KEY from your environment variables now."


@app.route("/setup-migrate")
def setup_migrate():
    """One-time browser-based schema fix for hosts without shell access
    (e.g. Render's free tier) — same pattern and same secret key as
    /setup-admin. Makes listings.id nullable on inquiries so a supplier can
    respond to a sourcing request (which has no listing_id) without hitting
    a NOT NULL constraint from before this feature existed.
    Safe to reload — running it again on an already-migrated DB is a no-op."""
    setup_key = os.environ.get("SETUP_ADMIN_KEY", "")
    if not setup_key:
        abort(404)
    if request.args.get("key", "") != setup_key:
        abort(403)

    from sqlalchemy import text
    try:
        db.session.execute(text("ALTER TABLE inquiries ALTER COLUMN listing_id DROP NOT NULL"))
        db.session.execute(text("ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS sourcing_request_id INTEGER REFERENCES sourcing_requests(id)"))
        db.session.commit()
        return "Migration applied: inquiries.listing_id is now nullable, sourcing_request_id column confirmed present."
    except Exception as e:
        db.session.rollback()
        return f"Migration failed or already applied: {e}", 500


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


# Runs on import — under `python app.py` AND under gunicorn — so the
# tables always exist before the first request, no matter how the app starts.
with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
