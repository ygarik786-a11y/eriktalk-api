from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3, time, os, hashlib, secrets

app = Flask(__name__)
CORS(app)
DB = os.path.join(os.path.dirname(__file__), "chat.db")


def db():
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS msgs (id INTEGER PRIMARY KEY AUTOINCREMENT, room TEXT, nick TEXT, color TEXT, text TEXT, ts REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS online (nick TEXT, color TEXT, room TEXT, ts REAL, PRIMARY KEY (nick, room))")
    c.execute("CREATE TABLE IF NOT EXISTS birthdays (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, day INTEGER, month INTEGER, year INTEGER, status TEXT DEFAULT 'pending', public INTEGER DEFAULT 0, created_at REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS tokens (token TEXT PRIMARY KEY, expires REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS bans (nick TEXT PRIMARY KEY, ts REAL)")
    for tbl in ("msgs", "online"):
        try:
            c.execute(f"ALTER TABLE {tbl} ADD COLUMN admin INTEGER DEFAULT 0")
        except Exception:
            pass
    return c


def _is_banned(c, nick):
    # Сравнение в Python: SQLite LOWER() не умеет кириллицу
    target = (nick or "").strip().lower()
    rows = c.execute("SELECT nick FROM bans").fetchall()
    return any((r[0] or "").strip().lower() == target for r in rows)


def _pin_ok(c, pin):
    if not pin:
        return False
    stored = _get_config(c, "chat_pin")
    if not stored or ":" not in stored:
        return False
    salt, h = stored.split(":", 1)
    return secrets.compare_digest(_hash_pw(str(pin), salt), h)


# ---------- ErikTalk chat ----------

@app.route("/api/messages")
def get_msgs():
    room = request.args.get("room", "general")
    after = int(request.args.get("after", 0))
    c = db()
    rows = c.execute("SELECT id,nick,color,text,ts,admin FROM msgs WHERE room=? AND id>? ORDER BY id DESC LIMIT 50", (room, after)).fetchall()
    c.close()
    rows.reverse()
    return jsonify([{"id": r[0], "nick": r[1], "color": r[2], "text": r[3], "ts": r[4], "admin": r[5] or 0} for r in rows])


@app.route("/api/messages", methods=["POST"])
def post_msg():
    d = request.get_json(force=True)
    nick = (d.get("nick") or "Гость")[:20]
    color = (d.get("color") or "#4CAF50")[:10]
    text = (d.get("text") or "")[:500].strip()
    room = (d.get("room") or "general")[:30]
    if not text:
        return jsonify({"ok": False}), 400
    c = db()
    if _is_banned(c, nick):
        c.close()
        return jsonify({"ok": False, "error": "banned"}), 403
    is_admin = 1 if _pin_ok(c, d.get("pin")) else 0
    cur = c.execute("INSERT INTO msgs (room,nick,color,text,ts,admin) VALUES (?,?,?,?,?,?)", (room, nick, color, text, time.time(), is_admin))
    # keep last 500 per room
    c.execute("DELETE FROM msgs WHERE room=? AND id NOT IN (SELECT id FROM msgs WHERE room=? ORDER BY id DESC LIMIT 500)", (room, room))
    c.commit()
    mid = cur.lastrowid
    c.close()
    return jsonify({"ok": True, "id": mid})


@app.route("/api/rooms")
def rooms():
    c = db()
    rows = c.execute("SELECT room, COUNT(*) FROM msgs GROUP BY room").fetchall()
    c.close()
    return jsonify({r[0]: r[1] for r in rows})


@app.route("/api/ping", methods=["POST"])
def ping():
    d = request.get_json(force=True) or {}
    nick = (d.get("nick") or "Гость")[:20]
    color = (d.get("color") or "#4CAF50")[:10]
    room = (d.get("room") or "general")[:30]
    c = db()
    if _is_banned(c, nick):
        c.close()
        return jsonify({"ok": False, "error": "banned"}), 403
    is_admin = 1 if _pin_ok(c, d.get("pin")) else 0
    c.execute("INSERT INTO online (nick,color,room,ts,admin) VALUES (?,?,?,?,?) "
              "ON CONFLICT(nick,room) DO UPDATE SET color=excluded.color, ts=excluded.ts, admin=excluded.admin",
              (nick, color, room, time.time(), is_admin))
    c.execute("DELETE FROM online WHERE ts < ?", (time.time() - 300,))
    c.commit()
    c.close()
    return jsonify({"ok": True})


@app.route("/api/online")
def online():
    room = request.args.get("room", "general")
    c = db()
    rows = c.execute("SELECT nick,color,admin FROM online WHERE room=? AND ts>? ORDER BY nick",
                     (room, time.time() - 90)).fetchall()
    c.close()
    return jsonify([{"nick": r[0], "color": r[1], "admin": r[2] or 0} for r in rows])


# ---------- Birthdays ----------

def _valid_bday(d):
    try:
        day = int(d.get("day"))
        month = int(d.get("month"))
    except (TypeError, ValueError):
        return None
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return None
    year = d.get("year")
    if year in (None, "", 0):
        year = None
    else:
        try:
            year = int(year)
        except (TypeError, ValueError):
            return None
        if not (1900 <= year <= 2100):
            return None
    name = str(d.get("name") or "").strip()[:40]
    if not name:
        return None
    return name, day, month, year


@app.route("/api/birthdays", methods=["POST"])
def submit_birthday():
    d = request.get_json(force=True) or {}
    v = _valid_bday(d)
    if not v:
        return jsonify({"ok": False, "error": "bad data"}), 400
    name, day, month, year = v
    c = db()
    c.execute("INSERT INTO birthdays (name,day,month,year,status,public,created_at) VALUES (?,?,?,?,'pending',0,?)",
              (name, day, month, year, time.time()))
    c.commit()
    c.close()
    return jsonify({"ok": True})


@app.route("/api/birthdays")
def public_birthdays():
    c = db()
    rows = c.execute("SELECT name,day,month,year FROM birthdays WHERE status='approved' AND public=1 ORDER BY month,day").fetchall()
    c.close()
    return jsonify([{"name": r[0], "day": r[1], "month": r[2], "year": r[3]} for r in rows])


# ---------- Admin (New Year site) ----------

def _get_config(c, key):
    row = c.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _hash_pw(password, salt):
    return hashlib.sha256((salt + "|" + password).encode("utf-8")).hexdigest()


def _is_admin():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    token = auth[7:].strip()
    if not token:
        return False
    c = db()
    row = c.execute("SELECT expires FROM tokens WHERE token=?", (token,)).fetchone()
    c.close()
    return bool(row and row[0] > time.time())


@app.route("/api/admin/setup-status")
def setup_status():
    c = db()
    done = _get_config(c, "admin_pass") is not None
    c.close()
    return jsonify({"setup_done": done})


@app.route("/api/admin/setup", methods=["POST"])
def admin_setup():
    d = request.get_json(force=True) or {}
    password = str(d.get("password") or "")
    if len(password) < 4:
        return jsonify({"ok": False, "error": "password too short"}), 400
    c = db()
    if _get_config(c, "admin_pass") is not None:
        c.close()
        return jsonify({"ok": False, "error": "already set"}), 403
    salt = secrets.token_hex(8)
    c.execute("INSERT INTO config (key,value) VALUES ('admin_pass',?)", (salt + ":" + _hash_pw(password, salt),))
    c.commit()
    c.close()
    return jsonify({"ok": True})


@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    d = request.get_json(force=True) or {}
    password = str(d.get("password") or "")
    c = db()
    stored = _get_config(c, "admin_pass")
    if not stored or ":" not in stored:
        c.close()
        return jsonify({"ok": False, "error": "not set up"}), 403
    salt, h = stored.split(":", 1)
    if not secrets.compare_digest(_hash_pw(password, salt), h):
        c.close()
        return jsonify({"ok": False, "error": "wrong password"}), 401
    token = secrets.token_hex(24)
    c.execute("INSERT INTO tokens (token,expires) VALUES (?,?)", (token, time.time() + 7 * 24 * 3600))
    c.execute("DELETE FROM tokens WHERE expires < ?", (time.time(),))
    c.commit()
    c.close()
    return jsonify({"ok": True, "token": token})


@app.route("/api/admin/birthdays")
def admin_birthdays():
    if not _is_admin():
        return jsonify({"ok": False}), 401
    c = db()
    rows = c.execute("SELECT id,name,day,month,year,status,public FROM birthdays ORDER BY month,day").fetchall()
    c.close()
    return jsonify([{"id": r[0], "name": r[1], "day": r[2], "month": r[3], "year": r[4],
                     "status": r[5], "public": r[6]} for r in rows])


@app.route("/api/admin/birthdays", methods=["POST"])
def admin_add_birthday():
    if not _is_admin():
        return jsonify({"ok": False}), 401
    d = request.get_json(force=True) or {}
    v = _valid_bday(d)
    if not v:
        return jsonify({"ok": False, "error": "bad data"}), 400
    name, day, month, year = v
    c = db()
    cur = c.execute("INSERT INTO birthdays (name,day,month,year,status,public,created_at) VALUES (?,?,?,?,'approved',0,?)",
                    (name, day, month, year, time.time()))
    c.commit()
    mid = cur.lastrowid
    c.close()
    return jsonify({"ok": True, "id": mid})


@app.route("/api/admin/birthdays/<int:bid>/approve", methods=["POST"])
def admin_approve(bid):
    if not _is_admin():
        return jsonify({"ok": False}), 401
    c = db()
    c.execute("UPDATE birthdays SET status='approved' WHERE id=?", (bid,))
    c.commit()
    c.close()
    return jsonify({"ok": True})


@app.route("/api/admin/birthdays/<int:bid>/public", methods=["POST"])
def admin_public(bid):
    if not _is_admin():
        return jsonify({"ok": False}), 401
    d = request.get_json(force=True) or {}
    c = db()
    c.execute("UPDATE birthdays SET public=? WHERE id=?", (1 if d.get("public") else 0, bid))
    c.commit()
    c.close()
    return jsonify({"ok": True})


@app.route("/api/admin/birthdays/<int:bid>/delete", methods=["POST"])
def admin_delete(bid):
    if not _is_admin():
        return jsonify({"ok": False}), 401
    c = db()
    c.execute("DELETE FROM birthdays WHERE id=?", (bid,))
    c.commit()
    c.close()
    return jsonify({"ok": True})


@app.route("/api/announcement")
def announcement():
    c = db()
    text = _get_config(c, "announcement") or ""
    c.close()
    return jsonify({"text": text})


@app.route("/api/admin/announcement", methods=["POST"])
def admin_announcement():
    if not _is_admin():
        return jsonify({"ok": False}), 401
    d = request.get_json(force=True) or {}
    text = str(d.get("text") or "").strip()[:300]
    c = db()
    c.execute("INSERT INTO config (key,value) VALUES ('announcement',?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (text,))
    c.commit()
    c.close()
    return jsonify({"ok": True})


# ---------- Admin: управление чатом ErikTalk ----------

@app.route("/api/admin/chat/overview")
def admin_chat_overview():
    if not _is_admin():
        return jsonify({"ok": False}), 401
    c = db()
    rows = c.execute("SELECT nick,color,room,admin FROM online WHERE ts>? ORDER BY room,nick",
                     (time.time() - 120,)).fetchall()
    bans = [r[0] for r in c.execute("SELECT nick FROM bans ORDER BY nick").fetchall()]
    pin_set = _get_config(c, "chat_pin") is not None
    c.close()
    return jsonify({"online": [{"nick": r[0], "color": r[1], "room": r[2], "admin": r[3] or 0} for r in rows],
                    "bans": bans, "pin_set": pin_set})


@app.route("/api/admin/chat/ban", methods=["POST"])
def admin_chat_ban():
    if not _is_admin():
        return jsonify({"ok": False}), 401
    d = request.get_json(force=True) or {}
    nick = str(d.get("nick") or "").strip()[:20]
    if not nick:
        return jsonify({"ok": False, "error": "no nick"}), 400
    c = db()
    c.execute("INSERT INTO bans (nick,ts) VALUES (?,?) ON CONFLICT(nick) DO UPDATE SET ts=excluded.ts",
              (nick, time.time()))
    for (onick, oroom) in c.execute("SELECT nick,room FROM online").fetchall():
        if (onick or "").strip().lower() == nick.strip().lower():
            c.execute("DELETE FROM online WHERE nick=? AND room=?", (onick, oroom))
    c.commit()
    c.close()
    return jsonify({"ok": True})


@app.route("/api/admin/chat/unban", methods=["POST"])
def admin_chat_unban():
    if not _is_admin():
        return jsonify({"ok": False}), 401
    d = request.get_json(force=True) or {}
    nick = str(d.get("nick") or "").strip()[:20]
    c = db()
    for (bnick,) in c.execute("SELECT nick FROM bans").fetchall():
        if (bnick or "").strip().lower() == nick.strip().lower():
            c.execute("DELETE FROM bans WHERE nick=?", (bnick,))
    c.commit()
    c.close()
    return jsonify({"ok": True})


@app.route("/api/admin/chat/pin", methods=["POST"])
def admin_chat_pin():
    if not _is_admin():
        return jsonify({"ok": False}), 401
    d = request.get_json(force=True) or {}
    pin = str(d.get("pin") or "")
    if len(pin) < 4:
        return jsonify({"ok": False, "error": "pin too short"}), 400
    salt = secrets.token_hex(8)
    c = db()
    c.execute("INSERT INTO config (key,value) VALUES ('chat_pin',?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (salt + ":" + _hash_pw(pin, salt),))
    c.commit()
    c.close()
    return jsonify({"ok": True})


# ---------- Цвет часов (Отсчёты + главная) ----------

@app.route("/api/clock-color")
def clock_color():
    c = db()
    color = _get_config(c, "clock_color") or ""
    c.close()
    return jsonify({"color": color})


@app.route("/api/admin/clock-color", methods=["POST"])
def admin_clock_color():
    if not _is_admin():
        return jsonify({"ok": False}), 401
    d = request.get_json(force=True) or {}
    color = str(d.get("color") or "").strip()
    if color and not (len(color) == 7 and color[0] == "#" and all(ch in "0123456789abcdefABCDEF" for ch in color[1:])):
        return jsonify({"ok": False, "error": "bad color"}), 400
    c = db()
    c.execute("INSERT INTO config (key,value) VALUES ('clock_color',?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (color,))
    c.commit()
    c.close()
    return jsonify({"ok": True})


@app.route("/")
def index():
    return "ErikTalk API is running 💬"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
