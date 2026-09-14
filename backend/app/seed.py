"""
CLI helper to create/reset an admin user.

Usage:
    python -m app.seed --username admin --password 'S3cret!'
"""
import argparse

from .database import Base, SessionLocal, engine
from .models import User
from .security import hash_password


def main():
    parser = argparse.ArgumentParser(description="Create or update a VMware Capacity Ops login user")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--display-name", default=None)
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == args.username).first()
        if user:
            user.password_hash = hash_password(args.password)
            if args.display_name:
                user.display_name = args.display_name
            print(f"Updated password for existing user '{args.username}'.")
        else:
            user = User(
                username=args.username,
                password_hash=hash_password(args.password),
                display_name=args.display_name or args.username,
            )
            db.add(user)
            print(f"Created new user '{args.username}'.")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
