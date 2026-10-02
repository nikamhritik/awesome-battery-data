# 批量选中（1.1.0）

Android 和 Windows 都可从搜索结果中选择多张图片，显示已选数量，并全选、清空或退出选择。全选覆盖本次搜索的所有结果；Windows 翻页保留选择。重新搜索会清空选择，非法正则和空查询也会清空旧结果。

Android 点击 **批量选择** 或长按结果进入选择模式，点击图片选中或取消，再点击 **分享所选**。单张仍使用系统单图片分享，多张使用系统多图片分享；接收应用得到每个原图 URI 的临时读取权限。屏幕旋转和返回应用后重新检查媒体版本，保留仍有效的选择。进程重新启动时清空选择。

Windows 点击 **批量选择** 后，点击结果选中或取消；**复制所选到剪贴板** 提供按搜索结果顺序排列的原图文件列表。原始 GIF 等文件保留动画，接收软件需要支持图片文件粘贴。预览中的单张图片数据复制和绝对路径复制保持原有用法。

应用在后台检查所选图片是否仍可读、版本是否匹配。一项失效时，本次分享或复制失败；Windows 保留旧剪贴板，不复制剩余部分。选择逻辑只保存版本标识，不将全部原图读入内存。系统或接收软件可能限制一次接收的图片数，可以减少选择后再次发送。

## 自动验证

- Android 纯逻辑测试：80 项通过，包括新增的空选择、去重、按结果排序、版本变化、清空重选、9000 条结果和随机选择变化测试。
- 发布版 APK 构建、v2/v3 签名与合并权限检查通过；版本 `1.1.0`，versionCode `2`，与 1.0.0 使用相同签名证书。Android lint 无错误，11 项提示涉及固定依赖版本和界面文字国际化。
- 桌面逻辑与 Qt 界面测试：Linux 隔离 Python 3.12 环境，118 项通过。core/storage/images/selection 总语句覆盖率 97%，新增 selection 模块 100%。
- 桌面界面测试使用真实 Qt 点击，覆盖跨页选择、全选全部结果、退出后预览、原图哈希不变、多文件剪贴板、删除/变化/权限失败、重复点击、旧回调和关闭窗口。
- Android 多图片 intent 仪器测试覆盖所有 URI 的 ClipData、单张与多张 action、Parcel 往返和仅临时读权限。测试 APK 已编译；本次未连接设备执行这些测试。
- Linux offscreen 离线自验：20 项检查通过，退出码为 0；包含真实中文 OCR、预览、单图复制、多图文件列表、清空选择和原图哈希检查。自验结束后清除临时图片的剪贴板数据。

可重复运行：

```bash
./gradlew :core:test :app:lintRelease :app:assembleRelease :app:assembleDebugAndroidTest

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=desktop QT_QPA_PLATFORM=offscreen \
  python -m pytest desktop/tests -p no:cacheprovider \
  --cov=memeocr.core --cov=memeocr.storage --cov=memeocr.images --cov=memeocr.selection

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=desktop QT_QPA_PLATFORM=offscreen \
  python -m memeocr --self-test /tmp/memeocr-verification/source-selftest.json
```

Windows 原生构建使用 `scripts/build-windows.ps1`。冻结程序自验增加了两张原图的文件列表复制，并通过 Windows `CF_HDROP` 和 `DragQueryFileW` 读取实际剪贴板文件数量与路径。Linux offscreen 自验只验证 Qt 行为。

## 真实环境验收

Mate 60 Pro 和用户 Windows 电脑尚待测试；本次未进行 Android 模拟器验证。建议验收以下使用过程：

1. 搜索后选择 2–10 张图片，确认已选数量和逐张取消。Android 打开系统分享菜单，Windows 在常用聊天软件中粘贴。
2. Windows 切换结果页再返回，确认选择保留；在不同页选图，再使用全选和清空。
3. Android 旋转屏幕、取消分享或从接收应用返回，确认查询、选择模式和仍有效的选择保留。
4. 选择图片后重新搜索、输入非法正则，确认旧选择清空。选择后移动或修改一张测试图片，确认本次操作提示失效。
5. 检查单张预览及原有分享、复制图片、复制路径仍可使用；确认缓存升级后仍可继续搜索。

收到真实环境测试反馈后提交 PR；合并后创建 `v1.1.0` tag 和 release。

## 执行记录

只读平台调查：`ses_f02b2cd0cffem9upyHbfmW5I4r`。环境准备：`ses_f02ad4e67ffeXD2RaodoluMVU5`，提供独立临时 JDK 17、SDK 35、build-tools 34.0.0 和已校验的 Gradle 8.9。初版实现会话 `ses_f02ad4e6effe9xQlRbXUjPL3Mw` 的选择模型由主代理重写；停止已获服务端确认，主代理随后接管实现和验证。
