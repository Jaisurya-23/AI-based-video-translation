The error [WinError 2] The system cannot find the file specified means FFmpeg can't be found on your system PATH. The file uploaded fine — FFmpeg just isn't accessible.
Let me also fix a Windows-specific path issue in the code at the same time.
First, verify FFmpeg:
ffmpeg -version
If you get 'ffmpeg' is not recognized — that's the problem. Here's how to fix it:

Step 1 — Download FFmpeg
Go to: https://www.gyan.dev/ffmpeg/builds/
Download: ffmpeg-release-essentials.zip

Step 2 — Extract it
Extract to somewhere simple like:
C:\ffmpeg\
So the folder looks like:
C:\ffmpeg\bin\ffmpeg.exe
C:\ffmpeg\bin\ffprobe.exe

Step 3 — Add to Windows PATH

Press Win + S → search "Environment Variables"
Click "Edit the system environment variables"
Click "Environment Variables" button
Under System variables, find Path → click Edit
Click New → type C:\ffmpeg\bin
Click OK on all dialogs


Step 4 — Restart your terminal
Close and reopen your terminal (the PATH change won't apply to already-open terminals), then test:
ffmpeg -version

Now let me also fix the app.py to handle Windows long paths and give a clearer error message when FFmpeg is missing: