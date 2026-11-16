# Third-party components

ScreenLens is an independent application. It is not affiliated with DeepSeek,
RapidAI, PaddlePaddle, Microsoft, The Qt Company, or the other projects below.

Bundled dependencies retain their original copyrights and licenses. Copies of
the installed distributions' license files and metadata are in `licenses/`.
The application's source code is delivered separately in the project folder.

- **RapidOCR 3.10.0**: Copyright RapidOCR Authors, Apache-2.0.
  https://github.com/RapidAI/RapidOCR
- **PP-OCRv6 small detection / recognition and the legacy text-angle model**:
  upstream model weights copyright Baidu and/or the applicable PaddleOCR
  rights holders, Apache-2.0; converted and packaged by RapidOCR. Model files
  are redistributed unchanged from the installed RapidOCR wheel. Full model
  provenance, artifact hashes, conversion attribution and terms are preserved
  in `licenses/rapidocr-3.10.0/licenses/MODEL_LICENSES.md`.
- **ONNX Runtime**: Microsoft, MIT. https://github.com/microsoft/onnxruntime
- **Qt / PySide6 / Shiboken6**: The Qt Company and contributors. Community
  components are provided under their applicable LGPL/GPL/commercial terms.
  This application uses the dynamically linked community Qt libraries;
  those DLLs remain separately available in `_internal/PySide6`.
  License texts are supplied in `licenses/PySide6_Essentials-6.12.0` and
  `licenses/shiboken6-6.12.0`. Qt source and installation resources:
  https://download.qt.io/archive/qt/6.12/ and https://code.qt.io/
  Binding source: https://code.qt.io/pyside/pyside-setup.git/
- **Python**: Python Software Foundation License.
  https://www.python.org/downloads/source/
- **OpenCV, NumPy, Pillow, Shapely, pyclipper, MSS, HTTPX / HTTPCore,
  AnyIO, Requests, OmegaConf and their bundled dependencies** retain their
  respective licenses, distributed in `licenses/`.

Fonts used to render development previews are installed Windows fonts and are
not redistributed. OCR benchmark images and preview text are synthetic test
fixtures authored for this project. Preview translation and network timings
are simulated UI test values, not evidence of live API performance.

## ScreenLens 2.0 video components

- faster-whisper 1.2.1 (MIT), CTranslate2 4.9.0 (MIT), PyAV 19.0.2,
  Hugging Face Hub, tokenizers and dependencies retain their individual
  licenses, copied from installed distributions into `licenses/`.
- The Systran CTranslate2 conversion of Whisper base multilingual weights
  is identified as MIT by its model card. The model card and original
  Whisper MIT license are in `licenses/whisper-base-MIT/`.
  https://huggingface.co/Systran/faster-whisper-base
- Unmodified FFmpeg executable files from build
  `2025-06-02-git-688f3944ce-full_build-www.gyan.dev` are provided as separate
  subprocess tools under the build's GPL v3 terms. The installed complete
  license, build configuration and source link are preserved in
  `licenses/ffmpeg-2025-06-02/`.
  Corresponding source: https://github.com/FFmpeg/FFmpeg/commit/688f3944ce
  Distributor: https://www.gyan.dev/ffmpeg/builds/

Synthetic voice fixtures use Windows' installed SAPI voice for local QA.
Video/audio/OCR/ASR/encoding checks actually run. The QA translation service
returns simulated text locally and does not contact a cloud model.
