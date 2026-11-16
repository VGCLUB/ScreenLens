import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase
from screenlens.config import Settings
from screenlens.ui import STYLE, SettingsDialog
from screenlens.video_ui import VideoDialog
from screenlens.media import probe, preview_frame

app = QApplication([])
for name in ("msyh.ttc","segoeui.ttf","YuGothM.ttc"):
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/"+name)
app.setStyle("Fusion")
app.setStyleSheet(STYLE)
root = Path(__file__).resolve().parent
settings = SettingsDialog(Settings())
settings.show()
app.processEvents()
assert settings.grab().save(str(root/"settings-ui-preview.png"))
settings.close()
dialog = VideoDialog(Settings())
dialog.info = probe(root/"video-fixtures/hard.mp4")
dialog.input.setText("合成测试视频.mp4")
dialog.output.setText("合成测试视频_translated.mp4")
dialog.canvas.set_frame(preview_frame(dialog.info,1.4))
dialog.status.setText("640 × 360 · 10 fps · 8 秒 · 音轨 有")
dialog.show()
app.processEvents()
assert dialog.grab().save(str(root/"video-ui-preview.png"))
dialog.reject()
