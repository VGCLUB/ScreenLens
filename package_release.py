"""Package an already built and self-tested application; never includes user settings."""
import hashlib
import json
import shutil
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from screenlens import __version__

project = Path(__file__).resolve().parent
bundle = project / "dist" / __version__ / "ScreenLens"
report = json.loads((project / "qa/packaged-self-test.json").read_text(encoding="utf-8"))
assert report["ok"] is True, "Run build.ps1 and fix the packaged self-test first"
video_report = json.loads((project/"qa/packaged-video-self-test.json").read_text(encoding="utf-8"))
assert video_report["ok"] is True, "Verify the packaged video processing before release"
assert (bundle / "ScreenLens.exe").is_file()
release = project / "release"
release.mkdir(exist_ok=True)
# Local QA output can contain user paths. Source archives use committed files only.
subprocess.run(["git", "diff", "--exit-code", "HEAD", "--quiet"], cwd=project, check=True)
untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], cwd=project)
assert not untracked.strip(), "Commit reviewed source before packaging"
for name in ("使用说明.txt", "README.md", "THIRD_PARTY_NOTICES.md"):
    shutil.copy2(project / name, bundle / name)
shutil.copy2(project / "qa/VALIDATION.md", bundle / "验证记录.md")

archives = []
portable = release / f"ScreenLens-{__version__}-Windows-x64.zip"
with zipfile.ZipFile(portable, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for path in sorted(bundle.rglob("*")):
        if path.is_file():
            archive.write(path, path.relative_to(bundle.parent))
archives.append(portable)

source = release / f"ScreenLens-{__version__}-source.zip"
# Release only reviewed, committed source, including LICENSE and build files.
# Local QA output may contain absolute user paths and must never be published.
subprocess.run(["git", "archive", "--format=zip", f"--prefix=ScreenLens-{__version__}/",
                "--output", str(source), "HEAD"], cwd=project, check=True)
archives.append(source)

test_suites = ET.parse(project / "qa/test-results.xml").getroot().findall("testsuite")
assert all(int(s.get("failures", 0)) == 0 and int(s.get("errors", 0)) == 0 for s in test_suites)
passed_count = sum(int(s.get("tests", 0)) - int(s.get("skipped", 0)) for s in test_suites)
native_report_path = project / "qa/packaged-native-overlay-self-test.json"
native_report = json.loads(native_report_path.read_text(encoding="utf-8")) if native_report_path.exists() else None
assert native_report and native_report["ok"], "Verify the packaged native overlay before release"
manifest = {"version": __version__, "automatic_tests_passed": passed_count, "packaged_self_test": report,
            "native_overlay_test": native_report,
            "video_self_test": video_report,
            "cloud_api_tested": False, "artifacts": []}
for path in archives:
    with path.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    manifest["artifacts"].append({"file": path.name, "size_bytes": path.stat().st_size, "sha256": digest})
(release / "release-info.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(manifest, ensure_ascii=False, indent=2))
