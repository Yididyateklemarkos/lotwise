"""
Lotwise — Reset a password directly

Use this if you're locked out and can't remember your password.
From the backend folder, with your virtual environment activated:

    python reset_password.py

It asks for the account's email and a new password, then sets it.
"""
from app import app, db
from models import User

with app.app_context():
    email = input("Email of the account: ").strip().lower()
    user = User.query.filter_by(email=email).first()

    if not user:
        print(f"No account found with email: {email}")
        print("Check the spelling — this must match exactly what you signed up with.")
    else:
        new_password = input("New password (type carefully, it won't show on screen): ").strip()
        if len(new_password) < 8:
            print("Password must be at least 8 characters. Nothing was changed — run this again.")
        else:
            user.set_password(new_password)
            db.session.commit()
            print(f"Password reset for {user.email}.")
            print("Go to /login on the site and use the new password.")
