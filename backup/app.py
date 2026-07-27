import os, uuid, time, json, queue, threading, subprocess
from flask import (Flask, request, render_template,
                   send_from_directory, redirect, url_for, Response, stream_with_context)
from werkzeug.utils import secure_filename

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

def translate(text):
    try:
        return GoogleTranslator(source='auto', target='ta').translate(text) or text
    except Exception:
        return text

def write_srt(segments, path, use_tamil):
    with open(path, 'w', encoding='utf-8') as f:
        for i, seg in enumerate(segments):
            txt = seg['tamil'] if use_tamil else seg['english']
            f.write(f"{i+1}\n{fmt_time(seg['start'])} --> {fmt_time(seg['end'])}\n{txt}\n\n")

def extract_audio(video_path, audio_path):
    subprocess.run(
        ['ffmpeg','-i',video_path,'-q:a','0','-map','a',audio_path,'-y'],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def merge_subtitles(video_path, srt_path, output_path):
    safe = srt_path.replace('\\','/').replace(':','\\:')
    subprocess.run(
        ['ffmpeg','-i',video_path,
         '-vf', (f"subtitles='{safe}':force_style="
                 "'FontName=Noto Sans Tamil,FontSize=20,"
                 "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
                 "Outline=2,Bold=1'"),
         '-c:a','copy',output_path,'-y'],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ── Pipeline (runs in thread) ─────────────────────────────────────────────────

def process_video(job_id, video_path):
    try:
        base        = str(uuid.uuid4())
        audio_path  = os.path.join(AUDIO_FOLDER,    f"{base}.wav")
        tamil_srt   = os.path.join(SUBTITLE_FOLDER, f"{base}_ta.srt")
        eng_srt     = os.path.join(SUBTITLE_FOLDER, f"{base}_en.srt")
        output_name = f"{base}_tamil.mp4"
        output_path = os.path.join(OUTPUT_FOLDER, output_name)

        push(job_id, {"type":"progress","pct":5,"msg":"Extracting audio from video…"})
        extract_audio(video_path, audio_path)

        push(job_id, {"type":"progress","pct":20,"msg":"Transcribing speech with Whisper AI…"})
        result = MODEL.transcribe(audio_path)
        raws   = result.get("segments", [])
        if not raws:
            raise ValueError("No speech detected in the video.")

        push(job_id, {"type":"progress","pct":35,"msg":f"Found {len(raws)} segments — translating to Tamil…"})
        segments = []
        total = len(raws)

        for i, seg in enumerate(raws):
            english = seg['text'].strip()
            tamil   = translate(english)
            segments.append({"start":seg['start'],"end":seg['end'],
                              "english":english,"tamil":tamil})
            pct = 35 + int((i+1)/total*30)
            push(job_id, {
                "type":"segment","index":i+1,"total":total,"pct":pct,
                "start":fmt_time(seg['start']),"end":fmt_time(seg['end']),
                "english":english,"tamil":tamil,
            })

        set_job(job_id, segments=segments)

        push(job_id, {"type":"progress","pct":68,"msg":"Writing SRT subtitle files…"})
        write_srt(segments, tamil_srt, use_tamil=True)
        write_srt(segments, eng_srt,  use_tamil=False)

        push(job_id, {"type":"progress","pct":78,"msg":"Burning Tamil subtitles into video…"})
        merge_subtitles(video_path, tamil_srt, output_path)

        for p in [audio_path, video_path]:
            try: os.remove(p)
            except OSError: pass

        set_job(job_id, status="done", video=output_name,
                tamil_srt=os.path.basename(tamil_srt),
                eng_srt=os.path.basename(eng_srt))

        push(job_id, {"type":"done","pct":100,"msg":"Complete!",
                      "video":output_name,"job_id":job_id})

    except subprocess.CalledProcessError as e:
        msg = f"FFmpeg failed: {e}"
        set_job(job_id, status="error", message=msg)
        push(job_id, {"type":"error","msg":msg})
    except Exception as e:
        msg = str(e)
        set_job(job_id, status="error", message=msg)
        push(job_id, {"type":"error","msg":msg})


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET","POST"])
def index():
    error = None
    if request.method == "POST":
        f = request.files.get("video")
        if not f or f.filename == '':
            error = "Please select a video file."
        elif not allowed(f.filename):
            error = f"Unsupported format. Allowed: {', '.join(sorted(ALLOWED_EXT)).upper()}"
        else:
            filename   = secure_filename(f.filename)
            uname      = f"{uuid.uuid4()}_{filename}"
            vpath      = os.path.join(UPLOAD_FOLDER, uname)
            f.save(vpath)

            job_id = str(uuid.uuid4())
            SSE_QUEUES[job_id] = queue.Queue()
            set_job(job_id, status="queued", segments=[], video=None)

            threading.Thread(target=process_video,
                             args=(job_id, vpath), daemon=True).start()
            return redirect(url_for("processing", job_id=job_id))

    return render_template("index.html", error=error)


@app.route("/processing/<job_id>")
def processing(job_id):
    if job_id not in JOBS:
        return redirect(url_for("index"))
    return render_template("processing.html", job_id=job_id)


@app.route("/stream/<job_id>")
def stream(job_id):
    q = SSE_QUEUES.get(job_id)

    def generate():
        # replay already-translated segments (handles page refresh)
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
def watch(job_id):
    job = JOBS.get(job_id)
    if not job or job.get("status") != "done":
        return redirect(url_for("index"))
    return render_template("watch.html",
                           job_id=job_id,
                           video=job["video"],
                           segments=job.get("segments",[]),
                           tamil_srt=job.get("tamil_srt",""),
                           eng_srt=job.get("eng_srt",""))


@app.route("/outputs/<filename>")
def get_video(filename):
    return send_from_directory(OUTPUT_FOLDER, secure_filename(filename))

@app.route("/subtitles/<filename>")
def get_subtitle(filename):
    return send_from_directory(SUBTITLE_FOLDER, secure_filename(filename))

@app.route("/download/<filename>")
def download_video(filename):
    return send_from_directory(OUTPUT_FOLDER, secure_filename(filename), as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True, threaded=True)