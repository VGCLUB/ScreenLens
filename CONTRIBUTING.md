# 参与贡献

欢迎提交问题、改进建议和 Pull Request。提交贡献即表示同意以项目的 GPL-3.0-or-later 许可证发布该贡献。

建议从独立分支修改，并说明问题触发条件、最终行为和验证方式。涉及时间轴、局部擦字、语音识别或字幕生命周期时，应提供可复现的合成测试。

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.venv\Scripts\python.exe -m pytest -q
```

完整视频自检需先准备 FFmpeg 与 base 模型，再运行 `qa/create_video_fixtures.py`。见 README。GitHub Actions 执行不依赖真实桌面、视频二进制或云端密钥的核心测试，缺少中文字体时跳过相应图形用例；完整图像、GUI 和导出验证在本地 Windows 环境执行。

问题报告可包含系统、版本、日志中已去除个人信息的错误片段，以及有权公开的合成样例。API Key、访问令牌、个人原视频、真实项目时间轴和账号配置不应提交至仓库。
