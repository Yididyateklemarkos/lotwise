"""
Lotwise — Create Admin Account (standalone script)

From the backend folder, with your virtual environment activated, run:

    python create_admin.py

It will ask for an email and password, then create (or update) that
admin login. This is the ONLY account type on the site — visitors never
sign up; they just submit the public request forms.
"""
import getpass
from app import app, db
from models import AdminUser

with app.app_context():
    email = input("Admin email: ").strip().lower()
    password = getpass.getpass("Admin password: ").strip()

    if not email or not password:
        print("Email and password are both required. Try again.")
        raise SystemExit(1)

    existing = AdminUser.query.filter_by(email=email).first()
    if existing:
        existing.set_password(password)
        db.session.commit()
        print(f"\nExisting admin '{email}' password updated.")
    else:
        admin = AdminUser(email=email)
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        print(f"\nAdmin account created: {email}")

    print("Log in at http://localhost:5000/admin/login")
