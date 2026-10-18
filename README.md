# DownloadsCleaner · 下载目录空间清理

MoviePilot V3 插件：**磁盘可用空间低于阈值时，自动清理下载目录中已入库、做种超时的下载任务**。

## 功能特性

- **空间触发**：监控盘可用空间低于阈值（默认 300 GiB）开始清理，删到目标线（默认 350 GiB）即停
- **目录白名单**：仅清理指定目录子树内的任务（默认 `E:/Downloads`），天然排除 SSD / 保种专区
- **多重防护**：
  - 保护标签命中即排除（默认 `保种`、`H&R`）
  - 要求媒体已入库（MP 整理记录成功或额外白名单），未入库不删
  - 完成保护天数（默认 7 天），防止刚入库还在不稳定窗口期被删
  - 分享率门槛 / 小文件保护 / 路径黑名单可选
- **候选排序**：按做种时长从长到短清理，单轮数量可设上限
- **两阶段删除（可选）**：第一轮只打「待清理」标签，下一轮才真删，留出人工干预窗口
- **自动识别 MP 设置**：下载器（qBittorrent / Transmission）、通知渠道均从 MoviePilot 系统设置自动读取，无需填写地址密码
- **演练模式**：只记录不删除，先观察日志确认候选符合预期再切正式
- **同步清理 MP 记录**：删除任务同时清理整理记录与下载记录，避免幽灵记录
- **空目录回收（可选）**：删除后清理白名单目录子树下的空目录
- **详情页**：累计/本轮删除统计、磁盘状态、当前候选清单、最近删除记录

## 安装

1. 将本仓库加入 MoviePilot 插件市场（`PLUGIN_MARKET` 环境变量，多个仓库逗号分隔）
2. 在 WebUI 插件市场搜索「下载目录空间清理」并安装
3. 进入插件配置，调整阈值/目录/标签等参数，保存

## 配置说明

| 配置 | 默认 | 说明 |
|------|------|------|
| enabled | 关 | 启用插件 |
| dry_run | 开 | 演练模式，只记录不删除（**首次使用建议先演练**） |
| onlyonce | 关 | 立即运行一次 |
| cron | `0 */6 * * *` | 检查周期 |
| monitor_path | `E:/` | 监控盘目录 |
| threshold_free_gb | 300 | 触发阈值（可用空间 GiB） |
| target_free_gb | 350 | 停止线（清理到该可用空间） |
| downloaders | 空=全部 | 下载器多选（自动识别 MP 配置） |
| clean_dirs | `E:/Downloads` | 清理目录白名单 |
| block_keywords | 空 | 路径/名称黑名单 |
| min_seeding_days | 15 | 最小做种天数 |
| protect_tags | `保种`/`H&R` | 保护标签 |
| protect_recent_days | 7 | 完成保护天数 |
| min_ratio | 0 | 分享率门槛（0=关） |
| min_size_gb | 0 | 小文件保护（0=关） |
| require_library | 开 | 要求已入库 |
| verified_extra | 空 | 额外入库白名单种子 Hash |
| delete_files | 开 | 同时删除本地文件 |
| delete_history | 开 | 同步清理 MP 历史记录 |
| clean_empty_dirs | 关 | 空目录回收 |
| tag_then_delete | 关 | 两阶段删除 |
| max_delete_per_run | 30 | 单轮最多删除任务数 |

## 注意事项

- qBittorrent 删除任务并删文件为**永久删除、不进回收站**，请谨慎开启并先演练
- 「已整理」标签不等于保证入库，删除前以整理记录（status=成功且目标在媒体库路径）为准
- 停止线为软目标：候选不足时可能停在线下

## 技术说明

- 插件仅操作下载器与本地磁盘，不访问 115 等网盘
- qB 连接信息全部来自 MoviePilot 下载器配置，插件不存储任何凭据
- V3 SDK：`app.sdk.plugin._PluginBase` / `app.sdk.services.DownloaderHelper` / `app.db.oper.transferhistory`

## License

MIT
