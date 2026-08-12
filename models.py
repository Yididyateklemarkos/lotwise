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

    listings = db.relationship("Listing", backref="owner", lazy=True,
                                foreign_keys="Listing.user_id")
    sourcing_requests = db.relationship("SourcingRequest", backref="owner", lazy=True,
                                         foreign_keys="SourcingRequest.user_id")
    documents = db.relationship("VerificationDocument", backref="user", lazy=True)

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

    def is_verified(self):
        return self.verification_status == "approved"

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

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    inquiries = db.relationship("Inquiry", backref="listing", lazy=True)


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


class Inquiry(db.Model):
    """A buyer's inquiry against a listing. Triggers an in-platform connection
    once the buyer is verified — no contact info is ever exposed by the platform."""
    __tablename__ = "inquiries"

    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listings.id"), nullable=False)
    buyer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
