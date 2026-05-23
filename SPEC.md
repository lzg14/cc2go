# cc2go 版本管理规范 (SPEC)

> 当前版本：0.7.7 | 更新：2026-05-23
> 技术架构参见 [ARCHITECTURE.md](ARCHITECTURE.md)

---


## 版本号规则

遵循语义化版本 2.0.0：

```
MAJOR.MINOR.PATCH

0.5.0 → 主版本 0, 次版本 5, 修订号 0
```

| 升级 | 触发条件 | 示例 |
|------|---------|------|
| PATCH | Bug 修复、文案调整 | `0.6.0` → `0.6.1` |
| MINOR | 新功能、向后兼容 | `0.6.0` → `0.7.0` |
| MAJOR | 不兼容的 API 变更 | `0.6.0` → `1.0.0` |


## 升版步骤

### 1. 改版本号

```python
# src/router.py
VERSION = "x.y.z"   # 只改这一处，tray.py / build_release.py 自动引用
```

### 2. 生成 Release 包

```bash
python build_release.py
# 生成 cc2go-vx.y.z-windows.zip
```

### 3. 提交并打 Tag

```bash
git add src/router.py
git commit -m "release: vx.y.z - 描述"
git tag vx.y.z
git push && git push --tags
```

### 4. 上传到 GitHub Releases

将 `cc2go-vx.y.z-windows.zip` 作为 Release 附件上传。


## 版本历史

| 版本 | 日期 | 主要变更 |
|------|------|---------|
| 0.7.7 | 2026-05-23 | 审计修复 v7：路径穿越/H2-H4/M4 安全修复、get_base_dir 抽到 utils、测试 import 统一、maybe_archive 提取、新增 15 个测试、类型注解补全；总测试 127 个 |
| 0.7.5 | 2026-05-23 | 修复：CLAUDE_SETTINGS_PATH 中 `~` 未展开导致 sync_claude_settings 静默失败；selectModel 同时发送 router_port 确保端口同步 |
| 0.7.4 | 2026-05-22 | 修复：ADMIN_HTML 分离到 static/index.html（router.py 减少 ~660 行）、删除 save_stats() 冗余函数、streaming.py 添加上游断开异常处理；默认监听改为 127.0.0.1 |
| 0.7.3 | 2026-05-22 | 新增：PyPI 发布支持 (`pip install cc2go`)、`cc2go` CLI 命令、`src/__init__.py` 包结构；首次启动引导（API Key 未配置警告横幅）、新版本检测横幅（GitHub API）；默认端口改为 4001 |
| 0.5.0 | 2026-05-17 | 初始开发：格式转换、系统托盘、打包脚本、工具调用循环 |
| 0.5.0+ | 2026-05-17 | 新增：流式 SSE 转换（streaming.py）、MCP web_search 短路（mcp_bypass.py）、错误分类与自适应重试（error_handler.py）、自定义模型 display_name 支持、auto-ID 生成、output_config 修复、Cache-Control 缓存防过期、mmx Windows 路径修复、54 个单元测试 |
| 0.6.0 | 2026-05-17 | 模型 UI 重构（预置/自定义分区、编辑按钮）、托盘简化（仅管理页+退出）、thinking 块三路处理（透传/摘除/转换）、DeepSeek reasoning_content 修复（tool_calls 消息自动补空）、自定义模型按 id 路由匹配（避免预置模型名冲突）、错误归档增强、19 个格式转换单元测试 |
| 0.7.0 | 2026-05-21 | 修复 DeepSeek 400 错误（reasoning_content 缺失）、MiniMax SSE 流式响应问题（透传路径强制 stream=false）、启动时自动杀旧进程防端口冲突、诊断日志（DEBUG 级别）、stop/start 脚本改进 |
| 0.7.1 | 2026-05-21 | 新增：Claude Code 配置自动备份（首次修改前备份，仅一次）、Web UI 一键恢复原始配置 |
| 0.7.2 | 2026-05-22 | 新增：工具名 sanitize（/ : 空格等特殊字符→_，防上游 400）、Schema 清理（递归移除 $schema / additionalProperties: false，提高兼容性）；增强：README Badges 和结构优化 |


## 提交规范

```
feat: 描述     # 新功能
fix: 描述      # Bug 修复
docs: 描述     # 文档
refactor: 描述 # 重构
release: 描述  # 版本发布
chore: 描述    # 杂项
```