"""Prepare free video/ASR runtime assets; never requests a paid API."""
import shutil
from pathlib import Path

project = Path(__file__).resolve().parent
destination = project/"assets/ffmpeg"
destination.mkdir(parents=True,exist_ok=True)
for name in ("ffmpeg","ffprobe"):
    target = destination/(name+".exe")
    if not target.is_file():
        source = shutil.which(name)
        if not source:
            raise RuntimeError("Install FFmpeg on PATH or put ffmpeg.exe and ffprobe.exe in assets/ffmpeg")
        shutil.copy2(source,target)
model = project/"assets/asr-base"
if not (model/"model.bin").is_file():
    from faster_whisper.utils import download_model
    download_model("base",output_dir=str(model),use_auth_token=False)
print("Video tools and multilingual ASR base model are ready")
