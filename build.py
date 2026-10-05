"""Build SEVBY HQ into a single app/executable for the OS you are on.

    pip install -r requirements.txt pyinstaller
    python build.py

Put an ffmpeg binary next to this file first (ffmpeg.exe on Windows, ffmpeg on Linux/macOS),
or let the build pick up the one on your PATH.
"""
import os
import shutil
import subprocess
import sys

sep = os.pathsep  # ';' on Windows, ':' elsewhere
exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
ffmpeg = exe if os.path.isfile(exe) else shutil.which("ffmpeg")
if not ffmpeg:
    sys.exit(f"ffmpeg not found. Put {exe} next to build.py or install ffmpeg first.")

cmd = [
    sys.executable, "-m", "PyInstaller", "--clean", "--noconsole", "--onefile",
    "--name", "SEVBY-HQ",
    "--collect-all", "yt_dlp", "--collect-all", "customtkinter", "--collect-all", "tkinterdnd2",
    "--add-binary", f"{ffmpeg}{sep}.",
]
# Icons are optional: bundle whichever ones are present next to this file.
for f in ("sevbyicon.ico", "sevby_logo_512.png"):
    if os.path.isfile(f):
        cmd += ["--add-data", f"{f}{sep}."]
    else:
        print(f"Note: {f} not found - building without it.")
if os.name == "nt" and os.path.isfile("sevbyicon.ico"):
    cmd += ["--icon", "sevbyicon.ico"]
cmd.append("sevby_app.py")
print(" ".join(cmd))
code = subprocess.call(cmd)
out = os.path.join("dist", "SEVBY-HQ.exe" if os.name == "nt" else "SEVBY-HQ")
if code == 0 and os.path.exists(out):
    print(f"\nDONE: {os.path.abspath(out)}")
else:
    print("\nBUILD FAILED - scroll up to the first line that says ERROR.")
sys.exit(code)
