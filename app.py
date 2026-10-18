from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3, time, os

app = Flask(__name__)
CORS(app)
DB = os.path.join(os.path.dirname(__file__), "chat.db")

def db():
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS msgs (id INTEGER PRIMARY KEY AUTOINCREMENT, room TEXT, nick TEXT, color TEXT, text TEXT, ts REAL)")
    return c

@app.route("/api/messages")
def get_msgs():
    room = request.args.get("room", "general")
    after = int(request.args.get("after", 0))
    c = db()
    rows = c.execute("SELECT id,nick,color,text,ts FROM msgs WHERE room=? AND id>? ORDER BY id DESC LIMIT 50", (room, after)).fetchall()
    c.close()
    rows.reverse()
    return jsonify([{"id": r[0], "nick": r[1], "color": r[2], "text": r[3], "ts": r[4]} for r in rows])

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
cur = c.execute("INSERT INTO msgs (room,nick,color,text,ts) VALUES (?,?,?,?,?)", (room, nick, color, text, time.time()))
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

@app.route("/")
def index():
    return "ErikTalk API is running"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
