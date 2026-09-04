"""
Lotwise — Database Models

Lotwise is now a sourcing/trade advisory front door, not a self-serve
marketplace: every visitor action (sourcing something, booking a
consultation or meeting, or submitting supply) becomes a
Lead that gets followed up on by hand. No payments are ever processed
on the site.
"""
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

# ---------------------------------------------------------------------------
# Enums (stored as plain strings for SQLite/Postgres simplicity)
# ---------------------------------------------------------------------------
LEAD_TYPES = ("sourcing", "consultation", "meeting", "supply", "contact")
LEAD_STATUSES = ("new", "contacted", "in_progress", "closed")

LEAD_TYPE_LABELS = {
    "sourcing": "Sourcing request",
    "consultation": "Consultation request",
    "meeting": "Meeting request",
    "supply": "Supply submission",
    "contact": "General contact",
}


class AdminUser(UserMixin, db.Model):
    """A single-purpose login for the person running Lotwise to review
    incoming leads. There is no public-facing account system —
    visitors never create accounts or log in."""
    __tablename__ = "admin_users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # A hash of an unguessable, never-used password. When a login attempt
    # uses an email that doesn't exist, we still run a check against this
    # so the response takes the same time either way — otherwise a faster
    # reply on unknown emails would let someone enumerate valid admin
    # accounts by timing alone.
    _DUMMY_HASH = generate_password_hash("not-a-real-password-" + "x" * 32)

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    @staticmethod
    def dummy_check(raw_password):
        check_password_hash(AdminUser._DUMMY_HASH, raw_password)
        return False


class Lead(db.Model):
    """Every public intake form (sourcing, consultation,
    meeting, supply, contact) writes one row here. Fields not relevant to
    a given lead_type are simply left blank — one flexible table instead
    of five near-identical ones, since the admin needs to triage all of
    them side by side anyway."""
    __tablename__ = "leads"

    id = db.Column(db.Integer, primary_key=True)
    lead_type = db.Column(db.String(20), nullable=False, index=True)
    status = db.Column(db.String(20), default="new", index=True)

    # Who's asking
    name = db.Column(db.String(255), nullable=False)
    company_name = db.Column(db.String(255))
    email = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(50))
    country = db.Column(db.String(100))

    # What it's about — shared across sourcing/supply/consultation
    category = db.Column(db.String(100))
    title = db.Column(db.String(255))
    description = db.Column(db.Text)
    quantity_note = db.Column(db.String(120))     # free text: "2 FCL", "500kg", etc.
    origin_country = db.Column(db.String(100))    # supply: where the goods are
    grade_spec = db.Column(db.String(255))        # supply: grade/spec
    needed_by = db.Column(db.Date)                # sourcing: buyer's timeline
    preferred_time_note = db.Column(db.Text)       # meeting: proposed times/timezone

    admin_note = db.Column(db.Text)  # internal-only follow-up notes
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    photos = db.relationship("LeadPhoto", backref="lead", lazy=True,
                              order_by="LeadPhoto.created_at",
                              cascade="all, delete-orphan")

    @property
    def type_label(self):
        return LEAD_TYPE_LABELS.get(self.lead_type, self.lead_type)


class LeadPhoto(db.Model):
    """Optional reference photo attached to a lead (mainly supply
    submissions and sourcing requests with a sample/spec image)."""
    __tablename__ = "lead_photos"

    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# (name, ISO-2 code) — used to render a real country dropdown on the
# intake forms instead of free text, and to derive flag emoji.
COUNTRIES = [
    ("Afghanistan", "AF"), ("Albania", "AL"), ("Algeria", "DZ"), ("American Samoa", "AS"),
    ("Andorra", "AD"), ("Angola", "AO"), ("Anguilla", "AI"), ("Antarctica", "AQ"),
    ("Antigua and Barbuda", "AG"), ("Argentina", "AR"), ("Armenia", "AM"), ("Aruba", "AW"),
    ("Australia", "AU"), ("Austria", "AT"), ("Azerbaijan", "AZ"), ("Bahamas", "BS"),
    ("Bahrain", "BH"), ("Bangladesh", "BD"), ("Barbados", "BB"), ("Belarus", "BY"),
    ("Belgium", "BE"), ("Belize", "BZ"), ("Benin", "BJ"), ("Bermuda", "BM"),
    ("Bhutan", "BT"), ("Bolivia", "BO"), ("Bosnia and Herzegovina", "BA"), ("Botswana", "BW"),
    ("Brazil", "BR"), ("Brunei Darussalam", "BN"), ("Bulgaria", "BG"), ("Burkina Faso", "BF"),
    ("Burundi", "BI"), ("Cabo Verde", "CV"), ("Cambodia", "KH"), ("Cameroon", "CM"),
    ("Canada", "CA"), ("Central African Republic", "CF"), ("Chad", "TD"), ("Chile", "CL"),
    ("China", "CN"), ("Colombia", "CO"), ("Comoros", "KM"), ("Congo", "CG"),
    ("Congo, The Democratic Republic of the", "CD"), ("Costa Rica", "CR"), ("Croatia", "HR"),
    ("Cuba", "CU"), ("Cyprus", "CY"), ("Czechia", "CZ"), ("Côte d'Ivoire", "CI"),
    ("Denmark", "DK"), ("Djibouti", "DJ"), ("Dominica", "DM"), ("Dominican Republic", "DO"),
    ("Ecuador", "EC"), ("Egypt", "EG"), ("El Salvador", "SV"), ("Equatorial Guinea", "GQ"),
    ("Eritrea", "ER"), ("Estonia", "EE"), ("Eswatini", "SZ"), ("Ethiopia", "ET"),
    ("Fiji", "FJ"), ("Finland", "FI"), ("France", "FR"), ("Gabon", "GA"),
    ("Gambia", "GM"), ("Georgia", "GE"), ("Germany", "DE"), ("Ghana", "GH"),
    ("Greece", "GR"), ("Grenada", "GD"), ("Guatemala", "GT"), ("Guinea", "GN"),
    ("Guinea-Bissau", "GW"), ("Guyana", "GY"), ("Haiti", "HT"), ("Honduras", "HN"),
    ("Hong Kong", "HK"), ("Hungary", "HU"), ("Iceland", "IS"), ("India", "IN"),
    ("Indonesia", "ID"), ("Iran", "IR"), ("Iraq", "IQ"), ("Ireland", "IE"),
    ("Israel", "IL"), ("Italy", "IT"), ("Jamaica", "JM"), ("Japan", "JP"),
    ("Jordan", "JO"), ("Kazakhstan", "KZ"), ("Kenya", "KE"), ("Kiribati", "KI"),
    ("Korea, Republic of", "KR"), ("Kuwait", "KW"), ("Kyrgyzstan", "KG"), ("Laos", "LA"),
    ("Latvia", "LV"), ("Lebanon", "LB"), ("Lesotho", "LS"), ("Liberia", "LR"),
    ("Libya", "LY"), ("Liechtenstein", "LI"), ("Lithuania", "LT"), ("Luxembourg", "LU"),
    ("Madagascar", "MG"), ("Malawi", "MW"), ("Malaysia", "MY"), ("Maldives", "MV"),
    ("Mali", "ML"), ("Malta", "MT"), ("Mauritania", "MR"), ("Mauritius", "MU"),
    ("Mexico", "MX"), ("Moldova", "MD"), ("Monaco", "MC"), ("Mongolia", "MN"),
    ("Montenegro", "ME"), ("Morocco", "MA"), ("Mozambique", "MZ"), ("Myanmar", "MM"),
    ("Namibia", "NA"), ("Nepal", "NP"), ("Netherlands", "NL"), ("New Zealand", "NZ"),
    ("Nicaragua", "NI"), ("Niger", "NE"), ("Nigeria", "NG"), ("North Macedonia", "MK"),
    ("Norway", "NO"), ("Oman", "OM"), ("Pakistan", "PK"), ("Palestine", "PS"),
    ("Panama", "PA"), ("Papua New Guinea", "PG"), ("Paraguay", "PY"), ("Peru", "PE"),
    ("Philippines", "PH"), ("Poland", "PL"), ("Portugal", "PT"), ("Qatar", "QA"),
    ("Romania", "RO"), ("Russian Federation", "RU"), ("Rwanda", "RW"), ("Saudi Arabia", "SA"),
    ("Senegal", "SN"), ("Serbia", "RS"), ("Seychelles", "SC"), ("Sierra Leone", "SL"),
    ("Singapore", "SG"), ("Slovakia", "SK"), ("Slovenia", "SI"), ("Somalia", "SO"),
    ("South Africa", "ZA"), ("South Sudan", "SS"), ("Spain", "ES"), ("Sri Lanka", "LK"),
    ("Sudan", "SD"), ("Suriname", "SR"), ("Sweden", "SE"), ("Switzerland", "CH"),
    ("Syrian Arab Republic", "SY"), ("Taiwan", "TW"), ("Tajikistan", "TJ"), ("Tanzania", "TZ"),
    ("Thailand", "TH"), ("Timor-Leste", "TL"), ("Togo", "TG"), ("Trinidad and Tobago", "TT"),
    ("Tunisia", "TN"), ("Turkmenistan", "TM"), ("Türkiye", "TR"), ("Uganda", "UG"),
    ("Ukraine", "UA"), ("United Arab Emirates", "AE"), ("United Kingdom", "GB"),
    ("United States", "US"), ("Uruguay", "UY"), ("Uzbekistan", "UZ"), ("Vanuatu", "VU"),
    ("Venezuela", "VE"), ("Viet Nam", "VN"), ("Yemen", "YE"), ("Zambia", "ZM"),
    ("Zimbabwe", "ZW"),
]
COUNTRY_CODE_BY_NAME = dict(COUNTRIES)


def country_flag_emoji(country_name):
    """Converts a country name to its flag emoji via ISO-2 regional
    indicator symbols. Returns '' if the name isn't recognized."""
    code = COUNTRY_CODE_BY_NAME.get((country_name or "").strip())
    if not code:
        return ""
    return "".join(chr(0x1F1E6 + ord(ch) - ord("A")) for ch in code.upper())
