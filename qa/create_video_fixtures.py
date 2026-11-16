"""Synthetic OCR/soft-subtitle/voice fixtures. No personal videos or cloud services."""
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root.parent))
from screenlens.media import media_tool, CREATE_FLAGS

output = root / "video-fixtures"
output.mkdir(exist_ok=True)
speech_script = output / "synthesize.ps1"
speech_path = output / "speech.wav"
speech_script.write_text("""$ErrorActionPreference = 'Stop'
$voice = New-Object -ComObject SAPI.SpVoice
$voices = $voice.GetVoices()
for ($i = 0; $i -lt $voices.Count; $i++) {
    if ($voices.Item($i).GetDescription() -match 'Zira|David|English') { $voice.Voice = $voices.Item($i); break }
}
$stream = New-Object -ComObject SAPI.SpFileStream
$stream.Open((Join-Path $PSScriptRoot 'speech.wav'), 3, $false)
$voice.AudioOutputStream = $stream
$voice.Rate = -1
$voice.Speak('Hello. This is a subtitle translation test. Good morning. Thank you.') | Out-Null
$stream.Close()
""", encoding="utf-8-sig")
subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(speech_script)], check=True, creationflags=CREATE_FLAGS)

width, height, fps, seconds = 640, 360, 10, 8
font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 31)
for name, captions in (("hard", True), ("speech", False)):
    path = output / f"{name}.mp4"
    args = [media_tool("ffmpeg"), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "640x360", "-r", "10", "-i", "pipe:0",
            "-i", str(speech_path), "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)]
    process = subprocess.Popen(args, stdin=subprocess.PIPE, creationflags=CREATE_FLAGS)
    for index in range(fps*seconds):
        xx = np.linspace(25, 75, width)[None, :].repeat(height, axis=0)
        yy = np.linspace(20, 65, height)[:, None].repeat(width, axis=1)
        rgb = np.dstack((xx, yy, np.full((height,width), 90+index%10))).astype(np.uint8)
        image = Image.fromarray(rgb)
        draw = ImageDraw.Draw(image)
        text = "HELLO SCREENLENS" if .5 <= index/fps < 2.5 else "GOOD MORNING" if 3 <= index/fps < 6.5 else ""
        if captions and text:
            draw.text((width//2, 320), text, font=font, anchor="mm", fill="white", stroke_width=2, stroke_fill="black")
        process.stdin.write(image.tobytes())
    process.stdin.close()
    assert process.wait() == 0

soft_srt = output / "embedded.srt"
soft_srt.write_text("1\n00:00:00,500 --> 00:00:02,500\nHELLO SCREENLENS\n\n2\n00:00:03,000 --> 00:00:06,500\nGOOD MORNING\n", encoding="utf-8")
subprocess.run([media_tool("ffmpeg"), "-v", "error", "-y", "-i", str(output/"speech.mp4"), "-i", str(soft_srt),
                "-map", "0:v", "-map", "0:a", "-map", "1:s", "-c", "copy", "-c:s", "mov_text", str(output/"embedded.mp4")], check=True, creationflags=CREATE_FLAGS)
print(output)
