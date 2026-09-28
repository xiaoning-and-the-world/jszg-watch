# 上传到 GitHub 的清单与步骤

把这一页照做一遍就能跑起来。**全程只需要浏览器，不用装 Git、不用命令行。**

---

## 一、结论：定时工作流还需要吗？需要

GitHub 上没有一个叫「定时任务」的按钮。**定时这件事本身必须由工作流文件描述**，
那个 `cron: "0 1 * * *"` 就写在工作流里。所以：

| 问题 | 答案 |
| --- | --- |
| 定时的工作流文件需要吗？ | **需要**，它是定时的唯一载体：`.github/workflows/watch.yml` |
| 放上去之后还要用命令行吗？ | **不用**，启停 / 改时间 / 手动跑 / 看日志全在网页上点 |
| 能在 GitHub 上可视化操作吗？ | **能**，见下面「三、网页上能做的操作」 |

---

## 二、要上传的文件（9 个，都在 `jszg-watch-github\` 里）

```
你的仓库/
├─ .github/workflows/watch.yml    ← 【必需】定时工作流，定时就靠它
├─ .github/workflows/smoke.yml    ← 【建议】改代码时自动跑冒烟测试
├─ jszg_watch.py                  ← 【必需】主程序
├─ config.example.json            ← 【必需】配置模板（不含任何密钥）
├─ state/snapshot.json.gz         ← 【必需】基线快照；带上它才不会把历史公告当新增
├─ reports/.gitkeep               ← 【必需】占位空文件，让 reports/ 目录存在
├─ tools/ci_smoke_test.py         ← 【有 smoke.yml 就需要】冒烟测试脚本
├─ .gitignore                     ← 【建议】防止把 config.json 传上去
└─ README.md                      ← 【建议】说明书
```

**不要上传**：`config.json`（含你的密钥）、`run_now.bat`、`install_task.ps1`
（这两个是本机 Windows 用的）、`logs/`、`__pycache__/`、PC 上的历史报告。

> 已经帮你准备好了：`C:\Users\Admin\Desktop\test\jszg-watch-github\`
> 里面就是上面这 9 个文件，并带着当前基线（858 条公告）。

---

## 三、网页上能做的操作（不需要命令行）

| 想做什么 | 点哪里 |
| --- | --- |
| 上传文件 | 仓库首页 `Add file → Upload files` |
| 立即跑一次 | `Actions → 教师资格网通知公告监控 → Run workflow` |
| 测试微信通不通 | 同上，勾选 `force` |
| 重建基线 | 同上，勾选 `baseline` |
| 改运行时间 | 点开 `.github/workflows/watch.yml` → 铅笔图标 → 改 `cron` → Commit |
| 暂停定时 | `Actions → 左侧选中该工作流 → 右侧 "..." → Disable workflow` |
| 恢复定时 | 同上 → `Enable workflow` |
| 看今天的报告 | 仓库里点开 `reports/latest.html` |
| 看运行记录 / 报错 | `Actions` 标签，绿勾成功 / 红叉失败，点进去看每一步日志 |
| 加密钥 | `Settings → Secrets and variables → Actions → New repository secret` |
| 新建文件 / 目录 | 仓库首页 `Add file → Create new file`，路径里打 `/` 会自动建目录 |

---

## 四、五步走

### 第 1 步：先拿微信推送的 key

| 渠道 | 地址 | 拿什么 |
| --- | --- | --- |
| Server酱（推荐） | <https://sct.ftqq.com> | 微信扫码登录 → SendKey（`SCT` 开头） |
| PushPlus | <https://www.pushplus.plus> | 微信扫码登录 → 「一对一推送」token（32 位） |

### 第 2 步：建仓库

<https://github.com/new> → 名字填 `jszg-watch` → 选 **Private**（推荐）
或 Public（也没有问题，仓库里没有密钥）→ **Create repository**。

### 第 3 步：把文件传上去

1. 新仓库页面点 **uploading an existing file**。
2. 打开 `C:\Users\Admin\Desktop\test\jszg-watch-github\`，**全选里面的内容**拖进网页。
   - 拖的是**文件夹里的内容**，不是外面那层文件夹；
   - `.github` 和 `.gitignore` 是隐藏项：先在资源管理器
     `查看 → 显示 → 隐藏的项目` 打开，否则会漏掉 `.github`，那样**定时不会生效**；
   - 万一拖不上去，也可以用 `Add file → Create new file`，路径填
     `.github/workflows/watch.yml` 再粘贴内容。
3. 底部 `Commit changes` 填一句 `初始化监控` → 提交。

### 第 4 步：加密钥 + 开写权限

- `Settings → Secrets and variables → Actions → New repository secret`：

  | Name | Secret |
  | --- | --- |
  | `SERVERCHAN_SENDKEY` | 你的 SendKey |
  | `PUSHPLUS_TOKEN` | 你的 token（只用一个也行） |

- `Settings → Actions → General → Workflow permissions` 选
  **Read and write permissions**（工作流每天要把新快照提交回仓库，否则报 403）。

### 第 5 步：手动跑一次验证

`Actions → 教师资格网通知公告监控 → Run workflow → 勾选 force → Run workflow`。
等 1～2 分钟，看运行是否绿勾、微信是否收到消息。收到就成功了。

之后每天北京时间 09:00 会自动跑，不用管它。

---

## 五、改时间的换算表

`cron` 用 UTC，`北京时间 = UTC + 8`，中国全年不用夏令时。

| 想在北京时间 | 填 cron |
| --- | --- |
| 每天 09:00 | `0 1 * * *`（当前默认） |
| 每天 08:30 | `30 0 * * *` |
| 每天 12:00 | `0 4 * * *` |
| 每天 09:00 和 21:00 | `0 1,13 * * *` |

---

## 六、三个必须知道的 GitHub 行为

1. **定时会被延迟**：高峰期可能晚几分钟到几十分钟。这是平台调度，不是脚本问题。
   要精确到分钟得用自建服务器或云函数。
2. **仓库连续 60 天没活动，schedule 会被自动停用**并邮件提醒你。本工作流每天都会
   提交一次快照，所以正常跑着就不会停；万一停了，去 Actions 页面点 `Enable workflow`。
3. **Actions 免费额度**：Public 仓库不限量；Private 每月 2000 分钟，
   本任务每次 1～2 分钟，一个月约 60 分钟，够用。