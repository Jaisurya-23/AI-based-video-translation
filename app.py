import os, uuid, time, json, queue, threading, subprocess, hashlib, re
from datetime import datetime
from flask import (Flask, request, render_template,
                   send_from_directory, redirect, url_for, Response,
                   stream_with_context, session, flash)
from werkzeug.utils import secure_filename
import mysql.connector

# ── Whisper import guard ──────────────────────────────────────────────────────
import whisper
if not hasattr(whisper, "load_model"):
    raise ImportError(
        "Wrong 'whisper' package.\n"
        "  pip uninstall whisper\n"
        "  pip install -U openai-whisper torch"
    )

from deep_translator import GoogleTranslator

# ── App ───────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "tamilvoice_secret_2024")
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

UPLOAD_FOLDER   = "uploads"
AUDIO_FOLDER    = "audio"
OUTPUT_FOLDER   = "outputs"
SUBTITLE_FOLDER = "subtitles"

for d in [UPLOAD_FOLDER, AUDIO_FOLDER, OUTPUT_FOLDER, SUBTITLE_FOLDER]:
    os.makedirs(d, exist_ok=True)

ALLOWED_EXT = {'mp4', 'avi', 'mov', 'mkv', 'webm', 'flv', 'wmv'}

# job_id → dict with status, segments, video, etc.
JOBS: dict = {}
# job_id → queue.Queue for SSE events
SSE_QUEUES: dict = {}

print("Loading Whisper model…")
MODEL = whisper.load_model("base")
print("Whisper ready.")

# ── DB ────────────────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "user": os.environ.get("DB_USER", "root"),
    "password": os.environ.get("DB_PASS", ""),
    "database": os.environ.get("DB_NAME", "tamilvoice"),
    "charset": "utf8",
    "use_unicode": True,
}

def get_db():
    return mysql.connector.connect(**DB_CONFIG)

def init_db():
    # Database creation and table setup moved to create_db.py
    # Keep this function as a no-op to avoid duplicate creation.
    return

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def get_user(username):
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM users WHERE username=%s", (username,))
    row = cur.fetchone()
    cur.close(); conn.close()
    return row

def get_user_by_id(uid):
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM users WHERE id=%s", (uid,))
    row = cur.fetchone()
    cur.close(); conn.close()
    return row

def create_user(username, email, password):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO users (username, email, password_hash) VALUES (%s,%s,%s)",
                (username, email, password))
    conn.commit(); cur.close(); conn.close()

def save_history(user_id, job_id, original_filename):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""INSERT INTO video_history (user_id, job_id, original_filename, status)
                   VALUES (%s,%s,%s,'processing')""",
                (user_id, job_id, original_filename))
    conn.commit(); cur.close(); conn.close()

def update_history(job_id, output_filename, tamil_srt, eng_srt, hindi_srt, segment_count, status):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""UPDATE video_history
                   SET output_filename=%s, tamil_srt=%s, eng_srt=%s, hindi_srt=%s,
                       segment_count=%s, status=%s
                   WHERE job_id=%s""",
                (output_filename, tamil_srt, eng_srt, hindi_srt, segment_count, status, job_id))
    conn.commit(); cur.close(); conn.close()

def get_user_history(user_id):
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM video_history WHERE user_id=%s ORDER BY created_at DESC", (user_id,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows

def get_all_users():
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id, username, email, role, created_at, is_active FROM users ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows

def get_all_history():
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""SELECT vh.*, u.username FROM video_history vh
                   JOIN users u ON vh.user_id=u.id
                   ORDER BY vh.created_at DESC""")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows

def toggle_user_active(uid):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_active = NOT is_active WHERE id=%s", (uid,))
    conn.commit(); cur.close(); conn.close()

def delete_user(uid):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE id=%s AND role!='admin'", (uid,))
    conn.commit(); cur.close(); conn.close()

# ── Auth helpers ──────────────────────────────────────────────────────────────
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('home'))
        user = get_user_by_id(session['user_id'])
        if not user or not user['is_active']:
            session.clear()
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('home'))
        user = get_user_by_id(session['user_id'])
        if not user or user['role'] != 'admin':
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated

# ── Helpers ───────────────────────────────────────────────────────────────────

def allowed(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

def fmt_time(s):
    h=int(s//3600); m=int((s%3600)//60); sec=int(s%60); ms=int((s-int(s))*1000)
    return f"{h:02}:{m:02}:{sec:02},{ms:03}"

def push(job_id, data):
    q = SSE_QUEUES.get(job_id)
    if q:
        q.put(json.dumps(data))

def set_job(job_id, **kw):
    JOBS.setdefault(job_id, {}).update(kw)

def translate_text(text, target):
    try:
        return GoogleTranslator(source='auto', target=target).translate(text) or text
    except Exception:
        return text

def write_srt(segments, path, lang_key):
    with open(path, 'w', encoding='utf-8') as f:
        for i, seg in enumerate(segments):
            txt = seg.get(lang_key, seg['english'])
            f.write(f"{i+1}\n{fmt_time(seg['start'])} --> {fmt_time(seg['end'])}\n{txt}\n\n")

def extract_audio(video_path, audio_path):
    subprocess.run(
        ['ffmpeg','-i',video_path,'-q:a','0','-map','a',audio_path,'-y'],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def merge_subtitles(video_path, srt_path, output_path):
    # Normalize rotation (if needed) before burning subtitles
    def normalize_rotation(inp):
        try:
            # probe for rotate tag
            out = subprocess.check_output([
                'ffprobe','-v','error','-select_streams','v:0',
                '-show_entries','stream_tags=rotate',
                '-of','default=noprint_wrappers=1:nokey=1', inp
            ])
            rot = out.decode().strip()
            if not rot:
                return inp, False
            rotv = int(rot)
            if rotv == 0:
                return inp, False
            vf = None
            if rotv == 90:
                vf = 'transpose=1'
            elif rotv == 180:
                vf = 'transpose=1,transpose=1'
            elif rotv == 270:
                vf = 'transpose=2'
            if vf is None:
                return inp, False
            tmp = inp + '.norm.mp4'
            subprocess.run(['ffmpeg','-i',inp,'-vf',vf,'-c:a','copy',tmp,'-y'], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return tmp, True
        except Exception:
            return inp, False

    inp_video, created = normalize_rotation(video_path)
    safe = srt_path.replace('\\','/').replace(':','\\:')
    subprocess.run(
        ['ffmpeg','-i',inp_video,
         '-vf', (f"subtitles='{safe}':force_style="
                 "'FontName=Noto Sans Tamil,FontSize=20,"
                 "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
                 "Outline=2,Bold=1'"),
         '-c:a','copy',output_path,'-y'],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # clean up temporary normalized file
    try:
        if created and inp_video != video_path:
            os.remove(inp_video)
    except OSError:
        pass


# ── Pipeline (runs in thread) ─────────────────────────────────────────────────

def process_video(job_id, video_path, user_id, original_filename):
    try:
        base        = str(uuid.uuid4())
        audio_path  = os.path.join(AUDIO_FOLDER,    f"{base}.wav")
        tamil_srt   = os.path.join(SUBTITLE_FOLDER, f"{base}_ta.srt")
        eng_srt     = os.path.join(SUBTITLE_FOLDER, f"{base}_en.srt")
        hindi_srt   = os.path.join(SUBTITLE_FOLDER, f"{base}_hi.srt")
        burned_name = f"{base}_tamil.mp4"
        burned_path = os.path.join(OUTPUT_FOLDER, burned_name)
        clean_name = f"{base}.mp4"
        clean_path = os.path.join(OUTPUT_FOLDER, clean_name)

        # Create a clean copy (no burned subtitles) for web playback
        try:
            subprocess.run(['ffmpeg','-i',video_path,'-c','copy',clean_path,'-y'], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            # fallback: copy file if ffmpeg not available or copy fails
            try:
                import shutil
                shutil.copy(video_path, clean_path)
            except Exception:
                pass

        push(job_id, {"type":"progress","pct":5,"msg":"Extracting audio from video…"})
        extract_audio(video_path, audio_path)

        push(job_id, {"type":"progress","pct":20,"msg":"Transcribing speech with Whisper AI…"})
        result = MODEL.transcribe(audio_path)
        raws   = result.get("segments", [])
        if not raws:
            raise ValueError("No speech detected in the video.")

        push(job_id, {"type":"progress","pct":35,"msg":f"Found {len(raws)} segments — translating to Tamil, English & Hindi…"})
        segments = []
        total = len(raws)

        for i, seg in enumerate(raws):
            english = seg['text'].strip()
            tamil   = translate_text(english, 'ta')
            hindi   = translate_text(english, 'hi')
            segments.append({"start":seg['start'],"end":seg['end'],
                              "english":english,"tamil":tamil,"hindi":hindi})
            pct = 35 + int((i+1)/total*30)
            push(job_id, {
                "type":"segment","index":i+1,"total":total,"pct":pct,
                "start":fmt_time(seg['start']),"end":fmt_time(seg['end']),
                "english":english,"tamil":tamil,"hindi":hindi,
            })

        set_job(job_id, segments=segments)

        push(job_id, {"type":"progress","pct":68,"msg":"Writing SRT subtitle files…"})
        write_srt(segments, tamil_srt, 'tamil')
        write_srt(segments, eng_srt,  'english')
        write_srt(segments, hindi_srt,'hindi')

        push(job_id, {"type":"progress","pct":78,"msg":"Burning Tamil subtitles into video…"})
        # Burn subtitles into a separate burned file for downloads
        merge_subtitles(clean_path, tamil_srt, burned_path)

        for p in [audio_path, video_path]:
            try: os.remove(p)
            except OSError: pass

        tamil_srt_name = os.path.basename(tamil_srt)
        eng_srt_name   = os.path.basename(eng_srt)
        hindi_srt_name = os.path.basename(hindi_srt)

        set_job(job_id, status="done", video=clean_name, burned_video=burned_name,
            tamil_srt=tamil_srt_name,
            eng_srt=eng_srt_name,
            hindi_srt=hindi_srt_name)

        # Update DB with the burned filename (keeps backward compatibility for downloads)
        update_history(job_id, burned_name, tamil_srt_name, eng_srt_name,
                   hindi_srt_name, len(segments), 'done')

        push(job_id, {"type":"done","pct":100,"msg":"Complete!",
                  "video":clean_name,"job_id":job_id})

    except subprocess.CalledProcessError as e:
        msg = f"FFmpeg failed: {e}"
        set_job(job_id, status="error", message=msg)
        update_history(job_id, None, None, None, None, 0, 'error')
        push(job_id, {"type":"error","msg":msg})
    except Exception as e:
        msg = str(e)
        set_job(job_id, status="error", message=msg)
        try: update_history(job_id, None, None, None, None, 0, 'error')
        except: pass
        push(job_id, {"type":"error","msg":msg})


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        if user and user['is_active']:
            if user['role'] == 'admin':
                return redirect(url_for('admin_panel'))
            return redirect(url_for('dashboard'))
    return render_template("home.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == 'GET':
        return render_template("login.html")
    username = request.form.get("username","").strip()
    password = request.form.get("password","")
    user = get_user(username)
    if not user or user['password_hash'] != password:
        return render_template("login.html", login_error="Invalid username or password.")
    if not user['is_active']:
        return render_template("login.html", login_error="Your account has been disabled.")
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['role'] = user['role']
    if user['role'] == 'admin':
        return redirect(url_for('admin_panel'))
    return redirect(url_for('dashboard'))



@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('home'))

# ── Dashboard ──────────────────────────────────────────────────────────────────

@app.route("/dashboard")
@login_required
def dashboard():
    user = get_user_by_id(session['user_id'])
    history = get_user_history(session['user_id'])
    return render_template("dashboard.html", user=user, history=history)

# ── Upload / Process ───────────────────────────────────────────────────────────

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == 'GET':
        return render_template("register.html")
    username = request.form.get("username","").strip()
    email    = request.form.get("email","")
    password = request.form.get("password","")
    confirm  = request.form.get("confirm","")
    if not username or not email or not password:
        return render_template("register.html", reg_error="All fields are required.")
    if password != confirm:
        return render_template("register.html", reg_error="Passwords do not match.")
    # password length requirement removed per user request
    if not re.match(r'^[a-zA-Z0-9_]+$', username):
        return render_template("register.html", reg_error="Username can only contain letters, numbers, underscores.")
    try:
        create_user(username, email, password)
    except mysql.connector.IntegrityError:
        return render_template("register.html", reg_error="Username or email already exists.")
    user = get_user(username)
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['role'] = user['role']
    return redirect(url_for('dashboard'))


@app.route("/processing/<job_id>")
@login_required
def processing(job_id):
    if job_id not in JOBS:
        return redirect(url_for("dashboard"))
    return render_template("processing.html", job_id=job_id)


@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == 'GET':
        return render_template('index.html')
    # POST: handle uploaded video
    f = request.files.get('video')
    if not f or f.filename == '':
        return render_template('index.html', error='No file selected')
    filename = secure_filename(f.filename)
    if not allowed(filename):
        return render_template('index.html', error='Unsupported file type')
    base = str(uuid.uuid4())
    save_name = f"{base}_{filename}"
    save_path = os.path.join(UPLOAD_FOLDER, save_name)
    f.save(save_path)

    # create job and start processing thread
    job_id = base
    set_job(job_id, status='processing')
    # associate with logged-in user if present, otherwise fallback to admin (id 1)
    user_id = session.get('user_id', 1)
    try:
        save_history(user_id, job_id, filename)
    except Exception:
        pass

    t = threading.Thread(target=process_video, args=(job_id, save_path, user_id, filename))
    t.daemon = True
    t.start()
    return redirect(url_for('processing', job_id=job_id))


@app.route("/stream/<job_id>")
def stream(job_id):
    q = SSE_QUEUES.setdefault(job_id, queue.Queue())

    def generate():
        for seg in JOBS.get(job_id, {}).get("segments", []):
            total = len(JOBS[job_id]["segments"])
            yield f"data: {json.dumps({'type':'segment',**seg,'total':total})}\n\n"

        if JOBS.get(job_id, {}).get("status") == "done":
            job = JOBS[job_id]
            yield f"data: {json.dumps({'type':'done','pct':100,'msg':'Complete!','video':job['video'],'job_id':job_id})}\n\n"
            return

        if not q:
            return

        while True:
            try:
                msg  = q.get(timeout=30)
                yield f"data: {msg}\n\n"
                data = json.loads(msg)
                if data.get("type") in ("done","error"):
                    break
            except queue.Empty:
                yield 'data: {"type":"ping"}\n\n'

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})


@app.route("/watch/<job_id>")
@login_required
def watch(job_id):
    # Check in-memory first
    job = JOBS.get(job_id)
    if job and job.get("status") == "done":
        return render_template("watch.html",
                               job_id=job_id,
                               video=job["video"],
                               burned_video=job.get("burned_video", job.get("video")),
                               segments=job.get("segments",[]),
                               tamil_srt=job.get("tamil_srt",""),
                               eng_srt=job.get("eng_srt",""),
                               hindi_srt=job.get("hindi_srt",""))

    # Fall back to DB history (for revisiting old videos)
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM video_history WHERE job_id=%s AND user_id=%s",
                (job_id, session['user_id']))
    row = cur.fetchone()
    cur.close(); conn.close()

    if not row or row['status'] != 'done':
        return redirect(url_for("dashboard"))

    # If we only have DB row (no in-memory job), prefer a clean playback filename when possible
    burned = row['output_filename']
    clean_guess = burned
    if burned.endswith('_tamil.mp4'):
        clean_guess = burned.replace('_tamil.mp4', '.mp4')
    return render_template("watch.html",
                           job_id=job_id,
                           video=clean_guess,
                           burned_video=burned,
                           segments=[],
                           tamil_srt=row.get("tamil_srt",""),
                           eng_srt=row.get("eng_srt",""),
                           hindi_srt=row.get("hindi_srt",""))


# ── Admin ──────────────────────────────────────────────────────────────────────

@app.route("/admin")
@admin_required
def admin_panel():
    users = get_all_users()
    history = get_all_history()
    total_videos = len(history)
    done_videos  = sum(1 for h in history if h['status']=='done')
    return render_template("admin.html",
                           users=users,
                           history=history,
                           total_videos=total_videos,
                           done_videos=done_videos,
                           admin_username=session.get('username'))

@app.route("/admin/toggle/<int:uid>", methods=["POST"])
@admin_required
def admin_toggle(uid):
    toggle_user_active(uid)
    return redirect(url_for('admin_panel'))

@app.route("/admin/delete/<int:uid>", methods=["POST"])
@admin_required
def admin_delete(uid):
    delete_user(uid)
    return redirect(url_for('admin_panel'))


# ── File serving ───────────────────────────────────────────────────────────────

@app.route("/outputs/<filename>")
def get_video(filename):
    return send_from_directory(OUTPUT_FOLDER, secure_filename(filename))

@app.route("/subtitles/<filename>")
def get_subtitle(filename):
    return send_from_directory(SUBTITLE_FOLDER, secure_filename(filename))

@app.route("/download/<filename>")
@login_required
def download_video(filename):
    return send_from_directory(OUTPUT_FOLDER, secure_filename(filename), as_attachment=True)


if __name__ == "__main__":
    # Database initialization handled by create_db.py; do not run here.
    # Create default admin if none exists
    try:
        existing = get_user('admin')
        if not existing:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("INSERT INTO users (username, email, password_hash, role) VALUES (%s,%s,%s,'admin')",
                        ('admin', 'admin@tamilvoice.local', 'admin123'))
            conn.commit(); cur.close(); conn.close()
            print("Default admin created: admin / admin123")
    except Exception as e:
        print(f"Admin creation skipped: {e}")

    # Database initialization is handled by create_db.py outside this module.
    app.run(debug=True, threaded=True)