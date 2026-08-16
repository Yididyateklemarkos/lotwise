"""
Lotwise — Database Models
Covers: users (supplier/buyer), verification, listings, sourcing requests,
tier standing, connections, and subscription records.
"""
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

# ---------------------------------------------------------------------------
# Enums (stored as plain strings for SQLite simplicity)
# ---------------------------------------------------------------------------
ACCOUNT_TYPES = ("supplier", "buyer")
VERIFICATION_STATUSES = ("pending", "approved", "disapproved")
SUB_TIERS = ("standard", "plus", "premium")
SUB_STATUSES = ("inactive", "active", "past_due", "cancelled")
TIER_GRADES = ("A", "B", "C")
CONNECTION_STATUSES = ("open", "reported_closed", "reported_no_deal")

TIER_LISTING_LIMITS = {"standard": 3, "plus": 7, "premium": 999}
TIER_PRICE_USD = {"standard": 39, "plus": 79, "premium": 139}
BUYER_TIER_PRICE_USD = {"standard": 39, "plus": 79, "premium": 139}

SUPPLIER_TIER_FEATURES = {
    "standard": [
        "Up to 3 active listings",
        "Full marketplace access — browse every listing and sourcing request",
        "Direct contact info exchange on every matched connection",
        "Track record profile",
    ],
    "plus": [
        "Up to 7 active listings",
        "Everything in Standard",
        "Urgent-sale flag on your listings (priority placement)",
        "Priority tier grade eligibility (B and above)",
    ],
    "premium": [
        "Unlimited active listings",
        "Everything in Plus",
        "Top placement across the marketplace",
        "Priority tier grade eligibility (A)",
    ],
}

BUYER_TIER_FEATURES = {
    "standard": [
        "Up to 3 active sourcing requests",
        "Full marketplace access — browse every listing and sourcing request",
        "Direct contact info exchange on every matched connection",
        "Track record profile",
    ],
    "plus": [
        "Up to 7 active sourcing requests",
        "Everything in Standard",
        "Urgent-need flag on your requests (priority placement)",
        "Priority tier grade eligibility (B and above)",
    ],
    "premium": [
        "Unlimited active sourcing requests",
        "Everything in Plus",
        "Top placement across the marketplace",
        "Priority tier grade eligibility (A)",
    ],
}


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=True)  # null if OAuth-only
    auth_provider = db.Column(db.String(20), default="manual")  # manual/google/apple

    company_name = db.Column(db.String(255), nullable=False)
    account_type = db.Column(db.String(20), nullable=False)  # supplier / buyer
    country = db.Column(db.String(100))
    contact_name = db.Column(db.String(255))
    phone = db.Column(db.String(50))
    contact_method = db.Column(db.String(20))  # whatsapp / telegram / phone
    contact_value = db.Column(db.String(100))  # the number/handle itself

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Verification
    verification_status = db.Column(db.String(20), default="pending")
    verification_reviewed_at = db.Column(db.DateTime)
    verification_reviewer_note = db.Column(db.Text)

    # Subscription
    subscription_tier = db.Column(db.String(20), default="standard")
    subscription_status = db.Column(db.String(20), default="inactive")
    subscription_renews_at = db.Column(db.DateTime)

    # Tier standing (earned, separate from paid subscription tier)
    tier_grade = db.Column(db.String(1), default="A")
    closed_deals_count = db.Column(db.Integer, default=0)
    last_activity_at = db.Column(db.DateTime, default=datetime.utcnow)

    is_admin = db.Column(db.Boolean, default=False)
    payment_exempt = db.Column(db.Boolean, default=False)  # admin-granted bypass, ignores plan/expiry
    profile_photo_path = db.Column(db.String(500))

    listings = db.relationship("Listing", backref="owner", lazy=True,
                                foreign_keys="Listing.user_id")
    sourcing_requests = db.relationship("SourcingRequest", backref="owner", lazy=True,
                                         foreign_keys="SourcingRequest.user_id")
    documents = db.relationship("VerificationDocument", backref="user", lazy=True)
    track_record = db.relationship("TrackRecordEntry", backref="user", lazy=True)

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, raw_password)

    def listing_limit(self):
        return TIER_LISTING_LIMITS.get(self.subscription_tier, 3)

    def can_create_listing(self):
        active_count = Listing.query.filter_by(user_id=self.id, is_active=True).count()
        return active_count < self.listing_limit()

    def can_create_request(self):
        active_count = SourcingRequest.query.filter_by(user_id=self.id, is_active=True).count()
        return active_count < self.listing_limit()

    def is_verified(self):
        if self.is_admin:
            return True
        return self.verification_status == "approved"

    def is_active_member(self):
        """Approved AND paid AND not expired — full marketplace access.
        Admins always pass, so you can see the site exactly as live members
        see it without having to pay yourself. payment_exempt users skip
        only the payment/expiry check (an admin-granted comp), but still
        need to be verified like anyone else.
        Crypto payments are one-time, not auto-recurring, so 'active' also
        requires subscription_renews_at to still be in the future — once it
        lapses, this flips to False on its own without a background job."""
        if self.is_admin:
            return True
        if self.verification_status != "approved":
            return False
        if self.payment_exempt:
            return True
        if self.subscription_status != "active":
            return False
        if self.subscription_renews_at and self.subscription_renews_at < datetime.utcnow():
            return False
        return True

    def days_until_expiry(self):
        """Whole days left on the current paid plan, or None if not on one."""
        if not self.subscription_renews_at:
            return None
        delta = self.subscription_renews_at - datetime.utcnow()
        return max(0, delta.days)

    def is_expiring_soon(self, within_days=5):
        days = self.days_until_expiry()
        return days is not None and 0 <= days <= within_days

    def sync_expiry(self):
        """Flips subscription_status back to 'inactive' in the DB once the
        30-day crypto payment window has actually passed. Cheap to call on
        every gated request — just a datetime comparison — so status stays
        accurate everywhere (admin views, dashboard) rather than only being
        correct inside is_active_member()'s live check."""
        if (self.subscription_status == "active" and self.subscription_renews_at
                and self.subscription_renews_at < datetime.utcnow()):
            self.subscription_status = "inactive"
            db.session.commit()

    def to_public_dict(self):
        """Safe fields only — never expose contact info pre-connection."""
        return {
            "id": self.id,
            "company_name": self.company_name,
            "account_type": self.account_type,
            "country": self.country,
            "tier_grade": self.tier_grade,
            "subscription_tier": self.subscription_tier,
            "closed_deals_count": self.closed_deals_count,
            "verified": self.is_verified(),
        }


class PaymentOrder(db.Model):
    """Tracks a payment attempt (currently NOWPayments/crypto) so the IPN
    webhook can match a provider's order_id back to a user + tier."""
    __tablename__ = "payment_orders"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    provider_order_id = db.Column(db.String(128), nullable=True)  # e.g. PayPal's own order ID
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    tier = db.Column(db.String(20), nullable=False)
    amount_usd = db.Column(db.Integer, nullable=False)
    amount_received = db.Column(db.Float, nullable=True)  # actually_paid, in price_currency (usd)
    provider = db.Column(db.String(20), default="nowpayments")
    status = db.Column(db.String(20), default="pending")  # pending / finished / partially_paid / failed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship("User")


class VerificationDocument(db.Model):
    __tablename__ = "verification_documents"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    doc_type = db.Column(db.String(100), nullable=False)  # e.g. "business_license"
    file_path = db.Column(db.String(500), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)


class Listing(db.Model):
    __tablename__ = "listings"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    category = db.Column(db.String(100), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    grade_spec = db.Column(db.String(255))
    quantity_fcl = db.Column(db.Integer)  # minimum: 1 FCL
    origin_country = db.Column(db.String(100))
    photo_path = db.Column(db.String(500))

    is_urgent = db.Column(db.Boolean, default=False)  # Plus/Premium feature
    is_active = db.Column(db.Boolean, default=True)
    is_sold = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    inquiries = db.relationship("Inquiry", backref="listing", lazy=True)
    photos = db.relationship("ListingPhoto", backref="listing", lazy=True,
                              order_by="ListingPhoto.created_at",
                              cascade="all, delete-orphan")


class ListingPhoto(db.Model):
    """One of several photos attached to a listing."""
    __tablename__ = "listing_photos"

    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listings.id"), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ContactMessage(db.Model):
    """A message submitted via the Contact us page. No email service is
    configured, so these are stored for admin to review directly."""
    __tablename__ = "contact_messages"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class TrackRecordEntry(db.Model):
    """A supplier or buyer's self-reported past deal, shown as proof of
    performance on their profile. Optional supporting document."""
    __tablename__ = "track_record_entries"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    title = db.Column(db.String(255), nullable=False)  # e.g. "40 FCL robusta to Hamburg"
    counterparty_note = db.Column(db.String(255))  # e.g. "EU importer" (no names required)
    volume_fcl = db.Column(db.Integer)
    year = db.Column(db.Integer)
    proof_file_path = db.Column(db.String(500))  # optional: BL, invoice, cert, etc.

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class SourcingRequest(db.Model):
    __tablename__ = "sourcing_requests"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    category = db.Column(db.String(100), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    quantity_fcl = db.Column(db.Integer)
    target_price_note = db.Column(db.String(255))
    needed_by = db.Column(db.Date)

    is_urgent = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    photos = db.relationship("SourcingRequestPhoto", backref="sourcing_request", lazy=True,
                              order_by="SourcingRequestPhoto.created_at",
                              cascade="all, delete-orphan")


class SourcingRequestPhoto(db.Model):
    """One of several reference photos attached to a buyer's sourcing request
    (e.g. a sample of the exact product/spec they're after)."""
    __tablename__ = "sourcing_request_photos"

    id = db.Column(db.Integer, primary_key=True)
    sourcing_request_id = db.Column(db.Integer, db.ForeignKey("sourcing_requests.id"), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Inquiry(db.Model):
    """A contact attempt between a buyer and supplier, triggered from either
    side: a buyer inquiring on a listing, or a supplier responding to a
    sourcing request. Exactly one of listing_id/sourcing_request_id is set.
    Triggers an in-platform connection once both are verified — no contact
    info is ever exposed by the platform outside that connection."""
    __tablename__ = "inquiries"

    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listings.id"), nullable=True)
    sourcing_request_id = db.Column(db.Integer, db.ForeignKey("sourcing_requests.id"), nullable=True)
    # "from_user" is whoever initiated contact — the buyer (on a listing) or
    # the supplier (on a sourcing request). Kept as buyer_id for backward
    # compatibility with existing data/columns.
    buyer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    sourcing_request = db.relationship("SourcingRequest", backref="inquiries", lazy=True)
    connection = db.relationship("Connection", backref="inquiry", uselist=False)


class Connection(db.Model):
    """Created once both parties are verified and matched. This is the
    self-reporting record used for tier grading — Lotwise is not a party
    to whatever happens after this point."""
    __tablename__ = "connections"

    id = db.Column(db.Integer, primary_key=True)
    inquiry_id = db.Column(db.Integer, db.ForeignKey("inquiries.id"), nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    buyer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    status = db.Column(db.String(20), default="open")  # open/reported_closed/reported_no_deal
    reported_volume_fcl = db.Column(db.Integer)
    reported_at = db.Column(db.DateTime)
    reported_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Message(db.Model):
    """In-platform messaging thread tied to a connection."""
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    connection_id = db.Column(db.Integer, db.ForeignKey("connections.id"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
