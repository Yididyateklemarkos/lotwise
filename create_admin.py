"""
Lotwise — Create Admin Account (standalone script)

Run this instead of the Flask CLI command if that one gave you trouble.
From the backend folder, with your virtual environment activated, run:

    python create_admin.py

It will ask for an email and password, then create (or promote) that
account to an approved admin account.
"""
import getpass
from app import app, db
from models import User

with app.app_context():
    email = input("Admin email: ").strip().lower()
    password = getpass.getpass("Admin password: ").strip()

    if not email or not password:
        print("Email and password are both required. Try again.")
        raise SystemExit(1)

    existing = User.query.filter_by(email=email).first()
    if existing:
        existing.is_admin = True
        existing.verification_status = "approved"
        if not existing.password_hash:
            existing.set_password(password)
        db.session.commit()
        print(f"\nExisting account '{email}' has been promoted to admin.")
    else:
        admin = User(
            email=email,
            company_name="Lotwise Admin",
            account_type="supplier",
            is_admin=True,
            verification_status="approved",
        )
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        print(f"\nAdmin account created: {email}")

    print("Log in at http://localhost:5000/login, then visit /admin")
