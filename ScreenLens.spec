from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_dynamic_libs

datas = collect_data_files('rapidocr')
datas += collect_data_files('onnxruntime')
datas += collect_data_files('faster_whisper')
datas += [('assets/ffmpeg', 'assets/ffmpeg')]
for path in Path('assets/asr-base').glob('*'):
    if path.is_file():
        datas.append((str(path),'assets/asr-base'))
hidden = collect_submodules('rapidocr') + collect_submodules('faster_whisper')
extra_binaries = collect_dynamic_libs('ctranslate2')
a = Analysis(['run.py'], pathex=[SPECPATH], binaries=extra_binaries, datas=datas,
             hiddenimports=hidden, hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
                       'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtPdf',
                       'PySide6.QtMultimedia', 'matplotlib', 'pytest', 'tkinter'])
# Qt 6.12 uses Windows' built-in, unversioned ICU API. Build-host PATH may
# contain Poppler's icuuc.dll (with version-suffixed exports), which must not
# shadow the Windows system library in the portable application.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() != 'icuuc.dll'
              and not Path(entry[0]).name.lower().startswith('icudt')]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='ScreenLens',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False, icon='assets/app.ico')
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='ScreenLens')
