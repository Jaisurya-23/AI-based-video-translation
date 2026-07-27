import mysql.connector
import hashlib
import os

# ── Config — edit these or set as environment variables ──────────────────────
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_USER = os.environ.get("DB_USER", "root")
DB_PASS = os.environ.get("DB_PASS", "")
DB_NAME = os.environ.get("DB_NAME", "tamilvoice")

ADMIN_USERNAME = "admin"
ADMIN_EMAIL    = "admin@tamilvoice.local"
ADMIN_PASSWORD = "admin123"   # ← change this after first login

# ─────────────────────────────────────────────────────────────────────────────

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def run():
    print(f"Connecting to MySQL at {DB_HOST} as '{DB_USER}'...")

    # Connect without selecting a DB first
    conn = mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        charset="utf8",
        use_unicode=True,
    )
    cur = conn.cursor()

    # 1. Create database
    print(f"Creating database '{DB_NAME}' if it doesn't exist...")
    cur.execute(
        f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
        f"CHARACTER SET utf8 COLLATE utf8_general_ci"
    )
    cur.execute(f"USE `{DB_NAME}`")
    print(f"  ✓ Database '{DB_NAME}' ready.")

    # 2. users table
    print("Creating table: users...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            username      VARCHAR(80)  NOT NULL UNIQUE,
            email         VARCHAR(120) NOT NULL UNIQUE,
            password_hash VARCHAR(200) NOT NULL,
            role          ENUM('user','admin') DEFAULT 'user',
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active     TINYINT(1)   DEFAULT 1
        ) CHARACTER SET utf8
    """)
    print("  ✓ Table 'users' ready.")

    # 3. video_history table
    print("Creating table: video_history...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS video_history (
            id                INT AUTO_INCREMENT PRIMARY KEY,
            user_id           INT          NOT NULL,
            job_id            VARCHAR(50)  NOT NULL,
            original_filename VARCHAR(255),
            output_filename   VARCHAR(255),
            tamil_srt         VARCHAR(255),
            eng_srt           VARCHAR(255),
            hindi_srt         VARCHAR(255),
            segment_count     INT          DEFAULT 0,
            status            VARCHAR(20)  DEFAULT 'processing',
            created_at        TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        ) CHARACTER SET utf8
    """)
    print("  ✓ Table 'video_history' ready.")

    # 4. Default admin account
    cur.execute("SELECT id FROM users WHERE username = %s", (ADMIN_USERNAME,))
    if cur.fetchone():
        print(f"  ℹ  Admin user '{ADMIN_USERNAME}' already exists — skipped.")
    else:
        cur.execute(
            "INSERT INTO users (username, email, password_hash, role) VALUES (%s, %s, %s, 'admin')",
            (ADMIN_USERNAME, ADMIN_EMAIL, hash_pw(ADMIN_PASSWORD))
        )
        conn.commit()
        print(f"  ✓ Admin account created.")
        print(f"      Username : {ADMIN_USERNAME}")
        print(f"      Password : {ADMIN_PASSWORD}  ← please change this after first login")

    conn.commit()
    cur.close()
    conn.close()

    print("\n✅ Database setup complete. You can now run: python app.py")

if __name__ == "__main__":
    run()
