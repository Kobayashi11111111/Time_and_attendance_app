
import os
import re
import sqlite3
from datetime import datetime
from flask import Flask, jsonify, request
 
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "kintai.db")
 
app = Flask(__name__, static_folder="public", static_url_path="")
 
TYPES = ["出勤", "退勤", "休憩開始", "休憩終了"]
 
# 今の状態で押せる打刻
ALLOWED = {
    "none": {"出勤"},
    "working": {"休憩開始", "退勤"},
    "break": {"休憩終了", "退勤"},
    "done": set(),
}
 
 
def get_db():
    return sqlite3.connect(DB_PATH)
 
 
# テーブルがなければ作る
with get_db() as db:
    db.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT,
            time TEXT
        )
    """)
 
 
# ---------- 共通の部品 ----------
 
def load_day(db, date):
    """その日の記録を、時刻順に [(id, type, datetime)] で返す。"""
    rows = db.execute(
        "SELECT id, type, time FROM attendance WHERE time LIKE ?",
        (date + "%",),
    ).fetchall()
    items = [(i, k, datetime.fromisoformat(t)) for i, k, t in rows]
    items.sort(key=lambda r: r[2])
    return items
 
 
def state_of(items):
    """none(未出勤) / working(勤務中) / break(休憩中) / done(退勤済み)"""
    state = "none"
    for _, kind, _ in items:
        if kind == "出勤" and state == "none":
            state = "working"
        elif kind == "休憩開始" and state == "working":
            state = "break"
        elif kind == "休憩終了" and state == "break":
            state = "working"
        elif kind == "退勤" and state in ("working", "break"):
            state = "done"
    return state
 
 
def summarize(items):
    """出勤・退勤・休憩・勤務時間(分)をまとめる。
    出勤はその日の最初、退勤は最後を使い、休憩の合計を引く。"""
    in_dt = next((d for _, k, d in items if k == "出勤"), None)
    out_dt = next((d for _, k, d in reversed(items) if k == "退勤"), None)
 
    break_sec = 0
    start = None
    for _, kind, dt in items:
        if kind == "休憩開始" and start is None:
            start = dt
        elif kind == "休憩終了" and start is not None:
            break_sec += (dt - start).total_seconds()
            start = None
    # 休憩終了を押し忘れていたら、退勤までを休憩とみなす
    if start is not None and out_dt and out_dt > start:
        break_sec += (out_dt - start).total_seconds()
 
    minutes = None
    if in_dt and out_dt and out_dt > in_dt:
        minutes = max(0, int(((out_dt - in_dt).total_seconds() - break_sec) // 60))
 
    return {
        "in": in_dt.strftime("%H:%M") if in_dt else None,
        "out": out_dt.strftime("%H:%M") if out_dt else None,
        "break_minutes": int(break_sec // 60),
        "minutes": minutes,
    }
 
 
def parse_time(text):
    """'2026-10-06 09:00' や '2026-10-06T09:00:00' を共通の形に直す。"""
    if not isinstance(text, str):
        return None
    text = text.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return None
 
 
def blocked_message(state, kind):
    if state == "done":
        return "今日はすでに退勤しています。直すときはカレンダーから修正できます"
    if state == "none":
        return "まだ出勤していません。先に出勤を押してください"
    if kind == "出勤":
        return "すでに出勤しています"
    if kind == "休憩開始":
        return "すでに休憩中です"
    return "休憩中ではありません"
 
 
# ---------- 打刻 ----------
 
def record(kind):
    now = datetime.now()
    with get_db() as db:
        state = state_of(load_day(db, now.strftime("%Y-%m-%d")))
        if kind not in ALLOWED[state]:
            return jsonify(message=blocked_message(state, kind)), 409
        now_text = now.strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "INSERT INTO attendance (type, time) VALUES (?, ?)",
            (kind, now_text),
        )
    return jsonify(message=f"{kind}を記録しました: {now_text}")
 
 
@app.route("/")
def index():
    return app.send_static_file("main.html")
 
 
@app.route("/api/clock-in", methods=["POST"])
def clock_in():
    return record("出勤")
 
 
@app.route("/api/clock-out", methods=["POST"])
def clock_out():
    return record("退勤")
 
 
@app.route("/api/break-start", methods=["POST"])
def break_start():
    return record("休憩開始")
 
 
@app.route("/api/break-end", methods=["POST"])
def break_end():
    return record("休憩終了")
 
 
@app.route("/api/status")
def status():
    today = datetime.now().strftime("%Y-%m-%d")
    with get_db() as db:
        items = load_day(db, today)
    info = summarize(items)
    return jsonify(state=state_of(items), **{"in": info["in"]})
 
 
# ---------- 集計 ----------
 
@app.route("/api/summary")
def summary():
    month = request.args.get("month") or datetime.now().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        return jsonify(error="month は YYYY-MM の形で指定してください"), 400
 
    with get_db() as db:
        rows = db.execute(
            "SELECT id, type, time FROM attendance WHERE time LIKE ?",
            (month + "%",),
        ).fetchall()
 
    by_day = {}
    for i, k, t in rows:
        dt = datetime.fromisoformat(t)
        by_day.setdefault(dt.strftime("%Y-%m-%d"), []).append((i, k, dt))
 
    result = {}
    for day, items in by_day.items():
        items.sort(key=lambda r: r[2])
        result[day] = summarize(items)
    return jsonify(days=result)
 
 
# ---------- 記録の確認・追加・修正・削除 ----------
 
@app.route("/api/records", methods=["GET", "POST"])
def records():
    if request.method == "GET":
        date = request.args.get("date", "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            return jsonify(message="date は YYYY-MM-DD の形で指定してください"), 400
        with get_db() as db:
            items = load_day(db, date)
        return jsonify(records=[
            {"id": i, "type": k, "time": d.strftime("%Y-%m-%d %H:%M:%S")}
            for i, k, d in items
        ])
 
    data = request.get_json(silent=True) or {}
    kind = data.get("type")
    time_text = parse_time(data.get("time"))
    if kind not in TYPES or not time_text:
        return jsonify(message="種類か時刻が正しくありません"), 400
    with get_db() as db:
        db.execute(
            "INSERT INTO attendance (type, time) VALUES (?, ?)",
            (kind, time_text),
        )
    return jsonify(message="追加しました"), 201
 
 
@app.route("/api/records/<int:rid>", methods=["PUT", "DELETE"])
def record_item(rid):
    with get_db() as db:
        if request.method == "DELETE":
            cur = db.execute("DELETE FROM attendance WHERE id = ?", (rid,))
            if cur.rowcount == 0:
                return jsonify(message="記録が見つかりません"), 404
            return jsonify(message="削除しました")
 
        data = request.get_json(silent=True) or {}
        kind = data.get("type")
        time_text = parse_time(data.get("time"))
        if kind not in TYPES or not time_text:
            return jsonify(message="種類か時刻が正しくありません"), 400
        cur = db.execute(
            "UPDATE attendance SET type = ?, time = ? WHERE id = ?",
            (kind, time_text, rid),
        )
        if cur.rowcount == 0:
            return jsonify(message="記録が見つかりません"), 404
    return jsonify(message="保存しました")
 
 
if __name__ == "__main__":
    app.run(port=3000)
 

