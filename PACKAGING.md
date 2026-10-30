# 打包说明

## 为什么 Windows 打不出 Mac 包？

PyInstaller **不能跨平台交叉编译**：

| 你在哪台机器上打包 | 能打出的产物 |
|---|---|
| Windows | `.exe` / 文件夹 |
| macOS | `.app` / `.dmg` |
| Linux | Linux 二进制 |

所以在这台 Windows 电脑上**无法直接生成**可在 Mac 上双击运行的安装包。  
要拿到 Mac 包，只能二选一：

1. **有 Mac**：在 Mac 上跑 `build_mac.sh`  
2. **没 Mac**：把项目推到 GitHub，用 Actions 云端 macOS 自动打包（下面）

---

## Windows（本机）

便携版：

```bat
build_windows.bat
```

产物：`dist\ChatReplyHelper\ChatReplyHelper.exe`

安装包（Inno Setup，需先有便携版）：

```bat
build_installer.bat
```

产物：`dist\installer\ChatReplyHelper-Setup-1.0.0.exe`  
（也会复制到桌面）

---

## macOS

### 方式 A：在 Mac 本机

```bash
chmod +x build_mac.sh
./build_mac.sh
```

### 方式 B：GitHub Actions（推荐，不用自己有 Mac）

1. 把 `wechat-reply-helper` 推到 GitHub 仓库  
2. 打开仓库 → **Actions** → **Build macOS** → **Run workflow**  
3. 跑完后在该次运行的 **Artifacts** 下载 `ChatReplyHelper-macOS.zip`

也可打 tag 触发：`git tag v1.0.0 && git push --tags`

说明：微信/QQ 窗口抓取与自动发送依赖 Windows API；Mac 版以截图 / 粘贴图片识别 + 复制回复为主。

---

## Android

当前是 CustomTkinter **桌面程序**，**打不出 APK**。要手机版需另做 PWA/原生，见此前说明。
