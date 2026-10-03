# Windows 1.0.0

本文保留 1.0.0 移植的构建与验证记录。1.1.0 新增的批量选中功能和验收范围见 [批量选中说明](bulk-selection.md)。

Windows 版保留 Android 1.0.0 的分批识别、缓存续做、失败重试、普通文字和正则搜索。文件夹对应相册，原图只读。预览中的操作改为复制图片到系统剪贴板和复制图片的绝对文件路径。

发布 release 或移植已有功能不提升版本号。本次 Windows 版本沿用 Android 的 1.0.0；新增功能时再按项目约定递增版本。

## 运行与数据

- 系统：Windows 10/11 x64；无需 Python、单独下载 OCR 模型或管理员权限。
- 程序：`WhereIsMyMeme-1.0.0-windows-x64.exe`，便携单文件，运行库启动时解压到系统临时目录。
- 数据库：`%LOCALAPPDATA%\WhereIsMyMeme\cache.sqlite3`，记录目录、递归设置、批量数量和识别结果。删除该目录会清除缓存，原图保留。
- 一次只允许一个应用进程使用缓存。每张保存后更新进度，关闭时等待当前任务结束。
- 支持 JPEG、PNG、WebP、GIF、BMP、TIFF；动图和多页图片识别首帧，EXIF 旋转会应用。单图上限为 3200 万像素，OCR 输入最大边为 2048 像素。
- 图片复制保留解码后的完整尺寸；动图图片数据为首帧。复制路径可用于找到原始动图。

## 构建

```powershell
./scripts/build-windows.ps1 -OutputDir "$env:TEMP/wheres-my-meme-windows"
```

Python 3.12 x64，全部 Python 依赖固定在 `desktop/requirements.txt`。Qt/PySide6 6.8.3 提供界面，RapidOCR 1.4.4 和 ONNX Runtime 1.20.1 运行中文 PP-OCRv4 模型，Google RE2 用于正则，SQLite 保存缓存，PyInstaller 6.12.0 打包。

`OutputDir` 必须位于源码之外，符号链接和 Windows junction 会按实际路径检查。脚本将虚拟环境、pip 缓存、PyInstaller work/spec、EXE 和报告放入该目录。模型随固定版本 wheel 提供；生成的 `build-info.json` 记录依赖版本与三个模型的 SHA-256，第三方许可文本随 EXE 提供。

## 已执行的源码验证

2026-10-02，在隔离 Python 3.12 环境运行 103 项测试，全部通过，core/storage/images 的语句覆盖率为 96%。包含 9000 张的九批调度、已完成和空文字跳过、版本变化、失败重试、取消保存当前图片、缓存重开、跨目录搜索、普通文字与 RE2、图片解码、数据库并发、快速翻页和 Qt 界面退出。

源码自验使用合成中文图片和空白图片，识别出“猫猫今天开心”等文字，验证缓存重开与跳过、搜索、缩略图、预览、图片及路径复制、线程退出和原图哈希不变。Linux 端的 Qt 自验使用 offscreen 插件。9000 条测试验证调度和缓存逻辑，未测量 9000 张真实图片的 OCR 耗时或准确率。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=desktop QT_QPA_PLATFORM=offscreen \
  COVERAGE_FILE=/tmp/memeocr-verification/.coverage \
  python -m pytest desktop/tests -p no:cacheprovider \
  --cov=memeocr.core --cov=memeocr.storage --cov=memeocr.images

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=desktop QT_QPA_PLATFORM=offscreen \
  python -m memeocr --self-test /tmp/memeocr-verification/source-selftest.json
```

## Windows 可执行文件验证

GitHub Actions 使用 `.github/workflows/windows.yml` 和上述 PowerShell 脚本。冻结程序自验要求使用 `windows` 平台插件，并检查 Windows 剪贴板的原生位图和 Unicode 文本格式；offscreen 验证不会被当作 Windows 系统剪贴板验证。

2026-10-02，[Windows 构建 36978937846](https://github.com/rb-tyz/wheres-my-meme/actions/runs/36978937846) 验证提交 `9d8df7549be7937568d20e09c08afc7af95a66e7`。99 项测试通过，4 项 POSIX 专用权限测试跳过；冻结程序使用 Windows 平台插件，19 项自验全部通过，程序内版本为 `1.0.0`。自验覆盖离线中文 OCR、缓存、搜索、预览、Windows 剪贴板位图与路径文本、原图哈希不变和阻断网络后的运行。搜索与预览截图已检查。

`WhereIsMyMeme-1.0.0-windows-x64.exe` 为 144,661,004 字节，SHA-256 为 `3e2e3d6c46af80f00bd274d0dac7f2d6bb7ec4e5a723e2906dc8641c57c7d782`。下载包与 GitHub 记录的摘要一致，EXE 与随附 `SHA256SUMS` 一致。内嵌构建信息的版本、三个 OCR 模型的哈希、Windows x64 格式、运行库和许可文件均已核对。

Windows 代码通过 PR 合并到 `main` 后，将 EXE 和 `SHA256SUMS-windows-x64` 添加到现有的 [v1.0.0 release](https://github.com/rb-tyz/wheres-my-meme/releases/tag/v1.0.0)。已有 APK 和它的 `SHA256SUMS` 保留，应用版本仍为 1.0.0。用户自己的 Windows 电脑和常用聊天软件需要本地测试。

## 测试与协作范围

纯逻辑测试行数超过 core/storage/images/ocr 的源码行数，覆盖边界和失败路径；Qt 控件布局、平台入口及构建脚本通过界面和冻结程序自验验证。POSIX chmod 权限测试只适用于 Linux，Windows 使用模拟权限丢失验证停止与保存逻辑。

缓存及测试的初版由独立工作树代理 `ses_f04cdfc20ffePE4G1Oft5rrknc` 提供，主代理完成接口修正、界面、打包与交付验证。打包只读审查会话为 `ses_f04d53cf3ffe4ll7AUu8lOHcFq`。界面与原生依赖审查会话 `ses_f04bf81c5ffeUBFQkzbwjdxJJz` 找出了翻页时的事件队列竞态，已修复并增加回归测试。
