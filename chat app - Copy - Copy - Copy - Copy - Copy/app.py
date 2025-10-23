from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_socketio import SocketIO, emit
import sqlite3
import os
from werkzeug.utils import secure_filename
from datetime import datetime

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "chat.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
DEFAULT_AVATAR = "/static/default.png"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.secret_key = "replace_this_with_a_real_secret"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
socketio = SocketIO(app, cors_allowed_origins="*")

# -------------- DB helpers --------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            profile_pic TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT,
            receiver TEXT,
            message TEXT,
            timestamp TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_all_users():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username, profile_pic FROM users")
    rows = c.fetchall()
    conn.close()
    users = []
    for r in rows:
        users.append({
            "username": r["username"],
            "profile_pic": r["profile_pic"] if r["profile_pic"] else DEFAULT_AVATAR
        })
    return users

def get_user(username):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username, password, profile_pic FROM users WHERE username=?", (username,))
    r = c.fetchone()
    conn.close()
    return dict(r) if r else None

def create_user(username, password, profile_pic_path=None):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (username, password, profile_pic) VALUES (?, ?, ?)",
                  (username, password, profile_pic_path))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def update_user(old_username, new_username=None, new_password=None, profile_pic_path=None):
    conn = get_db()
    c = conn.cursor()
    user = get_user(old_username)
    if not user:
        conn.close()
        return False
    username_to_set = new_username if new_username else old_username
    password_to_set = new_password if new_password else user["password"]
    pic_to_set = profile_pic_path if profile_pic_path is not None else user["profile_pic"]
    try:
        c.execute("UPDATE users SET username=?, password=?, profile_pic=? WHERE username=?",
                  (username_to_set, password_to_set, pic_to_set, old_username))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return False
    conn.close()
    return True

def save_message(sender, receiver, message):
    ts = datetime.now().strftime("%H:%M")
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO messages (sender, receiver, message, timestamp) VALUES (?, ?, ?, ?)",
              (sender, receiver, message, ts))
    conn.commit()
    conn.close()
    return ts

def get_history(user1, user2):
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT sender, receiver, message, timestamp FROM messages
                 WHERE (sender=? AND receiver=?) OR (sender=? AND receiver=?)
                 ORDER BY id ASC""", (user1, user2, user2, user1))
    rows = c.fetchall()
    conn.close()
    return [{"sender": r["sender"], "receiver": r["receiver"], "message": r["message"], "timestamp": r["timestamp"]} for r in rows]

# -------------- Routes --------------
@app.route("/")
def index():
    if "username" in session:
        return redirect(url_for("chat"))
    return render_template("login.html")

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        file = request.files.get("profile_pic")
        pic_path = None
        if file and file.filename:
            filename = secure_filename(f"{username}_{file.filename}")
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
            pic_path = f"/static/uploads/{filename}"
        else:
            pic_path = DEFAULT_AVATAR
        ok = create_user(username, password, pic_path)
        if not ok:
            return "Username already exists", 400
        session["username"] = username
        return redirect(url_for("chat"))
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        user = get_user(username)
        if user and user["password"] == password:
            session["username"] = username
            return redirect(url_for("chat"))
        return "Invalid credentials", 400
    return render_template("login.html")

@app.route("/chat")
def chat():
    if "username" not in session:
        return redirect(url_for("login"))
    # pass full users list for avatar/profile info (not online subset)
    return render_template("chat.html", username=session["username"], users=get_all_users())

@app.route("/history/<other>")
def history(other):
    if "username" not in session:
        return jsonify({"history": []})
    hist = get_history(session["username"], other)
    return jsonify({"history": hist})

@app.route("/profile", methods=["GET","POST"])
def profile():
    if "username" not in session:
        return redirect(url_for("login"))
    user = get_user(session["username"])
    if request.method == "POST":
        new_username = request.form.get("username", "").strip()
        new_password = request.form.get("password", "").strip()
        file = request.files.get("profile_pic")
        pic_path = user["profile_pic"]
        if file and file.filename:
            filename = secure_filename(f"{session['username']}_{file.filename}")
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
            pic_path = f"/static/uploads/{filename}"
        # If new_username blank, keep old
        want_username = new_username if new_username else session["username"]
        ok = update_user(session["username"], new_username=want_username, new_password=new_password if new_password else None, profile_pic_path=pic_path)
        if not ok:
            return "Username already exists", 400
        session["username"] = want_username
        return redirect(url_for("chat"))
    return render_template("profile.html", user=user)

# -------------- Socket.IO --------------
# mapping username -> sid
user_sids = {}

@socketio.on("connect")
def on_connect():
    # client must call "register_user" immediately after connect passing username
    print("socket connected", request.sid)

@socketio.on("register_user")
def on_register(data):
    username = data.get("username")
    if not username:
        return
    user_sids[username] = request.sid
    # broadcast online users
    emit("update_user_list", list(user_sids.keys()), broadcast=True)
    print("registered", username, "->", request.sid)

@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    removed = None
    for uname, usid in list(user_sids.items()):
        if usid == sid:
            removed = uname
            del user_sids[uname]
            break
    emit("update_user_list", list(user_sids.keys()), broadcast=True)
    print("disconnect", sid, "removed", removed)

@socketio.on("private_message")
def on_private_message(data):
    # data expected: { sender, to, message }
    sender = data.get("sender")
    receiver = data.get("to")
    message = data.get("message")
    if not sender or not receiver or message is None:
        return
    ts = save_message(sender, receiver, message)
    payload = {"sender": sender, "receiver": receiver, "message": message, "timestamp": ts}
    # send to sender (ack)
    sid_sender = user_sids.get(sender)
    if sid_sender:
        emit("new_private_message", payload, room=sid_sender)
    # send to receiver if online
    sid_receiver = user_sids.get(receiver)
    if sid_receiver:
        emit("new_private_message", payload, room=sid_receiver)

# -------------- Run --------------
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)

