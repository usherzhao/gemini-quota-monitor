"""
Gemini Quota Monitor - Settings Window
Modern multi-tab preferences dialog with Google Official OAuth 2.0 Login, Token Import, Proxy, Appearance, and Diagnostics.
"""

import json
from pathlib import Path
import time
from typing import Optional
import webbrowser
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QGuiApplication, QIcon
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from config import ConfigManager, ProxyConfig
from core.oauth import GoogleOAuthClient, GoogleOAuthThread
from utils.autostart import is_autostart_enabled, set_autostart
from utils.logger import get_log_file_path, logger, open_log_file
from utils.win_api import apply_dwm_window_attributes

try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
    import requests as cffi_requests


class SettingsWindow(QDialog):
    """Modern dark-themed preferences dialog for Gemini Quota Monitor."""

    settings_saved = pyqtSignal()
    reset_dock_requested = pyqtSignal()

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.oauth_thread: Optional[GoogleOAuthThread] = None

        self.setWindowTitle("Gemini Quota Monitor - 偏好设置")
        self.setFixedSize(580, 640)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self._init_ui()
        self._load_values()
        apply_dwm_window_attributes(int(self.winId()), dark_mode=True, round_corners=True)

    def _init_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #0F172A;
                color: #F8FAFC;
                font-family: 'Segoe UI', 'Microsoft YaHei UI', sans-serif;
            }
            QTabWidget::pane {
                border: 1px solid #334155;
                border-radius: 8px;
                background-color: #1E293B;
                padding: 12px;
            }
            QTabBar::tab {
                background-color: #0F172A;
                color: #94A3B8;
                padding: 8px 16px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 4px;
                font-weight: 500;
                font-size: 12px;
            }
            QTabBar::tab:selected {
                background-color: #1E293B;
                color: #38BDF8;
                border-bottom: 2px solid #0EA5E9;
            }
            QLabel {
                color: #E2E8F0;
                font-size: 13px;
            }
            QLineEdit, QComboBox, QSpinBox {
                background-color: #0F172A;
                border: 1px solid #334155;
                border-radius: 6px;
                color: #F8FAFC;
                padding: 6px 10px;
                font-size: 12px;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
                border-color: #0EA5E9;
            }
            QGroupBox {
                border: 1px solid #334155;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 14px;
                font-weight: 600;
                color: #7DD3FC;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QRadioButton, QCheckBox {
                color: #E2E8F0;
                font-size: 13px;
                spacing: 6px;
            }
            QPushButton {
                background-color: #2563EB;
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                padding: 8px 14px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #1D4ED8;
            }
            QPushButton#BtnSecondary {
                background-color: #334155;
                color: #E2E8F0;
            }
            QPushButton#BtnSecondary:hover {
                background-color: #475569;
            }
            QPushButton#BtnDanger {
                background-color: #DC2626;
                color: #FFFFFF;
            }
            QPushButton#BtnDanger:hover {
                background-color: #B91C1C;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        self.tab_widget = QTabWidget()
        self.tab_widget.addTab(self._create_auth_tab(), "🔑 账号与授权")
        self.tab_widget.addTab(self._create_network_tab(), "🌐 网络与代理")
        self.tab_widget.addTab(self._create_display_tab(), "🖥️ 外观与任务栏")
        self.tab_widget.addTab(self._create_general_tab(), "⚙️ 常规与诊断")
        main_layout.addWidget(self.tab_widget)

        # Bottom Buttons
        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(0, 4, 0, 0)
        bottom_layout.setSpacing(8)

        btn_logs = QPushButton("📄 查看运行日志")
        btn_logs.setObjectName("BtnSecondary")
        btn_logs.clicked.connect(open_log_file)
        bottom_layout.addWidget(btn_logs)

        bottom_layout.addStretch()

        btn_save = QPushButton("💾 保存并应用")
        btn_save.clicked.connect(self._save_and_close)
        bottom_layout.addWidget(btn_save)

        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("BtnSecondary")
        btn_cancel.clicked.connect(self.close)
        bottom_layout.addWidget(btn_cancel)

        main_layout.addLayout(bottom_layout)

    def _create_auth_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        # 1. Google OAuth Card
        oauth_box = QGroupBox("Google 账号授权 (推荐 - 官方 OAuth 2.0)")
        oauth_layout = QVBoxLayout(oauth_box)
        oauth_layout.setSpacing(10)

        self.lbl_auth_status = QLabel("当前状态: 检测中...")
        self.lbl_auth_status.setStyleSheet("font-size: 13px; font-weight: 600;")
        oauth_layout.addWidget(self.lbl_auth_status)

        self.lbl_auth_email = QLabel("账号邮箱: 未登录")
        self.lbl_auth_email.setStyleSheet("color: #94A3B8; font-size: 12px;")
        oauth_layout.addWidget(self.lbl_auth_email)

        self.lbl_auth_project = QLabel("Cloud Code 项目: 默认 (bamboo-precept-lgxtn)")
        self.lbl_auth_project.setStyleSheet("color: #94A3B8; font-size: 12px;")
        oauth_layout.addWidget(self.lbl_auth_project)

        btn_row = QHBoxLayout()
        self.btn_login = QPushButton("🔑 浏览器一键登录 Google 账号")
        self.btn_login.clicked.connect(self._start_google_oauth)
        btn_row.addWidget(self.btn_login)

        self.btn_logout = QPushButton("退出登录")
        self.btn_logout.setObjectName("BtnDanger")
        self.btn_logout.clicked.connect(self._logout_account)
        btn_row.addWidget(self.btn_logout)

        oauth_layout.addLayout(btn_row)

        desc = QLabel("💡 点击登录后会自动打开系统默认浏览器，在 Google 官方页面完成登录授权后即可自动同步配额。")
        desc.setStyleSheet("color: #64748B; font-size: 11px;")
        desc.setWordWrap(True)
        oauth_layout.addWidget(desc)

        layout.addWidget(oauth_box)

        # 2. Manual / CLIProxyAPI Import Card
        manual_box = QGroupBox("手动凭据与导入 (高级)")
        manual_layout = QVBoxLayout(manual_box)
        manual_layout.setSpacing(8)

        row_refresh = QHBoxLayout()
        row_refresh.addWidget(QLabel("Refresh Token:"))
        self.txt_refresh_token = QLineEdit()
        self.txt_refresh_token.setPlaceholderText("可手动粘贴 Google Refresh Token...")
        self.txt_refresh_token.setEchoMode(QLineEdit.EchoMode.Password)
        row_refresh.addWidget(self.txt_refresh_token)
        manual_layout.addLayout(row_refresh)

        row_proj = QHBoxLayout()
        row_proj.addWidget(QLabel("Project ID:"))
        self.txt_project_id = QLineEdit()
        self.txt_project_id.setPlaceholderText("留空则自动检测或使用默认")
        row_proj.addWidget(self.txt_project_id)
        manual_layout.addLayout(row_proj)

        row_import = QHBoxLayout()
        btn_import_json = QPushButton("📂 导入 CLIProxyAPI / antigravity-*.json 凭据文件")
        btn_import_json.setObjectName("BtnSecondary")
        btn_import_json.clicked.connect(self._import_auth_json)
        row_import.addWidget(btn_import_json)
        manual_layout.addLayout(row_import)

        layout.addWidget(manual_box)
        layout.addStretch()
        return tab

    def _create_network_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        proxy_box = QGroupBox("网络代理设置 (用于国内顺畅直连 Google API)")
        p_layout = QVBoxLayout(proxy_box)
        p_layout.setSpacing(10)

        self.chk_proxy_enabled = QCheckBox("启用代理服务器")
        self.chk_proxy_enabled.toggled.connect(self._toggle_proxy_fields)
        p_layout.addWidget(self.chk_proxy_enabled)

        grid = QHBoxLayout()
        grid.addWidget(QLabel("协议:"))
        self.cbo_proxy_type = QComboBox()
        self.cbo_proxy_type.addItems(["SOCKS5", "HTTP"])
        grid.addWidget(self.cbo_proxy_type)

        grid.addWidget(QLabel("地址:"))
        self.txt_proxy_host = QLineEdit()
        self.txt_proxy_host.setText("127.0.0.1")
        grid.addWidget(self.txt_proxy_host)

        grid.addWidget(QLabel("端口:"))
        self.spn_proxy_port = QSpinBox()
        self.spn_proxy_port.setRange(1, 65535)
        self.spn_proxy_port.setValue(7890)
        grid.addWidget(self.spn_proxy_port)
        p_layout.addLayout(grid)

        p_test_row = QHBoxLayout()
        btn_test_proxy = QPushButton("⚡ 测试代理连接 Google")
        btn_test_proxy.setObjectName("BtnSecondary")
        btn_test_proxy.clicked.connect(self._test_proxy_connection)
        p_test_row.addWidget(btn_test_proxy)
        p_test_row.addStretch()
        p_layout.addLayout(p_test_row)

        layout.addWidget(proxy_box)
        layout.addStretch()
        return tab

    def _create_display_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        dock_box = QGroupBox("任务栏胶囊外观与定制")
        d_layout = QVBoxLayout(dock_box)
        d_layout.setSpacing(10)

        self.chk_show_dock = QCheckBox("显示任务栏小胶囊")
        d_layout.addWidget(self.chk_show_dock)

        self.chk_show_countdown = QCheckBox("显示配额重置倒计时 (例如: 5H: 70% 4h10m)")
        d_layout.addWidget(self.chk_show_countdown)

        self.chk_auto_collapse = QCheckBox("靠桌面边缘时自动贴边收起 (鼠标划过展开)")
        d_layout.addWidget(self.chk_auto_collapse)

        # Mode Selection
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("默认显示模式:"))
        self.rb_remaining = QRadioButton("显示剩余配额 % (例如: 剩余 85%)")
        self.rb_used = QRadioButton("显示已用配额 % (例如: 已用 15%)")
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.rb_remaining)
        self.mode_group.addButton(self.rb_used)
        mode_row.addWidget(self.rb_remaining)
        mode_row.addWidget(self.rb_used)
        mode_row.addStretch()
        d_layout.addLayout(mode_row)

        # Capsule Width Slider
        w_row = QHBoxLayout()
        w_row.addWidget(QLabel("胶囊基础宽度:"))
        self.slider_width = QSlider(Qt.Orientation.Horizontal)
        self.slider_width.setRange(70, 300)
        self.slider_width.setValue(86)
        self.lbl_width_val = QLabel("86 px")
        self.slider_width.valueChanged.connect(lambda v: self.lbl_width_val.setText(f"{v} px"))
        w_row.addWidget(self.slider_width)
        w_row.addWidget(self.lbl_width_val)
        d_layout.addLayout(w_row)

        # Opacity Slider
        op_row = QHBoxLayout()
        op_row.addWidget(QLabel("背景不透明度:"))
        self.slider_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_opacity.setRange(40, 100)
        self.slider_opacity.setValue(88)
        self.lbl_opacity_val = QLabel("88%")
        self.slider_opacity.valueChanged.connect(lambda v: self.lbl_opacity_val.setText(f"{v}%"))
        op_row.addWidget(self.slider_opacity)
        op_row.addWidget(self.lbl_opacity_val)
        d_layout.addLayout(op_row)

        btn_reset_dock = QPushButton("⚓ 重置胶囊位置到任务栏托盘旁")
        btn_reset_dock.setObjectName("BtnSecondary")
        btn_reset_dock.clicked.connect(self._reset_dock_pos)
        d_layout.addWidget(btn_reset_dock)

        layout.addWidget(dock_box)
        layout.addStretch()
        return tab

    def _create_general_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        gen_box = QGroupBox("常规设置")
        g_layout = QVBoxLayout(gen_box)
        g_layout.setSpacing(10)

        # Refresh Interval
        ref_row = QHBoxLayout()
        ref_row.addWidget(QLabel("配额自动刷新间隔:"))
        self.spn_refresh = QSpinBox()
        self.spn_refresh.setRange(15, 3600)
        self.spn_refresh.setValue(120)
        self.spn_refresh.setSuffix(" 秒")
        ref_row.addWidget(self.spn_refresh)
        ref_row.addStretch()
        g_layout.addLayout(ref_row)

        self.chk_autostart = QCheckBox("开机自动启动 Gemini Quota Monitor")
        g_layout.addWidget(self.chk_autostart)

        self.chk_notify_low = QCheckBox("配额不足预警提示 (低于阈值时)")
        g_layout.addWidget(self.chk_notify_low)

        thresh_row = QHBoxLayout()
        thresh_row.addWidget(QLabel("低配额预警阈值:"))
        self.spn_low_thresh = QSpinBox()
        self.spn_low_thresh.setRange(5, 50)
        self.spn_low_thresh.setValue(20)
        self.spn_low_thresh.setSuffix(" %")
        thresh_row.addWidget(self.spn_low_thresh)
        thresh_row.addStretch()
        g_layout.addLayout(thresh_row)

        self.chk_simulator = QCheckBox("启用离线模拟演示模式 (开发与界面预览用)")
        g_layout.addWidget(self.chk_simulator)

        layout.addWidget(gen_box)
        layout.addStretch()
        return tab

    def _load_values(self):
        cfg = self.config_manager.config

        # Auth
        email = cfg.gemini.account_email.strip()
        proj = cfg.gemini.project_id.strip() or "bamboo-precept-lgxtn"
        has_token = bool(cfg.gemini.refresh_token or cfg.gemini.access_token)

        if has_token:
            self.lbl_auth_status.setText("当前状态: ✅ 已授权登录")
            self.lbl_auth_status.setStyleSheet("color: #10B981; font-size: 13px; font-weight: 700;")
            self.lbl_auth_email.setText(f"账号邮箱: {email or '已授权'}")
            self.lbl_auth_project.setText(f"Cloud Code 项目: {proj}")
            self.btn_logout.setEnabled(True)
        else:
            self.lbl_auth_status.setText("当前状态: ⚠️ 未登录")
            self.lbl_auth_status.setStyleSheet("color: #F59E0B; font-size: 13px; font-weight: 700;")
            self.lbl_auth_email.setText("账号邮箱: 未登录")
            self.lbl_auth_project.setText("Cloud Code 项目: 未关联")
            self.btn_logout.setEnabled(False)

        self.txt_refresh_token.setText(cfg.gemini.refresh_token)
        self.txt_project_id.setText(cfg.gemini.project_id)

        # Proxy
        p_cfg = cfg.proxy
        self.chk_proxy_enabled.setChecked(p_cfg.enabled)
        self.cbo_proxy_type.setCurrentText(p_cfg.proxy_type.upper())
        self.txt_proxy_host.setText(p_cfg.host or "127.0.0.1")
        self.spn_proxy_port.setValue(p_cfg.port or 7890)
        self._toggle_proxy_fields(p_cfg.enabled)

        # Display
        d_cfg = cfg.display
        self.chk_show_dock.setChecked(d_cfg.show_taskbar_dock)
        self.chk_show_countdown.setChecked(d_cfg.dock_show_countdown)
        self.chk_auto_collapse.setChecked(d_cfg.dock_auto_collapse_edge)
        if d_cfg.usage_display_mode == "used":
            self.rb_used.setChecked(True)
        else:
            self.rb_remaining.setChecked(True)

        self.slider_width.setValue(d_cfg.dock_width)
        self.lbl_width_val.setText(f"{d_cfg.dock_width} px")
        op_pct = int(d_cfg.dock_opacity * 100)
        self.slider_opacity.setValue(op_pct)
        self.lbl_opacity_val.setText(f"{op_pct}%")

        # General
        g_cfg = cfg.general
        self.spn_refresh.setValue(g_cfg.refresh_interval_sec)
        self.chk_autostart.setChecked(is_autostart_enabled())
        self.chk_notify_low.setChecked(g_cfg.notify_low_quota)
        self.spn_low_thresh.setValue(g_cfg.low_quota_threshold)
        self.chk_simulator.setChecked(g_cfg.active_source == "simulator")

    def _toggle_proxy_fields(self, enabled: bool):
        self.cbo_proxy_type.setEnabled(enabled)
        self.txt_proxy_host.setEnabled(enabled)
        self.spn_proxy_port.setEnabled(enabled)

    def _start_google_oauth(self):
        proxy_url = self._get_current_proxy_url()
        self.btn_login.setEnabled(False)
        self.btn_login.setText("正在打开浏览器授权...")

        self.oauth_thread = GoogleOAuthThread(proxy_url=proxy_url)
        self.oauth_thread.auth_started.connect(self._on_oauth_started)
        self.oauth_thread.auth_success.connect(self._on_oauth_success)
        self.oauth_thread.auth_failed.connect(self._on_oauth_failed)
        self.oauth_thread.start()

    def _on_oauth_started(self, url: str):
        self.btn_login.setText("等待浏览器授权完成...")

    def _on_oauth_success(self, result: dict):
        self.btn_login.setEnabled(True)
        self.btn_login.setText("🔑 浏览器一键登录 Google 账号")

        cfg = self.config_manager.config.gemini
        cfg.access_token = result.get("access_token", "")
        cfg.refresh_token = result.get("refresh_token", "")
        cfg.token_expiry = result.get("token_expiry", "")
        cfg.account_email = result.get("account_email", "")
        self.config_manager.save()

        self._load_values()
        QMessageBox.information(self, "登录成功", f"成功关联 Google 账号: {cfg.account_email or '已授权'}！\n配额数据将立即开始同步。")
        self.settings_saved.emit()

    def _on_oauth_failed(self, error: str):
        self.btn_login.setEnabled(True)
        self.btn_login.setText("🔑 浏览器一键登录 Google 账号")
        QMessageBox.warning(self, "登录失败", f"Google 授权未完成: {error}")

    def _logout_account(self):
        reply = QMessageBox.question(self, "确认退出", "确定要清除当前的 Google 账号授权信息吗？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            cfg = self.config_manager.config.gemini
            cfg.access_token = ""
            cfg.refresh_token = ""
            cfg.token_expiry = ""
            cfg.account_email = ""
            self.config_manager.save()
            self._load_values()
            self.settings_saved.emit()

    def _import_auth_json(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择凭据文件", "", "JSON Files (*.json);;All Files (*.*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            refresh_token = data.get("refresh_token") or data.get("refreshToken") or ""
            access_token = data.get("access_token") or data.get("accessToken") or ""
            email = data.get("email") or data.get("account_email") or ""
            project_id = data.get("project_id") or data.get("projectId") or data.get("project") or ""

            if not refresh_token and not access_token:
                QMessageBox.warning(self, "导入失败", "所选 JSON 文件中未找到 refresh_token 或 access_token。")
                return

            cfg = self.config_manager.config.gemini
            if refresh_token:
                cfg.refresh_token = refresh_token
            if access_token:
                cfg.access_token = access_token
            if email:
                cfg.account_email = email
            if project_id:
                cfg.project_id = project_id

            self.config_manager.save()
            self._load_values()
            QMessageBox.information(self, "导入成功", f"成功导入凭据！\n账号: {cfg.account_email or '未知'}\n项目: {cfg.project_id or '默认'}")
            self.settings_saved.emit()
        except Exception as e:
            QMessageBox.critical(self, "解析错误", f"读取凭据文件异常: {e}")

    def _get_current_proxy_url(self) -> Optional[str]:
        if not self.chk_proxy_enabled.isChecked():
            return None
        ptype = self.cbo_proxy_type.currentText().lower()
        host = self.txt_proxy_host.text().strip()
        port = self.spn_proxy_port.value()
        scheme = "socks5h" if ptype == "socks5" else "http"
        return f"{scheme}://{host}:{port}"

    def _test_proxy_connection(self):
        proxy_url = self._get_current_proxy_url()
        if not proxy_url:
            QMessageBox.information(self, "提示", "请先勾选并配置代理服务器信息。")
            return

        try:
            proxies = {"http": proxy_url, "https": proxy_url}
            if HAS_CURL_CFFI:
                session = cffi_requests.Session(impersonate="chrome124")
                session.proxies = proxies
                resp = session.get("https://accounts.google.com", timeout=8)
            else:
                resp = cffi_requests.get("https://accounts.google.com", proxies=proxies, timeout=8)

            if resp.status_code in [200, 302, 301]:
                QMessageBox.information(self, "连接成功", f"代理连接正常！成功访问 Google 认证服务 (HTTP {resp.status_code})。")
            else:
                QMessageBox.warning(self, "连接异常", f"代理已连通但返回状态码: {resp.status_code}")
        except Exception as e:
            QMessageBox.critical(self, "连接失败", f"无法通过该代理访问 Google: {e}")

    def _reset_dock_pos(self):
        self.reset_dock_requested.emit()
        QMessageBox.information(self, "已重置", "任务栏胶囊位置已重置为任务栏系统托盘旁。")

    def _save_and_close(self):
        cfg = self.config_manager.config

        # Auth
        if self.txt_refresh_token.text().strip():
            cfg.gemini.refresh_token = self.txt_refresh_token.text().strip()
        cfg.gemini.project_id = self.txt_project_id.text().strip()

        # Proxy
        cfg.proxy.enabled = self.chk_proxy_enabled.isChecked()
        cfg.proxy.proxy_type = self.cbo_proxy_type.currentText().lower()
        cfg.proxy.host = self.txt_proxy_host.text().strip()
        cfg.proxy.port = self.spn_proxy_port.value()

        # Display
        cfg.display.show_taskbar_dock = self.chk_show_dock.isChecked()
        cfg.display.dock_show_countdown = self.chk_show_countdown.isChecked()
        cfg.display.dock_auto_collapse_edge = self.chk_auto_collapse.isChecked()
        cfg.display.usage_display_mode = "used" if self.rb_used.isChecked() else "remaining"
        cfg.display.dock_width = self.slider_width.value()
        cfg.display.dock_opacity = self.slider_opacity.value() / 100.0

        # General
        cfg.general.refresh_interval_sec = self.spn_refresh.value()
        cfg.general.notify_low_quota = self.chk_notify_low.isChecked()
        cfg.general.low_quota_threshold = self.spn_low_thresh.value()
        cfg.general.active_source = "simulator" if self.chk_simulator.isChecked() else "gemini_oauth"

        # Autostart
        set_autostart(self.chk_autostart.isChecked())

        self.config_manager.save()
        self.settings_saved.emit()
        self.close()
