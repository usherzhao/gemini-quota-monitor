# Gemini Quota Monitor (Gemini 5小时用量与周配额桌面监控工具)

专为 Windows 10 / Windows 11 量身定制的 Google Gemini (Cloud Code / Antigravity) 配额实时监控小工具。  
采用 TrafficMonitor 风格双行微圆角磁贴卡片常驻桌面或吸附任务栏，支持靠边自动收起与鼠标悬停展开，并配备 Fluent 风格毛玻璃详情面板与系统托盘指示。

---

## ✨ 核心特性

- 📊 **双周期实时监控与智能倒计时**：
  - **5 小时滚动配额（5-Hour Window）**：实时追踪 5 小时滚动请求用量与重置倒计时（例如 `5H: 70%`）。
  - **每周总配额（Weekly Quota）**：实时展示每周配额池剩余/使用比例与周重置时间（例如 `7D: 86%`）。
  - **双视角一键切换**：支持“**显示剩余量 %**”与“**显示已用量 %**”，悬浮窗、系统托盘与详情面板均可一键切换。
- 🖥️ **TrafficMonitor 紧凑双行磁贴布局**：
  - 经典紧凑双行排版（默认高 36px，宽度自适应包裹约 86px~110px），完美融入桌面任务栏。
  - 第一行显示 `5H:` 滚动配额（暖橙色 `#F59E0B`，高危红警 `#EF4444`）。
  - 第二行显示 `7D:` 每周配额（翡翠绿 `#10B981`）或重置倒计时。
  - 左侧微型状态指示灯，随配额健康度变色，鼠标悬停带有呼吸发光光晕。
- 🧲 **靠桌面边缘自动收起 & 鼠标移动展开 (Edge Dock & Auto-Collapse)**：
  - **智能吸附**：鼠标拖拽悬浮窗贴近屏幕左边缘、右边缘或顶部边缘时自动吸附。
  - **微型 LED 梯级指示条**：光标移开 350ms 后，自动收缩为宽 20px、高 52px 的微型立柱条，内置两组 8 级微型水平刻度光栅，从下往上实时点亮对应比例。
  - **悬停瞬时展开**：鼠标划过立柱无需点击即向屏幕内侧展开为完整数字卡片，移出后防抖自动收起；拖离边缘放回桌面中央则解除收起状态。
- ⚡ **Antigravity 本地直连 & 云端 OAuth 双通道**：
  - **本地极速直连**：自动探测本机正在运行的 Antigravity 本地语言服务（`language_server.exe`），通过环回 Connect-RPC 免代理毫秒级获取 100% 真实的 Models & Usage 配额。
  - **Google 官方 OAuth 2.0 授权**：支持浏览器一键登录官方 Google Cloud Code，自动拉起浏览器完成授权，支持 Token 静默自动刷新。
  - **高级凭据导入**：支持导入已有的 Refresh Token 或 `antigravity-*.json` 凭据文件。
- 📌 **Windows 绝对置顶保活与智能防遮挡**：
  - 基于高效的 200ms 原生 Win32 心跳保活（`WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE`），保证在全屏浏览器、代码编辑器切换时不被任何程序覆盖，且不抢占键盘输入焦点。
  - **智能防遮挡右键菜单**：右键点击悬浮窗时，菜单智能向上方屏幕弹出（预留安全边距），弹出期间自动挂起置顶定时器，保证菜单 100% 完整可见。
- 🌐 **网络与代理支持**：
  - 内置 SOCKS5 / HTTP 本地代理配置（如 `127.0.0.1:7890`），国内直连 Google API 顺畅无阻。
- 📦 **独立单文件发布**：
  - 通过 PyInstaller 打包为纯单文件 `GeminiQuotaMonitor.exe`，内置原生启动闪屏（Splash Screen），无需配合 `_internal` 依赖文件夹，直接发送给朋友单独双击即可运行。

---

## 🚀 快速使用

### 1. 运行方式

- **源码运行**：
  ```bash
  # 安装依赖项
  pip install -r requirements.txt

  # 运行主程序
  python main.py
  ```

- **构建单文件 EXE**：
  ```bash
  python build.py
  ```
  构建完成后将在 `dist/GeminiQuotaMonitor.exe` 生成免安装独立单文件程序。

### 2. 账号授权

1. 启动程序后，若尚未检测到有效授权，会自动打开“偏好设置”窗口（亦可通过右键悬浮窗或托盘图标进入设置）。
2. 在“**网络与代理**”选项卡中配置您的科学上网代理（若在国内网络环境）并测试连通性。
3. 在“**账号与凭据**”选项卡中点击“**🔑 浏览器一键登录 Google 账号**”。
4. 在弹出的系统浏览器页面中允许 Google 授权，本地服务自动捕获 Token，监控即可正常运行。

---

## 📂 项目结构

```
gemini-quota-monitor/
├── assets/                  # 图标与启动闪屏图片
│   ├── icon.ico
│   ├── icon.png
│   └── splash.png
├── core/
│   ├── fetchers/
│   │   └── gemini.py        # Antigravity 本地极速直连与 Cloud Code PA 解析
│   ├── engine.py            # 后台轮询与状态调度引擎
│   ├── models.py            # 数据模型 (QuotaItem, QuotaSnapshot)
│   └── oauth.py             # Google OAuth 2.0 浏览器登录与回调服务
├── ui/
│   ├── flyout_window.py     # Fluent 暗色详情弹出卡片
│   ├── settings_window.py   # 设置与登录对话框
│   ├── taskbar_dock.py      # TrafficMonitor 双行卡片与边缘收起立柱
│   └── tray_manager.py      # 系统托盘图标与右键菜单
├── utils/
│   ├── autostart.py         # Windows 开机自启注册表管理
│   ├── logger.py            # 日志管理与查看器
│   └── win_api.py           # 任务栏定位与 DWM 属性交互
├── build.py                 # PyInstaller 单文件打包脚本
├── config.py                # 配置文件持久化管理
├── main.py                  # 程序主入口与单实例互斥锁
└── requirements.txt         # 依赖项清单
```

---

## 📄 License

MIT License.
