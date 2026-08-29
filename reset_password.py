"""
Lotwise — Reset the admin password directly

Use this if you're locked out and can't remember your password.
From the backend folder, with your virtual environment activated:

    python reset_password.py
"""
from app import app, db
from models import AdminUser

with app.app_context():
    email = input("Admin email: ").strip().lower()
    user = AdminUser.query.filter_by(email=email).first()

    if not user:
        print(f"No admin account found with email: {email}")
        print("Run create_admin.py first.")
    else:
        new_password = input("New password (won't show on screen): ").strip()
        if len(new_password) < 8:
            print("Password must be at least 8 characters. Nothing was changed — run this again.")
        else:
            user.set_password(new_password)
            db.session.commit()
            print(f"Password reset for {user.email}.")
            print("Go to /admin/login on the site and use the new password.")
