# 视译 2.0 验证记录

日期：2026-11-16，Windows 11 x64。

- 38 项测试：DPAPI、URL、SSE、取消、缓存、批量字幕 ID、字形修补局部性、透明背景、字体适配、漏检与最短阅读、SRT、文件保护、GUI 退出。
- 原生字幕自检只显示和捕获本程序的合成窗口，不注入鼠标键盘。覆盖层可见时 MSS 与覆盖前平均像素差 0，OCR 仍识别出 SUBTITLE SOURCE 123，物理位置、鼠标穿透、不抢焦点标志正确。新版预览使用实际局部字形修补。
- 视频自检使用 640×360、10 fps、8 秒合成画面及 Windows 本地 TTS，分别实际运行内嵌 mov_text、画面 OCR、无字幕语音回退，均输出 MP4 并检查音轨、分辨率和时长。硬字幕时间边界误差不超过 0.25 秒，字幕区外平均像素差低于 5（含编码误差）。
- OCR、语音模型、提取、渲染、编码与音轨均真实运行；译文由本机模拟 HTTP/SSE 服务返回。没有用户真实 Key，不代表真实云端翻译质量和网络延迟。
- video-translated-preview.png 是实际输出抽帧。界面预览使用合成素材与空 Key，其他历史 UI 延迟数字属于模拟值。
- 源码/发行包结果分别见 video-self-test.json、packaged-video-self-test.json、native-overlay-self-test.json、packaged-native-overlay-self-test.json、packaged-self-test.json；测试断言见 test-results.xml。
- 原生检查仅使用本程序的合成窗口和视频，不注入桌面输入；真实场景的识别、字体匹配与修补效果取决于输入素材。
