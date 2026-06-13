# -*- coding: utf-8 -*-
"""
GMC 自动化任务 AI 诊断引擎

根据任务日志自动分析失败原因，输出结构化诊断报告和解决方案。
支持阶段三（GMC 注册）和阶段四（商品数据与验证）。
"""

import json as _json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

DIAGNOSIS_RULES = {
    "import": {
        "patterns": [
            "cloakbrowser 未安装",
            "No module named",
            "ImportError"
        ],
        "root_cause": "CloakBrowser 库未安装",
        "solution": "在服务器上运行: pip install cloakbrowser",
        "severity": "critical"
    },
    "profile_dir": {
        "patterns": [
            "Profile 目录不存在",
            "No such file"
        ],
        "root_cause": "CloakBrowser Profile 目录丢失",
        "solution": "重新创建品牌套件，或在 profiles/ 目录下手动创建 profile 并配置 config.json",
        "severity": "critical"
    },
    "config": {
        "patterns": [
            "proxy 配置无效",
            "fingerprint 参数缺失"
        ],
        "root_cause": "Profile config.json 配置不完整或有误",
        "solution": "检查 backend/profiles/NAME/config.json 中的 proxy 和 fingerprint 配置",
        "severity": "warning"
    },
    "launch": {
        "patterns": [
            "浏览器启动失败",
            "browser closed unexpectedly",
            "Target closed",
            "connect ECONNREFUSED",
            "Protocol error",
            "launch_persistent",
            "chromium",
            "executable"
        ],
        "root_cause": "CloakBrowser 浏览器启动失败",
        "solution": "清单排查:\\n  1. 检查服务器内存是否充足 (free -h, 至少 2GB)\\n  2. 检查 Chromium 依赖是否安装: apt list --installed | grep libnss3\\n  3. 检查 profile 目录权限: ls -la backend/profiles/NAME/\\n  4. 手动测试 CloakBrowser: python3 -c 'from cloakbrowser import launch'\\n  5. 检查代理连通性: curl -x PROXY_URL https://www.google.com -v\\n  6. 查看系统日志: journalctl -u kairui -n 100 | grep -i error",
        "severity": "critical"
    },
    "navigate": {
        "patterns": [
            "访问 GMC 首页超时",
            "navigation timeout",
            "net::ERR_",
            "DNS",
            "Timeout"
        ],
        "root_cause": "无法访问 Google Merchant Center",
        "solution": "网络排查:\\n  1. 检查代理是否存活: curl -x PROXY_URL https://merchants.google.com -v -m 30\\n  2. 如果代理使用 IP 白名单，确认服务器出口 IP 已加入白名单\\n  3. 检查 DNS 解析: nslookup merchants.google.com\\n  4. 尝试更换代理节点或国家\\n  5. 如果无代理: 检查服务器能否直接访问 Google",
        "severity": "critical"
    },
    "google_login": {
        "patterns": [
            "Google 自动登录失败",
            "password",
            "Password",
            "challenge",
            "captcha",
            "CAPTCHA",
            "verification",
            "2-Step Verification",
            "recovery",
            "unusual activity",
            "Enter the code",
            "2FA",
            "TOTP"
        ],
        "root_cause": "Google 登录被拦截或凭证过期",
        "solution": "按优先级依次尝试:\\n  1. TOTP 过期: 回到品牌套件编辑页，重新填入 Google TOTP Secret (Base32)\\n     - TOTP 每 30 秒刷新，确保系统时间同步\\n  2. CAPTCHA 人机验证: Google 检测到自动化行为，需要:\\n     - 更换代理 IP（使用住宅代理）\\n     - 设置 headless=False 并手动完成验证码\\n  3. 账户被锁定: 使用浏览器手动登录一次解除锁定\\n  4. 密码错误: 检查品牌套件中的 Google 账户密码\\n  5. 异常活动检测: 等待 24 小时后重试，或更换 Google 账户",
        "severity": "critical"
    },
    "state_detect": {
        "patterns": [
            "GMC 状态检测",
            "phase=",
            "wizard_step="
        ],
        "root_cause": "GMC 页面状态异常（非预期的注册流程页面）",
        "solution": "可能原因:\\n  - GMC 账户已存在但未完成设置 -> 系统会自动跳过注册进入设置\\n  - GMC 页面结构变更 -> 运行 GMC 侦查模式获取新页面结构\\n  - Google 语言/地区不匹配 -> 检查 profile 的语言和国家配置",
        "severity": "warning"
    },
    "business_info": {
        "patterns": [
            "未找到商家显示名输入框",
            "未找到国家选择器",
            "MISSING_INPUT",
            "fill_any",
            "fill_select"
        ],
        "root_cause": "GMC 注册向导表单元素定位失败 - Google 页面结构可能已更新",
        "solution": "页面结构变更应对:\\n  1. 运行「GMC 侦查模式」获取新页面的 DOM 结构和选择器\\n  2. 检查 mc_auto_register.py 中对应步骤的选择器列表是否需要更新\\n  3. 查看侦查导出的截图确认页面渲染状态\\n  4. 如果页面语言不是英文，检查 profile locale 配置",
        "severity": "error"
    },
    "business_address": {
        "patterns": [
            "找不到地址输入框",
            "未找到城市输入框",
            "未找到州/省输入框",
            "MISSING_INPUT"
        ],
        "root_cause": "地址表单元素定位失败 - 页面结构变更或国家特定字段",
        "solution": "同 business_info 步骤的排查流程.\\n  - 如果选择的国家需要额外字段（如增值税号），可能需要手动跳过\\n  - 检查 address/city/state/zip 是否在 selectors 列表中",
        "severity": "error"
    },
    "verify_website": {
        "patterns": [
            "未能提取验证码",
            "验证标签注入失败",
            "WordPress 注入异常",
            "Verification",
            "verify"
        ],
        "root_cause": "Google 网站验证环节失败",
        "solution": "网站验证排查:\\n  1. 验证码未提取到: 运行 GMC 侦查检查验证码是否在页面中\\n  2. WordPress 注入失败: 检查 WP 管理账户密码和站点 URL\\n  3. 手动注入: 登录 WordPress -> 使用 SEO 插件添加 meta 标签\\n  4. Google 未检测到标签: 等待 2-5 分钟后在 GMC 手动点击验证\\n  5. 备选方案: 使用 DNS TXT 记录验证 (通过 Cloudflare 添加)",
        "severity": "error"
    },
    "add_feed": {
        "patterns": [
            "Feed",
            "feed",
            "未找到 Feed URL 输入框"
        ],
        "root_cause": "产品 Feed URL 提交失败",
        "solution": "排查:\\n  1. 验证 Feed URL 可访问: curl -I FEED_URL\\n  2. 检查 Feed XML 格式 (应返回 200 + Content-Type: application/xml)\\n  3. 如果 Feed 为空: 确保 WordPress 中有产品数据\\n  4. 可手动在 GMC 后台 Data sources 页面添加 Feed",
        "severity": "warning"
    },
    "returns": {
        "patterns": [
            "return",
            "Return",
            "退货"
        ],
        "root_cause": "退货/退款政策设置失败",
        "solution": "  - 确保 WordPress 站点有 /return-policy/ 页面\\n  - 在 GMC 设置中手动填写退货政策 URL",
        "severity": "warning"
    },
    "terms": {
        "patterns": [
            "Terms of Service",
            "terms",
            "未找到同意复选框"
        ],
        "root_cause": "服务条款确认步骤失败",
        "solution": "手动在浏览器中完成此步骤，检查是否有复选框需要打勾",
        "severity": "warning"
    },
    "extract_code": {
        "patterns": [
            "无法从页面提取验证码",
            "verification_code",
            "google-site-verification"
        ],
        "root_cause": "Google Search Console 验证码提取失败",
        "solution": "手动获取验证码:\\n  1. 登录 Google Merchant Center\\n  2. 进入 Settings -> Business information -> Website\\n  3. 选择 HTML tag 验证方式\\n  4. 复制 meta 标签中的 content 值\\n  5. 在 WordPress 中使用 SEO 插件添加到 head 中\\n  6. 回到 GMC 点击 Verify",
        "severity": "error"
    },
    "inject_wp": {
        "patterns": [
            "WordPress 注入异常",
            "WordPress 注入警告",
            "WordPressAdminSession",
            "登录失败"
        ],
        "root_cause": "WordPress 管理后台操作失败",
        "solution": "WordPress 连接排查:\\n  1. 验证站点 URL 和 /wp-admin 是否可访问\\n  2. 检查 admin_name 和 admin_password 是否正确\\n  3. 如果 WP 使用 HTTP Basic Auth，确认安全凭证正确\\n  4. 手动登录 WP 后台，使用 SEO 插件添加验证标签\\n  5. 检查 WP 是否启用了额外的安全插件阻挡 API 登录",
        "severity": "error"
    },
    "verify_click": {
        "patterns": [
            "未找到可见的验证按钮",
            "验证按钮未找到",
            "验证尚未成功",
            "无法自动完成验证"
        ],
        "root_cause": "Google 端验证按钮交互失败",
        "solution": "手动完成验证:\\n  1. 确认验证标签已正确添加到 WP 站点的 head 中\\n  2. 在浏览器中手动打开 GMC 的网站验证页面\\n  3. 点击 Verify 按钮\\n  4. 如果仍失败，使用 DNS TXT 记录方式重新验证",
        "severity": "error"
    },
    "wp_connect": {
        "patterns": [
            "requests.exceptions",
            "ConnectionError",
            "Max retries exceeded",
            "HTTPSConnectionPool"
        ],
        "root_cause": "无法连接到 WordPress 站点",
        "solution": "连接排查:\\n  1. 验证站点 URL 是否正确 (注意 http vs https)\\n  2. 检查服务器是否能访问目标站点: curl -I WP_URL\\n  3. 如果是刚创建的站点，等待 1-2 分钟让 DNS/SSL 生效\\n  4. 检查 Cloudflare DNS 是否已正确指向服务器 IP",
        "severity": "critical"
    },
    "network": {
        "patterns": [
            "timeout",
            "Timeout",
            "timed out",
            "net::ERR_TIMED_OUT",
            "ERR_CONNECTION",
            "NameResolutionError",
            "DNS"
        ],
        "root_cause": "网络连接超时或 DNS 解析失败",
        "solution": "网络诊断:\\n  1. ping 8.8.8.8 (检查基础网络)\\n  2. 如果使用代理: curl -x PROXY_URL https://www.google.com -v -m 20\\n  3. 检查防火墙: ufw status\\n  4. 如果使用 HTTP 代理: 确认代理服务器支持 HTTP CONNECT 方法",
        "severity": "critical"
    },
    "proxy": {
        "patterns": [
            "proxy",
            "Proxy",
            "SOCKS",
            "407",
            "Proxy Authentication",
            "Tunnel",
            "connect through proxy",
            "ERR_TUNNEL_CONNECTION_FAILED",
            "ERR_PROXY_CONNECTION_FAILED"
        ],
        "root_cause": "代理服务器连接或认证失败",
        "solution": "代理排查:\\n  1. 验证代理 URL 格式: socks5://user:pass@host:port\\n  2. 测试代理连通: curl -x PROXY_URL https://www.google.com -v -m 20\\n  3. 检查代理 IP 是否被 Google 封禁\\n  4. 如果是住宅代理，确认 IP 白名单中包含当前服务器 IP\\n  5. 更换代理节点或供应商\\n  6. HTTP 代理认证问题: 系统自动切换到 Playwright CDP 代理模式",
        "severity": "critical"
    },
    "browser_crash": {
        "patterns": [
            "Target closed",
            "browser has been closed",
            "Page crashed",
            "Session closed",
            "closed unexpectedly"
        ],
        "root_cause": "浏览器进程异常崩溃",
        "solution": "浏览器崩溃排查:\\n  1. 检查服务器内存: free -h (Chromium 至少需要 1GB)\\n  2. 检查是否有其他 Chromium 进程: ps aux | grep chrom\\n  3. 清理所有僵尸进程: pkill -f chromium\\n  4. 检查磁盘空间: df -h\\n  5. 如果频繁崩溃，尝试重启服务器后重试\\n  6. 增加服务器内存或关闭其他服务释放资源",
        "severity": "critical"
    }
}


class TaskDiagnosis:
    """任务诊断结果。"""

    def __init__(self, task_id, task_type):
        self.task_id = task_id
        self.task_type = task_type
        self.steps = []
        self.errors = []
        self.warnings = []
        self.root_cause = ""
        self.solution = ""
        self.severity = "info"
        self.summary = ""
        self.generated_at = datetime.now().isoformat()

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "steps": self.steps,
            "errors": self.errors,
            "warnings": self.warnings,
            "root_cause": self.root_cause,
            "solution": self.solution,
            "severity": self.severity,
            "summary": self.summary,
            "generated_at": self.generated_at,
        }

    def to_json(self):
        return _json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def _match_rules(log_entries, task_type):
    """将日志条目与诊断规则匹配。"""
    matched = []
    seen = set()

    step_logs = {}
    for entry in log_entries:
        step = entry.get("step", "") or "__no_step__"
        if step not in step_logs:
            step_logs[step] = []
        step_logs[step].append(entry.get("message", "") or "")

    for step, messages in step_logs.items():
        combined = " ".join(messages)
        if step in DIAGNOSIS_RULES:
            rule = DIAGNOSIS_RULES[step]
            for pattern in rule.get("patterns", []):
                if pattern.lower() in combined.lower():
                    key = "step:" + step
                    if key not in seen:
                        seen.add(key)
                        evidence = [m for m in messages if pattern.lower() in m.lower()][:3]
                        matched.append({
                            "step": step,
                            "type": "step_error",
                            "root_cause": rule["root_cause"],
                            "solution": rule["solution"],
                            "severity": rule["severity"],
                            "matched_pattern": pattern,
                            "evidence": evidence,
                        })
                    break

    all_text = " ".join(e.get("message", "") for e in log_entries)
    for rule_name in ["network", "proxy", "browser_crash"]:
        if rule_name in DIAGNOSIS_RULES:
            rule = DIAGNOSIS_RULES[rule_name]
            for pattern in rule.get("patterns", []):
                if pattern.lower() in all_text.lower():
                    key = "global:" + rule_name
                    if key not in seen:
                        seen.add(key)
                        matched.append({
                            "step": "",
                            "type": "global_error",
                            "root_cause": rule["root_cause"],
                            "solution": rule["solution"],
                            "severity": rule["severity"],
                            "matched_pattern": pattern,
                            "evidence": ["(全局匹配)"],
                        })
                    break

    if not matched:
        errors = [e for e in log_entries if e.get("level") == "error"]
        if errors:
            last_msg = errors[-1].get("message", "")[:200] if errors else ""
            matched.append({
                "step": errors[0].get("step", "") if errors else "",
                "type": "unknown_error",
                "root_cause": "未知错误（诊断库中无匹配规则）",
                "solution": (
                    "请查看以下错误详情进行手动排查:\\n"
                    f"  最后错误: {last_msg}\\n"
                    "  建议:\\n"
                    "  1. 查看完整任务日志\\n"
                    "  2. 运行「GMC 侦查模式」检查页面状态\\n"
                    "  3. 将错误信息复制给技术支持"
                ),
                "severity": "error",
                "matched_pattern": "",
                "evidence": [e.get("message", "")[:200] for e in errors[-3:]],
            })

    return matched


def _analyze_step_timeline(log_entries):
    """分析步骤时间线。"""
    steps = []
    current_step = None

    for entry in log_entries:
        msg = entry.get("message", "") or ""
        step = entry.get("step", "") or ""
        level = entry.get("level", "info")

        is_new_step = level == "info" and ("步骤" in msg or "Step" in msg or step)
        if is_new_step and step:
            prev_name = current_step.get("name") if current_step else ""
            if step != prev_name:
                if current_step:
                    steps.append(current_step)
                current_step = {
                    "name": step,
                    "description": msg[:120],
                    "status": "in_progress",
                    "errors": [],
                    "warnings": [],
                }
        elif level == "error" and current_step:
            current_step["errors"].append(msg[:200])
            current_step["status"] = "failed"
        elif level == "warning" and current_step:
            current_step["warnings"].append(msg[:200])

        if current_step and ("成功" in msg or "success" in msg.lower() or "完成" in msg):
            if current_step["status"] != "failed":
                current_step["status"] = "completed"

    if current_step:
        steps.append(current_step)

    return steps


def diagnose_task(task_id, task_type, log_entries):
    """对任务日志进行 AI 诊断。"""
    report = TaskDiagnosis(task_id, task_type)

    report.steps = _analyze_step_timeline(log_entries)
    report.errors = [e for e in log_entries if e.get("level") == "error"]
    report.warnings = [e for e in log_entries if e.get("level") == "warning"]

    matches = _match_rules(log_entries, task_type)

    if matches:
        severity_order = {"critical": 4, "error": 3, "warning": 2, "info": 1}
        best = max(matches, key=lambda m: severity_order.get(m["severity"], 0))
        report.root_cause = best["root_cause"]
        report.solution = best["solution"]
        report.severity = best["severity"]

        all_solutions = []
        for m in matches:
            all_solutions.append("### " + m.get("step", "通用") + ":\n" + m["solution"])

        report.summary = (
            f"诊断任务: {task_id}\n"
            f"类型: {task_type}\n"
            f"严重程度: {report.severity.upper()}\n"
            f"根因: {report.root_cause}\n\n"
            f"解决方案:\n{report.solution}\n\n"
            f"步骤统计: {len(report.steps)} 个步骤, "
            f"{len(report.errors)} 个错误, {len(report.warnings)} 个警告"
        )
    else:
        failed_steps = [s for s in report.steps if s["status"] == "failed"]
        if failed_steps:
            failed_names = ", ".join(s["name"] for s in failed_steps)
            report.root_cause = "以下步骤失败: " + failed_names
            report.solution = (
                "自动诊断无法确定具体原因。请:\\n"
                "  1. 查看每个失败步骤的详细日志\\n"
                "  2. 运行 GMC 侦查模式检查页面变化\\n"
                "  3. 检查服务器网络和代理状态"
            )
            report.severity = "error"
            report.summary = (
                f"任务 {task_id} 失败\\n"
                f"失败步骤: {failed_names}\\n"
                f"共 {len(report.errors)} 个错误, {len(report.warnings)} 个警告"
            )
        else:
            report.root_cause = "未检测到明显错误"
            report.solution = "任务可能已成功完成，或日志不足。请检查任务状态。"
            report.severity = "info"
            report.summary = "任务 " + task_id + " 未检测到失败步骤"

    return report


def get_task_logs_as_list(task_id):
    """从数据库读取任务日志为字典列表。"""
    from models import get_db
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT log_index, timestamp, level, message, step "
            "FROM task_log_entries WHERE task_id = ? ORDER BY log_index",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_task_type(task_id):
    """从数据库读取任务类型。"""
    from models import get_db
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT task_type FROM task_sessions WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return row["task_type"] if row else "unknown"
    finally:
        conn.close()
