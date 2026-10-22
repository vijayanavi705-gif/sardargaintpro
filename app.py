import os, shutil, uuid, functools
from datetime import timedelta, datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory
from werkzeug.utils import secure_filename
import psycopg2
from psycopg2 import pool
from flask_socketio import SocketIO, emit, join_room
import schema

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)

# Database Configuration
def get_db_connection():
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return psycopg2.connect(database_url)
    return psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        database=os.environ.get("PGDATABASE", "sardargiantscloud"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD"),
        port=os.environ.get("PGPORT", "5432"),
    )

def release_db_connection(conn):
    conn.close()

# Initialize SocketIO
socketio = SocketIO(app)

# --- Initialize DB ---
# Run the schema init on startup
schema.init_db()

# --- File Paths ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
storage_path = os.environ.get("STORAGE_PATH", os.path.join(BASE_DIR, "static", "storage"))

# ------------------- Helper Functions ------------------- #
    
def get_used_storage_size(id):
    """Calculates the total size of files in a user's directory in bytes."""
    user_folder = os.path.join(storage_path, id)
    if not os.path.exists(user_folder):
        return 0
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(user_folder):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            total_size += os.path.getsize(fp)
    return total_size

def login_required(roles=None):
    """Decorator to protect routes and check user roles."""
    def decorator(f):
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            if 'id' not in session:
                flash('Please log in to access this page.', 'error')
                return redirect(url_for('login'))
            
            if roles and session['role'] not in roles:
                flash('You do not have permission to access this page. You have been signed out.', 'error')
                return redirect(url_for('logout', id=session['id']))
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ------------------- Main Routes ------------------- #
@app.route("/")
def home():
    """Redirects the root URL to the login page."""
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    """Handles user login."""
    if request.method == "POST":
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        
        if not email or not password:
            flash('Please enter both email and password.', 'error')
            return render_template("login_page.html")
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, name, email, role, password, next_payment FROM users WHERE email = %s", (email,))
        user_data = cur.fetchone()
        cur.close()
        release_db_connection(conn)
        
        if user_data:
            # user_data = (id, name, email, role, password, next_payment)
            if user_data[4] == password:
                session['id'] = user_data[0]
                session['name'] = user_data[1]
                session['sgmail'] = user_data[2]
                session['role'] = user_data[3]
                session['next_payment'] = str(user_data[5])

                # Update last_login timestamp
                conn2 = get_db_connection()
                cur2 = conn2.cursor()
                try:
                    cur2.execute("UPDATE users SET last_login = %s WHERE id = %s", (datetime.now(), user_data[0]))
                    conn2.commit()
                except Exception:
                    conn2.rollback()
                finally:
                    cur2.close()
                    release_db_connection(conn2)

                if session['role'] == 'root':
                    flash('Login successful.', 'success')
                    return redirect(url_for('root_dashboard', id=session['id']))
                elif session['role'] == 'admin':
                    flash('Login successful.', 'success')
                    return redirect(url_for('admin_dashboard', id=session['id']))
                else:
                    flash('Login successful.', 'success')
                    return redirect(url_for('client_dashboard', id=session['id']))
            else:
                 flash('Invalid email or password.', 'error')
        else:
            flash('Invalid email or password.', 'error')
            return render_template("login_page.html")

    return render_template("login_page.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    """Handles user signup and new folder creation."""
    if request.method == "POST":
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        
        if not all([name, email, password]):
            flash('All fields are required.', 'error')
            return render_template("client_signup_page.html")
        
        if not email.endswith("@sgmail.com"):
            flash('Email must end with @sgmail.com', 'error')
            return render_template("client_signup_page.html")
        
        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'error')
            return render_template("client_signup_page.html")
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            cur.close()
            release_db_connection(conn)
            flash('An account with this email already exists.', 'error')
            return render_template("client_signup_page.html")
        
        new_id = str(uuid.uuid4())
        # Default 30 days access
        next_pay = datetime.now() + timedelta(days=30)
        
        try:
            cur.execute("""
                INSERT INTO users (id, name, email, password, role, allocated_storage, next_payment)
                VALUES (%s, %s, %s, %s, 'client', 10, %s)
            """, (new_id, name, email, password, next_pay))
            conn.commit()
            
            os.makedirs(os.path.join(storage_path, new_id), exist_ok=True)
            
            flash('Account created successfully! Please log in.', 'success')
            cur.close()
            release_db_connection(conn)
            return redirect(url_for('login'))
        except Exception as e:
            conn.rollback()
            cur.close()
            release_db_connection(conn)
            flash(f'Error creating account: {e}', 'error')
            
    return render_template("client_signup_page.html")

@app.route("/logout")
def logout():
    """Logs the user out."""
    session.clear()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('login'))

@app.route("/storage/<id>", methods=["GET", "POST"])
@login_required(roles=['client', 'admin', 'root'])
def storage(id):
    """Unified file management page for clients."""
    
    if request.method == "GET":
        
        if not session['id'] or session['id'] != id:
            flash("User not found.", "error")
            return redirect(url_for('logout'))

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT allocated_storage, next_payment FROM users WHERE id = %s", (id,))
        user_row = cur.fetchone()
        cur.close()
        release_db_connection(conn)
        
        if not user_row:
             flash("User not found.", "error")
             return redirect(url_for('logout'))
        
        allocated_storage = user_row[0]
        next_payment = user_row[1]
        
        session['allocated_storage'] = allocated_storage
        session['next_payment'] = str(next_payment)
                
        if session['role']=='client' and datetime.now() > next_payment:
            flash('Your account is closed due to insufficient balance. Directed to billing page.', 'error')
            return redirect(url_for('client_billing', id=session['id']))
            
        user_folder = os.path.join(storage_path, id)
        os.makedirs(user_folder, exist_ok=True)
        
        files = []
        try:
            for file in os.listdir(user_folder):
                path = os.path.join(user_folder, file)
                if os.path.isfile(path):
                    files.append({
                        "name": file,
                        "size": os.path.getsize(path),
                        "modified": datetime.fromtimestamp(os.path.getmtime(path)).strftime('%Y-%m-%d %H:%M')
                    })
        except Exception as e:
            flash('Error reading your files.', 'error')
        
        return render_template("storage_page.html", files=files, storage_used = get_used_storage_size(session['id']) / (1024 * 1024))

    if request.method == "POST":
        action = request.form.get("action")
        
        if action == "upload":
            uploaded_files = request.files.getlist("files[]")

            if not uploaded_files:
                flash("No files selected for upload.", "error")
                return redirect(url_for('storage', id=id))
            
            for file in uploaded_files:
                if file.filename:
                    original_filename = os.path.basename(file.filename)
                    name, ext = os.path.splitext(original_filename)
                    safe_name = original_filename
                    file_path = os.path.join(storage_path, id, safe_name)

                    counter = 1
                    while os.path.exists(file_path):
                        safe_name = f"{name} ({counter}){ext}"
                        file_path = os.path.join(storage_path, id, safe_name)
                        counter += 1

                    file.seek(0, os.SEEK_END)
                    file_size = file.tell()
                    file.seek(0)

                    if file_size > 50 * 1024 * 1024:
                        flash(f"File {safe_name} exceeds the 50MB limit.", "error")  
                        continue

                    if get_used_storage_size(id) + file_size > float(session['allocated_storage']) * 1024 * 1024:
                        flash(f"Storage limit exceeded. Cannot upload file '{safe_name}'.", "error")
                        continue

                    file.save(file_path)
                    flash(f"Successfully uploaded file '{safe_name}'.", "success")

        elif action == "delete":
            filename_to_delete = request.form.get("filename")
            file_path = os.path.join(storage_path, id, filename_to_delete)
            if os.path.exists(file_path):
                os.remove(file_path)
                flash(f"File '{filename_to_delete}' deleted successfully.", "success")
            else:
                flash("File not found.", "error")
        
        return redirect(url_for('storage', id=id))

@app.route("/notification/<id>", methods=["GET", "POST"])
@login_required(roles=['client', 'admin', 'root'])
def notification(id):
    """Notification center for clients."""
    if not session['id'] or session['id'] != id:
        flash("User not found.", "error")
        return redirect(url_for('logout'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT role, next_payment FROM users WHERE id = %s", (id,))
    user_row = cur.fetchone()
    
    if not user_row:
        cur.close()
        release_db_connection(conn)
        flash("User not found.", "error")
        return redirect(url_for('logout'))
    
    session['role'] = user_row[0]
    session['next_payment'] = str(user_row[1])
    
    if session['role']=='client' and datetime.now() > user_row[1]:
            cur.close()
            release_db_connection(conn)
            flash('Your account is closed due to insufficient balance. Directed to billing page.', 'error')
            return redirect(url_for('client_billing', id=session['id']))
    
    # Handle POST deletion
    if request.method == "POST":
        notif_id = request.form.get("notif_id")
        if notif_id:
            try:
                cur.execute("DELETE FROM notifications WHERE id = %s AND user_id = %s", (notif_id, id))
                conn.commit()
                flash('Notification deleted successfully.', 'success')
            except Exception as e:
                conn.rollback()
                flash(f'Error deleting notification: {e}', 'error')
        cur.close()
        release_db_connection(conn)
        return redirect(url_for('notification', id=id))
    
    # Handle GET view
    cur.execute("SELECT id, message, sender, timestamp FROM notifications WHERE user_id = %s ORDER BY timestamp DESC", (id,))
    notif_rows = cur.fetchall()
    cur.close()
    release_db_connection(conn)
    
    notifications = []
    for row in notif_rows:
        notifications.append({
            "id": row[0],
            "message": row[1],
            "sender": row[2],
            "timestamp": row[3] # Jinja can format this or we format here
        })
    
    return render_template("notification.html", notifications=notifications)

# --- Dashboard Routes ---
@app.route("/client_dashboard/<id>")
@login_required(roles=['client', 'admin', 'root'])
def client_dashboard(id):
    """Client dashboard."""
    if not session['id'] or session['id'] != id:
        flash("User not found.", "error")
        return redirect(url_for('logout'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT allocated_storage, next_payment FROM users WHERE id = %s", (id,))
    user_row = cur.fetchone()
    cur.close()
    release_db_connection(conn)

    if not user_row:
         flash("User not found.", "error")
         return redirect(url_for('logout'))

    session['allocated_storage'] = user_row[0]
    session['next_payment'] = str(user_row[1])
            
    if datetime.now() > user_row[1]:
        flash('Your account is closed due to insufficient balance. Directed to billing page.', 'error')
        return redirect(url_for('client_billing', id=session['id']))

    return render_template("client_dashboard_page.html", storage_used = get_used_storage_size(session['id']) / (1024 * 1024))

@app.route("/client_billing/<id>")
@login_required(roles=['client', 'admin', 'root'])
def client_billing(id):
    if not session['id'] or session['id'] != id:
        flash("User not found.", "error")
        return redirect(url_for('logout'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT allocated_storage, next_payment FROM users WHERE id = %s", (id,))
    user_row = cur.fetchone()
    cur.close()
    release_db_connection(conn)
    
    if not user_row:
        flash("User not found.", "error")
        return redirect(url_for('logout'))

    session['allocated_storage'] = user_row[0]
    session['next_payment'] = str(user_row[1])
                
    return render_template("client_billing_page.html", storage_used = get_used_storage_size(session['id']) / (1024 * 1024))

@app.route("/submit_storage_request/<id>", methods=["POST"])
@login_required(roles=['client', 'admin', 'root'])
def submit_storage_request(id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        req_id = str(uuid.uuid4())
        
        # Handle payment proof upload
        payment_proof_filename = None
        payment_file = request.files.get('payment_proof')
        if payment_file and payment_file.filename:
            ext = os.path.splitext(secure_filename(payment_file.filename))[1]
            payment_proof_filename = f"{req_id}{ext}"
            proof_dir = os.path.join(BASE_DIR, 'static', 'payment_proofs')
            os.makedirs(proof_dir, exist_ok=True)
            payment_file.save(os.path.join(proof_dir, payment_proof_filename))
        
        cur.execute("""
            INSERT INTO billing_requests (request_id, user_id, phone_number, request_type, storage_needed, message, payment_proof)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (req_id, session["id"], request.form.get("phone_number"), request.form.get("request_type"), request.form.get("storage_needed"), request.form.get("message"), payment_proof_filename))
        conn.commit()
        flash("Request submitted successfully!", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error submitting request: {e}", "error")
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('client_billing', id=id))

@app.route("/client_support/<id>")
@login_required(roles=['client', 'admin', 'root'])
def client_support(id):
    if not session['id'] or session['id'] != id:
        flash("User not found.", "error")
        return redirect(url_for('logout'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT next_payment FROM users WHERE id = %s", (id,))
    user_row = cur.fetchone()
    cur.close()
    release_db_connection(conn)
    
    if not user_row:
        flash("User not found.", "error")
        return redirect(url_for('logout'))
    
    if datetime.now() > user_row[0]:
        flash('Your account is closed due to insufficient balance. Directed to billing page.', 'error')
        return redirect(url_for('client_billing', id = session['id']))
        
    return render_template("client_support_page.html")

@app.route("/submit_support_request/<id>", methods=["POST"])
@login_required(roles=['client', 'admin', 'root'])
def submit_support_request(id):
    if not session['id'] or session['id'] != id:
        flash("User not found.", "error")
        return redirect(url_for('logout'))

    message = request.form.get("feedback", "").strip()
    
    if not message:
        flash('Please enter a message.', 'error')
    else:
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO support_requests (user_id, message) VALUES (%s, %s)", (id, message))
            conn.commit()
            flash('Support ticket submitted successfully!', 'success')
        except Exception as e:
            conn.rollback()
            flash(f'Error submitting ticket: {e}', 'error')
        finally:
            cur.close()
            release_db_connection(conn)

    return redirect(url_for('client_support', id = session['id']))

@app.route("/download/<id>/<filename>", methods=["GET"])
@login_required(roles=['client', 'admin', 'root'])
def download(id, filename):
    if not session['id'] or session['id'] != id:
        flash("User not found.", "error")
        return redirect(url_for('logout'))
    
    try:
        return send_from_directory(os.path.join(storage_path, id), filename, as_attachment=True)
    except FileNotFoundError:
        flash("File not found.", "error")
        return redirect(url_for('storage', id=id))

@app.route("/admin_dashboard/<id>")
@login_required(roles=['admin', 'root'])
def admin_dashboard(id):
    if not session['id'] or session['id'] != id:
        flash("User not found.", "error")
        return redirect(url_for('logout'))

    conn = get_db_connection()
    cur = conn.cursor()

    # 1. Total Clients
    cur.execute("SELECT COUNT(*) FROM users WHERE role = 'client'")
    total_clients = cur.fetchone()[0]

    # 2. Pending Billing Requests
    cur.execute("SELECT COUNT(*) FROM billing_requests WHERE status = 'Pending'")
    pending_billing = cur.fetchone()[0]

    # 3. Open Support Tickets
    cur.execute("SELECT COUNT(*) FROM support_requests WHERE status = 'Open'")
    pending_support = cur.fetchone()[0]

    # 4. Total Storage Used (Iterate through all user folders in storage path)
    total_storage_used = 0
    try:
        # Assuming storage_path is defined globally as "static/storage"
        if os.path.exists(storage_path):
             for dirpath, dirnames, filenames in os.walk(storage_path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    total_storage_used += os.path.getsize(fp)
        total_storage_used = round(total_storage_used / (1024 * 1024), 2) # MB
    except Exception as e:
        print(f"Error calculating storage: {e}")
        total_storage_used = 0
    
    cur.close()
    release_db_connection(conn)

    return render_template("admin_dashboard.html", 
                           total_clients=total_clients,
                           pending_billing=pending_billing,
                           pending_support=pending_support,
                           total_storage_used=total_storage_used)

@app.route("/client_management/<id>")
@login_required(roles=['admin', 'root'])
def client_management(id):
    if not session['id'] or session['id'] != id:
        flash("User not found.", "error")
        return redirect(url_for('logout'))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name, email, created_on, allocated_storage, next_payment, last_login FROM users WHERE role = 'client'")
    client_rows = cur.fetchall()
    cur.close()
    release_db_connection(conn)
    
    clients = []
    for c in client_rows:
        clients.append({
            "id": c[0],
            "name": c[1],
            "sgmail": c[2],
            "created_on": c[3].strftime('%Y-%m-%d %H:%M:%S') if c[3] else '',
            "allocated_storage": c[4],
            "next_payment": c[5].strftime('%Y-%m-%d %H:%M:%S') if c[5] else '',
            "last_login": c[6].strftime('%Y-%m-%d %H:%M:%S') if c[6] else None,
            "last_login_iso": c[6].isoformat() if c[6] else None
        })

    return render_template("client_management.html", clients=clients)

@app.route("/admin_support/<id>")
@login_required(roles=['admin', 'root'])
def admin_support(id):
    if not session['id'] or session['id'] != id:
        flash("User not found.", "error")
        return redirect(url_for('logout'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    # Fetch support requests showing latest first
    cur.execute("""
        SELECT s.id, s.user_id, u.name, u.email, s.message, s.submitted_on, s.status 
        FROM support_requests s
        JOIN users u ON s.user_id = u.id
        ORDER BY s.submitted_on DESC
    """)
    rows = cur.fetchall()
    cur.close()
    release_db_connection(conn)
    
    tickets = []
    for r in rows:
        tickets.append({
            "id": r[0],
            "user_id": r[1],
            "name": r[2],
            "email": r[3],
            "message": r[4],
            "submitted_on": r[5].strftime('%Y-%m-%d %H:%M') if r[5] else '',
            "status": r[6]
        })

    return render_template("admin_support_page.html", tickets=tickets)

@app.route("/submit_support_responce/<id>", methods=["POST"])
@login_required(roles=['admin', 'root'])
def submit_support_responce(id):
    target_client_id = request.form['target_client_id']
    message = request.form['message']

    if not message:
        flash('Please enter a message.', 'error')
        return redirect(url_for('client_management', id=id))
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO notifications (user_id, sender, message) VALUES (%s, 'Admin', %s)", (target_client_id, message))
        conn.commit()
        
        socketio.emit('new_notification', {
            'message': message,
            'sender': 'Admin',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M')
        }, room=target_client_id)
        
        flash(f"Message sent successfully.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error sending message: {e}", "error")
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('client_management', id=id))

@app.route("/delete_client/<id>", methods=["POST"])
@login_required(roles=['admin', 'root'])
def delete_client(id):
    target_client_id = request.form['target_client_id']
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Cascade delete is cleaner but doing manual for safety/clarity in this refactor
        cur.execute("DELETE FROM billing_requests WHERE user_id = %s", (target_client_id,))
        cur.execute("DELETE FROM support_requests WHERE user_id = %s", (target_client_id,))
        cur.execute("DELETE FROM notifications WHERE user_id = %s", (target_client_id,))
        cur.execute("DELETE FROM users WHERE id = %s", (target_client_id,))
        conn.commit()
        
        client_folder = os.path.join(storage_path, target_client_id)
        if os.path.exists(client_folder):
            shutil.rmtree(client_folder)
            
        flash(f"Client deleted successfully.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error deleting client: {e}", "error")
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for('client_management', id=id))


@app.route("/billing_management/<id>")
@login_required(roles=['admin', 'root'])
def billing_management(id):
    if not session['id'] or session['id'] != id:
        flash("User not found.", "error")
        return redirect(url_for('logout'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    # Join to get user info
    cur.execute("""
        SELECT b.request_id, b.user_id, u.name, u.email, b.submitted_on, b.phone_number, b.request_type, b.storage_needed, b.message, b.payment_proof 
        FROM billing_requests b
        JOIN users u ON b.user_id = u.id
        WHERE b.status = 'Pending'
    """)
    rows = cur.fetchall()
    cur.close()
    release_db_connection(conn)
    
    data = []
    for r in rows:
        data.append({
            "id": r[0],
            "user_id": r[1],
            "name": r[2],
            "sgmail": r[3],
            "submitted_on": r[4].strftime('%Y-%m-%d %H:%M'),
            "phone_number": r[5],
            "request": r[6],
            "storage": r[7],
            "message": r[8],
            "payment_proof": r[9]
        })

    return render_template("billing_management.html", billing_requests=data)

@app.route("/approve_request/<request_id>", methods=["POST"])
@login_required(roles=['admin', 'root'])
def approve_request(request_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT user_id, storage_needed, request_type FROM billing_requests WHERE request_id = %s", (request_id,))
        req = cur.fetchone()
        
        if req:
            user_id = req[0]
            storage_delta = int(req[1] if req[1] is not None else '0')
            request_type = req[2] if req[2] else 'same'
            
            cur.execute("SELECT allocated_storage FROM users WHERE id = %s", (user_id,))
            current_storage = cur.fetchone()[0]
            
            # Calculate new storage based on request type
            if request_type == 'extend':
                new_storage = current_storage + storage_delta
            elif request_type == 'shrink':
                new_storage = max(1, current_storage - storage_delta)  # Minimum 1 MB
            else:  # 'same' — no storage change, just renew
                new_storage = current_storage
            
            # Always extend payment date by 30 days from now
            new_payment_date = datetime.now() + timedelta(days=30)
            
            cur.execute("UPDATE users SET allocated_storage = %s, next_payment = %s WHERE id = %s", (new_storage, new_payment_date, user_id))
            cur.execute("UPDATE billing_requests SET status = 'Approved' WHERE request_id = %s", (request_id,))
            
            # Send notification to the client
            notif_msg = f"Your billing request has been approved. Storage: {new_storage}MB. Next payment: {new_payment_date.strftime('%Y-%m-%d')}."
            cur.execute("INSERT INTO notifications (user_id, sender, message) VALUES (%s, 'Admin', %s)", (user_id, notif_msg))
            
            conn.commit()
            
            socketio.emit('billing_update', {
                'message': notif_msg,
                'allocated_storage': new_storage
            }, room=user_id)
            
            socketio.emit('new_notification', {
                'message': notif_msg,
                'sender': 'Admin',
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M')
            }, room=user_id)

            flash(f"Approved request. Storage: {new_storage}MB. Payment extended to {new_payment_date.strftime('%Y-%m-%d')}.", "success")
        else:
            flash("Request not found.", "error")
            
    except Exception as e:
        conn.rollback()
        flash(f"Error approving request: {e}", "error")
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for("billing_management", id=session["id"]))


@app.route("/deny_request/<request_id>", methods=["POST"])
@login_required(roles=['admin', 'root'])
def deny_request(request_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT user_id FROM billing_requests WHERE request_id = %s", (request_id,))
        req = cur.fetchone()
        
        if req:
            user_id = req[0]
            cur.execute("UPDATE billing_requests SET status = 'Denied' WHERE request_id = %s", (request_id,))
            
            # Send notification to the client
            notif_msg = "Your billing/storage request has been denied. Please contact support for more information."
            cur.execute("INSERT INTO notifications (user_id, sender, message) VALUES (%s, 'Admin', %s)", (user_id, notif_msg))
            
            conn.commit()
            
            socketio.emit('billing_update', {
                'message': notif_msg
            }, room=user_id)
            
            socketio.emit('new_notification', {
                'message': notif_msg,
                'sender': 'Admin',
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M')
            }, room=user_id)
            
            flash("Request denied.", "info")
        else:
             flash("Request not found.", "error")
    except Exception as e:
        conn.rollback()
        flash(f"Error denying request: {e}", "error")
    finally:
        cur.close()
        release_db_connection(conn)

    return redirect(url_for("billing_management", id=session["id"]))

@app.route("/root_dashboard/<id>")
@login_required(roles=['root'])
def root_dashboard(id):
    """Root user dashboard to manage system and users."""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Fetch Users
    cur.execute("SELECT * FROM users")
    users = cur.fetchall()
    
    # Fetch Support Requests
    cur.execute("SELECT * FROM support_requests")
    supports = cur.fetchall()
    
    # Fetch Stats
    cur.execute("SELECT COUNT(*) FROM users")
    total_users_count = cur.fetchone()[0]
    
    # Calculate Total Storage from Filesystem (since we don't have a files table)
    total_storage_used = 0
    try:
        if os.path.exists(storage_path):
            for dirpath, dirnames, filenames in os.walk(storage_path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    total_storage_used += os.path.getsize(fp)
        total_storage_used = round(total_storage_used / (1024 * 1024), 2) # Convert to MB
    except Exception as e:
        print(f"Error calculating storage: {e}")
        total_storage_used = 0

    # Mock online users for now (or use a heartbeat table in future)
    online_count = 1 
    
    cur.close()
    release_db_connection(conn)
    
    return render_template("root_dashboard_page.html", 
                           users=users, 
                           supports=supports,
                           total_users_count=total_users_count,
                           total_storage_used=total_storage_used,
                           online_count=online_count)

@app.route("/iam_dashboard/<id>")
@login_required(roles=['root'])
def iam_dashboard(id):
    if not session['id'] or session['id'] != id:
        flash("User not found.", "error")
        return redirect(url_for('logout'))
        
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT id, name, email, created_on, allocated_storage, last_login FROM users WHERE role = 'admin'")
    admin_rows = cur.fetchall()
    
    cur.close()
    release_db_connection(conn)
    
    admins = []
    for row in admin_rows:
        admins.append({
            "id": row[0],
            "name": row[1],
            "email": row[2],
            "created_on": row[3].strftime('%Y-%m-%d %H:%M') if row[3] else '',
            "allocated_storage": row[4],
            "last_login": row[5].strftime('%Y-%m-%d %H:%M:%S') if row[5] else None,
            "last_login_iso": row[5].isoformat() if row[5] else None
        })
        
    return render_template("iam_dashboard.html", admins=admins)

@app.route("/add_admin_iam/<id>", methods=["POST"])
@login_required(roles=['root'])
def add_admin_iam(id):
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()
    storage = request.form.get("storage", "10").strip()
    
    if not all([name, email, password]):
        flash("All fields are required.", "error")
        return redirect(url_for('iam_dashboard', id=id))

    if not email.endswith("@sgmail.com"):
        flash("Email must end with @sgmail.com", "error")
        return redirect(url_for('iam_dashboard', id=id))
        
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Check if email exists
        cur.execute("SELECT 1 FROM users WHERE email = %s", (email,))
        if cur.fetchone():
             flash(f"User with email {email} already exists.", "error")
        else:
            new_id = str(uuid.uuid4())
            next_payment = datetime.now() + timedelta(days=365*10) # Admins get long access
            
            cur.execute("""
                INSERT INTO users (id, name, email, password, role, allocated_storage, next_payment)
                VALUES (%s, %s, %s, %s, 'admin', %s, %s)
            """, (new_id, name, email, password, int(storage), next_payment))
            
            conn.commit()
            
            # Create Folder
            os.makedirs(os.path.join(storage_path, new_id), exist_ok=True)
            
            flash(f"Admin '{name}' created successfully.", "success")
            
    except Exception as e:
        conn.rollback()
        flash(f"Error creating admin: {e}", "error")
    finally:
        cur.close()
        release_db_connection(conn)
        
    return redirect(url_for('iam_dashboard', id=id))

@app.route("/delete_admin/<id>", methods=["POST"])
@login_required(roles=['root'])
def delete_admin(id):
    target_id = request.form.get("target_admin_id")
    
    if not target_id:
        flash("Invalid admin ID.", "error")
        return redirect(url_for('iam_dashboard', id=id))
        
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Cascade delete
        cur.execute("DELETE FROM billing_requests WHERE user_id = %s", (target_id,))
        cur.execute("DELETE FROM support_requests WHERE user_id = %s", (target_id,))
        cur.execute("DELETE FROM notifications WHERE user_id = %s", (target_id,))
        cur.execute("DELETE FROM users WHERE id = %s", (target_id,))
        conn.commit()
        
        # Remove folder
        admin_folder = os.path.join(storage_path, target_id)
        if os.path.exists(admin_folder):
            shutil.rmtree(admin_folder)
            
        flash("Admin deleted successfully.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error deleting admin: {e}", "error")
    finally:
        cur.close()
        release_db_connection(conn)
        
    return redirect(url_for('iam_dashboard', id=id))

@app.route("/notify_admin/<id>", methods=["POST"])
@login_required(roles=['root'])
def notify_admin(id):
    target_id = request.form.get("target_admin_id")
    message = request.form.get("message", "").strip()
    
    if not message:
        flash("Message cannot be empty.", "error")
        return redirect(url_for('iam_dashboard', id=id))
        
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO notifications (user_id, sender, message) VALUES (%s, 'Root', %s)", (target_id, message))
        conn.commit()
        
        socketio.emit('new_notification', {
            'message': message,
            'sender': 'Root',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M')
        }, room=target_id)
        
        flash("Notification sent.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error sending notification: {e}", "error")
    finally:
        cur.close()
        release_db_connection(conn)
        
    return redirect(url_for('iam_dashboard', id=id))

if __name__ == "__main__":
    import socket
    
    # Get local IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except:
        local_ip = "127.0.0.1"

    print("\n" + "="*50)
    print("SERVER STARTED SUCCESSFULLY!")
    print("="*50)
    print(f" -> Local Access:   http://localhost:5000")
    print(f" -> Network Access: http://{local_ip}:5000")
    print("="*50 + "\n")
    print("Press CTRL+C to stop the server.\n")
    
    socketio.run(
        app,
        debug=os.environ.get("FLASK_DEBUG", "").lower() == "true",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
    )
