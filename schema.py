import psycopg2
import os
import uuid
from datetime import datetime, timedelta

def get_db_connection(db_name="sardargiantscloud"):
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return psycopg2.connect(database_url)
    return psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        database=os.environ.get("PGDATABASE", db_name),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD"),
        port=os.environ.get("PGPORT", "5432"),
    )

def create_database():
    if os.environ.get("DATABASE_URL"):
        return
    try:
        conn = get_db_connection(db_name="postgres")
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_catalog.pg_database WHERE datname = 'sardargiantscloud'")
        exists = cur.fetchone()
        if not exists:
            cur.execute("CREATE DATABASE sardargiantscloud")
            print("Database 'sardargiantscloud' created.")
        else:
            print("Database 'sardargiantscloud' already exists.")
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error creating database: {e}")

def init_db():
    create_database()
    conn = get_db_connection()
    cur = conn.cursor()
    
    # User Table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id VARCHAR(36) PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            email VARCHAR(120) UNIQUE NOT NULL,
            password VARCHAR(200) NOT NULL,
            role VARCHAR(20) DEFAULT 'client',
            created_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            allocated_storage INTEGER DEFAULT 10,
            next_payment TIMESTAMP NOT NULL,
            last_login TIMESTAMP
        );
    """)

    # Migration: add last_login column if it doesn't exist (for existing DBs)
    try:
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login TIMESTAMP")
    except Exception:
        pass
    
    # Billing Requests
    cur.execute("""
        CREATE TABLE IF NOT EXISTS billing_requests (
            request_id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) REFERENCES users(id),
            submitted_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            phone_number VARCHAR(20),
            request_type VARCHAR(50),
            storage_needed VARCHAR(50),
            message TEXT,
            status VARCHAR(20) DEFAULT 'Pending',
            payment_proof VARCHAR(255)
        );
    """)

    # Migration: add payment_proof column if it doesn't exist (for existing DBs)
    try:
        cur.execute("ALTER TABLE billing_requests ADD COLUMN IF NOT EXISTS payment_proof VARCHAR(255)")
    except Exception:
        pass
    
    # Support Requests
    cur.execute("""
        CREATE TABLE IF NOT EXISTS support_requests (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(36) REFERENCES users(id),
            message TEXT NOT NULL,
            submitted_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status VARCHAR(20) DEFAULT 'Open'
        );
    """)
    
    # Notifications
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(36) REFERENCES users(id),
            sender VARCHAR(100),
            message TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            read BOOLEAN DEFAULT FALSE
        );
    """)
    
    conn.commit()
    cur.close()
    conn.close()
    print("Database Initialized!")

def add_user(name, email, password, role):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        user_id = str(uuid.uuid4())
        # Default next_payment to 30 days from now
        next_payment = datetime.now() + timedelta(days=30)
        
        cur.execute(
            "INSERT INTO users (id, name, email, password, role, next_payment) VALUES (%s, %s, %s, %s, %s, %s)",
            (user_id, name, email, password, role, next_payment)
        )
        conn.commit()
        print(f"User '{name}' ({email}) added as {role}.")
    except psycopg2.IntegrityError:
        conn.rollback()
        print(f"Error: User with email '{email}' already exists.")
    except Exception as e:
        conn.rollback()
        print(f"Error adding user: {e}")
    finally:
        cur.close()
        conn.close()

def add_admin(name, email, password):
    add_user(name, email, password, 'admin')

def add_root(name, email, password):
    add_user(name, email, password, 'root')

def add_client(name, email, password):
    add_user(name, email, password, 'client')

if __name__ == "__main__":
    init_db()
    
    # Example usage (commented out to avoid auto-adding on import if logic changes)
    # add_admin("Admin One", "admin@sgmail.com", "7194")
    # add_root("Root One", "root@sgmail.com", "7194")
    add_client("Client One", "client@sgmail.com", "7194")
