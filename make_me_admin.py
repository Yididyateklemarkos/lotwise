"""
Lotwise — Promote an existing account to admin

Run this if you already have an account (from signup or create_admin.py)
and just need it flagged as admin. From the backend folder, with your
virtual environment activated:

    python make_me_admin.py

It will ask for the email of the account you want to promote.
"""
from app import app, db
from models import User

with app.app_context():
    email = input("Email of the account to make admin: ").strip().lower()
    user = User.query.filter_by(email=email).first()

    if not user:
        print(f"No account found with email: {email}")
        print("Double check the spelling, or sign up with that email first.")
    else:
        user.is_admin = True
        user.verification_status = "approved"
        db.session.commit()
        print(f"Done. {user.email} is now an admin and can visit /admin.")
        print("Log out and log back in if you were already logged in, so the change takes effect.")
