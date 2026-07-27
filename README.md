# 🎙️ TamilVoice: AI Video Subtitler

TamilVoice is a web application that automatically generates and translates subtitles for your videos. You just upload a video, and the app uses AI to figure out what is being said, translates it, and permanently adds (burns) Tamil subtitles right onto the video. It also gives you subtitle files for English, Tamil, and Hindi!

## ✨ Features

* **Easy Uploads:** Simple web interface to upload your videos.
* **AI Transcription:** Uses OpenAI's Whisper model to accurately convert speech to text.
* **Auto-Translation:** Automatically translates the English text into Tamil and Hindi.
* **Hardcoded Subtitles:** Burns the Tamil subtitles directly into the video file so they display everywhere without needing a separate file.
* **Live Progress Bar:** See exactly what the app is doing (extracting audio, translating, burning) in real-time.
* **User Accounts:** Register, log in, and see a history of all the videos you have processed.
* **Admin Dashboard:** A special panel for admins to manage users and view all processed videos.

## 🧠 How It Works

Here is the step-by-step journey of a video once you hit "Upload":

1. **Upload & Save:** The video is saved to the server.
2. **Audio Extraction:** The app uses a tool called FFmpeg to strip the audio out of the video and save it as a `.wav` file.
3. **AI Listening (Whisper):** The Whisper AI model "listens" to the audio and writes down everything said in English along with the exact timestamps.
4. **Translation:** The app takes those English sentences and runs them through Google Translate to get the Tamil and Hindi versions.
5. **Subtitle Creation:** It creates standard subtitle files (`.srt`) for all three languages.
6. **Burning Subtitles:** FFmpeg stitches the Tamil subtitle file directly onto the video frames.
7. **Ready to Watch:** The original video and audio are cleaned up, and you can now watch or download your newly subtitled video!

## 🛠️ Requirements

Before you install the app, make sure you have these installed on your computer:
* **Python 3.8 or higher**
* **MySQL:** A running database server.
* **FFmpeg:** This is absolutely required for handling the video and audio files.
  * *Windows:* Download from the FFmpeg website and add it to your PATH.
  * *Mac:* `brew install ffmpeg`
  * *Linux:* `sudo apt install ffmpeg`

## 🚀 Setup and Installation

**1. Clone the project**
```bash
git clone [https://github.com/yourusername/TamilVoice.git](https://github.com/yourusername/TamilVoice.git)
cd TamilVoice

2. Install the required Python packages

Bash
pip install Flask werkzeug mysql-connector-python deep-translator torch
pip install -U openai-whisper
3. Set up the Database
Make sure your MySQL server is running. Create a database named tamilvoice. You can configure your database credentials by setting environment variables or modifying the DB_CONFIG inside app.py.

Bash
export DB_USER="root"
export DB_PASS="yourpassword"
4. Create the Database Tables
Run the setup script to create the necessary tables for users and video history.

Bash
python create_db.py
5. Start the Application

Bash
python app.py
Open your web browser and go to http://127.0.0.1:5000.

(Note: The first time you run the app, a default admin account is created. Username: admin / Password: admin123)

📂 Folder Structure
app.py: The main code that runs the server and processes videos.

create_db.py: The script to set up your MySQL database.

templates/: The HTML files for the web pages (Dashboard, Login, Watch, etc.).

uploads/, audio/, subtitles/, outputs/: Folders automatically created by the app to store files while working.


***

Your `app.py` file relies on a separate script called `create_db.py` to set up the