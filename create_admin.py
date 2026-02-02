"""Script to make a user an admin"""

from wazz_shared.database import SessionLocal
from wazz_shared.models import User
import sys

def make_admin(email_or_username: str):
    """Make a user an admin by email or username"""
    db = SessionLocal()

    try:
        # Try to find user by email first
        user = db.query(User).filter(User.email == email_or_username).first()

        # If not found, try username
        if not user:
            user = db.query(User).filter(User.username == email_or_username).first()

        if not user:
            print(f"❌ Error: User '{email_or_username}' not found")
            print("   Make sure you've registered an account first")
            return False

        # Check if already admin
        if user.is_admin:
            print(f"ℹ️  User '{user.username}' is already an admin")
            return True

        # Make admin
        user.is_admin = True
        db.commit()

        print(f"✅ Success! User '{user.username}' ({user.email}) is now an admin")
        print(f"   User ID: {user.id}")
        print(f"   Created: {user.created_at}")
        return True

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        db.rollback()
        return False
    finally:
        db.close()


def list_admins():
    """List all admin users"""
    db = SessionLocal()

    try:
        admins = db.query(User).filter(User.is_admin == True).all()

        if not admins:
            print("No admin users found")
            return

        print(f"\n📋 Admin Users ({len(admins)} total):")
        print("-" * 80)
        for admin in admins:
            status = "✓ Active" if admin.is_active else "✗ Inactive"
            verified = "✓ Verified" if admin.is_verified else "✗ Not Verified"
            print(f"ID: {admin.id:3d} | {admin.username:20s} | {admin.email:30s}")
            print(f"        Status: {status:12s} | Email: {verified}")
            print("-" * 80)

    finally:
        db.close()


def main():
    print("\n" + "=" * 80)
    print("                    Whazz Audio - Admin User Manager")
    print("=" * 80 + "\n")

    if len(sys.argv) < 2:
        print("Usage:")
        print("  Make user admin:  python3 create_admin.py <email_or_username>")
        print("  List admins:      python3 create_admin.py --list")
        print("\nExamples:")
        print("  python3 create_admin.py john@example.com")
        print("  python3 create_admin.py johndoe")
        print("  python3 create_admin.py --list")
        sys.exit(1)

    if sys.argv[1] == "--list":
        list_admins()
    else:
        email_or_username = sys.argv[1]
        make_admin(email_or_username)

    print()


if __name__ == "__main__":
    main()
