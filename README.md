# 教师资格网通知公告监控（浙江省教师资格认定）

每天自动检查 [中国教师资格网 · 资格资讯 > 通知公告](https://www.jszg.edu.cn/news.html)，
发现**新增 / 更新 / 下架**就推到微信，并单独高亮**浙江省**的教师资格认定公告。
最初是给 Windows 本机写的，同一份脚本既能本机跑，也能放到 GitHub 上由
GitHub Actions 每天定时跑（推荐后者：不用开着电脑）。

监控两个栏目：

| 栏目 | 说明 |
| --- | --- |
| 教师资格认定（id=107） | 750+ 条，各省认定公告都在这里 |
| 教师资格定期注册（id=181） | 100+ 条，含浙江省定期注册公告 |

---

## 目录

- [一、放到 GitHub 上跑（可以全程可视化操作）](#一放到-github-上跑推荐可以全程可视化操作)
- [二、在本机 Windows 上跑](#二在本机-windows-上跑)
- [三、配置项说明](#三配置项说明)
- [四、判断逻辑与误报控制](#四判断逻辑与误报控制重要)
- [五、常见问题](#五常见问题)

---

## 一、放到 GitHub 上跑（推荐，可以全程可视化操作）

**结论先说：定时工作流需要，但只需要放一次文件，之后全在网页上点。**
GitHub 没有「定时器」这种按钮，定时这件事本身必须由一个工作流文件（`.github/workflows/watch.yml`）
来描述——它就是那段 `cron: "0 1 * * *"`。文件放进去以后，启停、改时间、
手动触发、看结果、看日志都能在网页上完成，不用碰命令行。

### 1. 需要哪些文件（只要 8 个）

```
你的仓库/
├─ .github/workflows/watch.yml    ← 定时工作流（必需，定时就靠它）
├─ .github/workflows/smoke.yml    ← 改动代码时自动做冒烟测试（可选但建议）
├─ jszg_watch.py                  ← 主程序（必需）
├─ config.example.json            ← 配置模板，不含密钥（必需）
├─ state/snapshot.json.gz         ← 基线快照（必需，否则第一次要重建基线）
├─ reports/.gitkeep               ← 占位空文件，让 reports/ 目录存在于仓库里（必需）
├─ tools/ci_smoke_test.py         ← smoke.yml 要跑的自检脚本（放了 smoke.yml 就需要）
├─ .gitignore                     ← 防止把 config.json 等传上去（建议）
└─ README.md                      ← 说明书（建议）
```

**不需要上传的**：`config.json`（含你的密钥）、`run_now.bat`、`install_task.ps1`
（这两个是 Windows 本机用的）、`logs/`、`__pycache__/`、以及原来 PC 上的报告文件。

我已经把这份「只含必要文件」的干净目录放在：

```
C:\Users\Admin\Desktop\test\jszg-watch-github\
```

里面已经带好了当前基线快照，传上去就是「已知 858 条公告」的状态，
不会把历史公告当成新增。

### 2. 第 0 步：拿微信推送的 key（先做，之后直接填网页表单）

| 渠道 | 地址 | 拿什么 |
| --- | --- | --- |
| Server酱（推荐） | <https://sct.ftqq.com> | 微信扫码登录 → 复制 SendKey（`SCT` 开头） |
| PushPlus | <https://www.pushplus.plus> | 微信扫码登录 → 复制「一对一推送」token（32 位） |

### 3. 全程可视化：网页上把文件传上去

不用装 Git，不用命令行：

1. 打开 <https://github.com/new> → **Repository name** 填 `jszg-watch` →
   选 **Private**（推荐，只有你自己看得到；选 Public 也可以，因为仓库里没有密钥）→
   **Create repository**。
2. 在新仓库页面点 **uploading an existing file**（或 `Add file → Upload files`）。
3. 打开 `C:\Users\Admin\Desktop\test\jszg-watch-github\`，**全选里面的内容**拖进网页。
   > ⚠️ 注意两点：
   > - 拖**文件夹里的内容**，不要拖外面那层文件夹本身；
   > - `.github`、`.gitignore` 是隐藏项。Windows 资源管理器里先
   >   `查看 → 显示 → 隐藏的项目` 打开显示，否则会漏传 `.github`（那样定时就不会生效）。
   >   如果怎么都传不上去，用下面第 6 步的「网页新建文件」方式手工建这两个文件也行。
4. 下方 **Commit changes** 填一句说明，例如 `初始化监控` → 点 **Commit changes**。
5. 上传完成后再确认一次目录结构：仓库首页应该能看到 `.github`、`jszg_watch.py`、
   `state`、`reports`、`tools`、`config.example.json`。

### 4. 在网页上加密钥（Secrets）

`Settings → Secrets and variables → Actions → New repository secret`，加两条：

| Name | Secret |
| --- | --- |
| `SERVERCHAN_SENDKEY` | 你的 SendKey（`SCT...`） |
| `PUSHPLUS_TOKEN` | 你的 32 位 token |

只用其中一个也行，另一个留着空不建即可。密钥只存在 GitHub 的加密存储里，
工作流运行时才注入，**不会**出现在仓库文件或日志里。

> 顺便检查一下写权限：`Settings → Actions → General → Workflow permissions`
> 选 **Read and write permissions**。工作流每天要把新的快照提交回仓库，
> 没有这个权限就会报 `403`。

### 5. 在网页上打开并手动跑一次

1. 点仓库顶部 **Actions** 标签。
2. 左侧选 **教师资格网通知公告监控**。
3. 右侧 **Run workflow** 按钮 → 下拉里勾选：
   - **force** = true → 无论有没有变化都推一条，用来验证微信通道是否配好；
   - 第一次建库时不要勾 **baseline**（仓库里已经带了快照，勾了反而会重建）。
4. 点绿色 **Run workflow**，等 1～2 分钟刷新页面，能看到一条运行记录。
5. 点进去看每一步的日志：`抓取并对比` 里会打印新增/变化数量，
   `写回快照与报告` 会把 `state/` 和 `reports/` 提交回仓库。
6. 微信收到消息 = 全部搞定。

### 6. 网页上常用操作速查

| 想做什么 | 在哪里点 |
| --- | --- |
| 立即跑一次 | `Actions → 教师资格网通知公告监控 → Run workflow` |
| 测试微信通道 | 同上，勾选 **force** |
| 重建基线 | 同上，勾选 **baseline** |
| 改运行时间 | 打开 `.github/workflows/watch.yml` → 右上角铅笔图标 → 改 `cron` → Commit |
| 暂停定时 | `Actions → 左侧选中该工作流 → 右侧 "..." → Disable workflow` |
| 恢复定时 | 同上，`Enable workflow` |
| 看今天的报告 | 点开仓库里的 `reports/latest.html` → 右上角 **Raw**/预览即可 |
| 看历史报告 | `reports/` 目录里按时间命名的文件 |
| 只看运行历史 | `Actions` 标签，绿勾=成功，红叉=失败，点进去看日志 |
| 手工新建文件 | 仓库首页 `Add file → Create new file`，路径里打 `/` 会自动建目录 |

**改时间的换算**：`cron` 用的是 UTC，`北京时间 = UTC + 8`，中国全年不用夏令时。

| 想在北京时间 | 填 cron |
| --- | --- |
| 每天 09:00 | `0 1 * * *`（当前默认） |
| 每天 08:30 | `30 0 * * *` |
| 每天 12:00 | `0 4 * * *` |
| 每天 09:00 和 21:00 | `0 1,13 * * *` |

### 7. 三个必须知道的 GitHub 行为

1. **定时会延迟**：高峰期可能晚几分钟到几十分钟，偶尔更久。这是平台调度，
   不是脚本问题。对「每天看一眼公告」够用；要精确到分钟就得自建服务器或云函数。
2. **仓库连续 60 天没有任何活动，schedule 会被自动停用**，并给你发邮件提醒。
   本工作流每天都会 `git commit` 一次快照，所以只要它正常跑着就不会被停；
   万一停了，去 Actions 页面点 **Enable workflow** 即可。
3. **Actions 有免费额度**：Public 仓库不限量；Private 仓库每月 2000 分钟，
   本任务每次约 1～2 分钟，一个月 60 分钟左右，完全够用。

---

## 二、在本机 Windows 上跑

不想用 GitHub 也可以纯本机跑。

### 1. 配置 `config.json`

把 `config.example.json` 复制成 `config.json`，填上 key / token：

```json
"channels": {
  "serverchan": { "enabled": true,  "sendkey": "SCT你的Key" },
  "pushplus":   { "enabled": false, "token": "" }
}
```

> `config.json` 已被 `.gitignore` 忽略，不会被提交。`config.example.json` 是
> 模板，里面**没有**真实密钥，可以放心上传。

### 2. 跑起来

```bat
run_now.bat                 :: 立即检查一次（没变化不推送）
run_now.bat --force         :: 强制推一条，测推送通道
run_now.bat --dry-run       :: 只抓取对比并打印，不推送、不写文件
run_now.bat --self-test     :: 自检：配置、网络、两个栏目是否抓得到
run_now.bat --baseline      :: 重建基线
```

### 3. 装成每天 09:00 的计划任务

```powershell
powershell -ExecutionPolicy Bypass -File .\install_task.ps1              # 默认 09:00
powershell -ExecutionPolicy Bypass -File .\install_task.ps1 -Time 08:30  # 改时间
powershell -ExecutionPolicy Bypass -File .\install_task.ps1 -RunNow      # 立即跑一次
powershell -ExecutionPolicy Bypass -File .\install_task.ps1 -Remove      # 删除任务
```

> `install_task.ps1` 存的是 **UTF-8 with BOM**。Windows PowerShell 5.1 读取无 BOM
> 的 UTF-8 脚本会按 GBK 解码，中文乱码并报语法错误，改这个文件时请保持 BOM。

---

## 三、配置项说明

```jsonc
{
  "enabled": true,                    // false = 整体停用（CI 里仍然会跑，只是不推送）

  "channels": {
    "serverchan": { "enabled": true, "sendkey": "SCT..." },
    "pushplus":   { "enabled": false, "token": "" }
  },

  "notify": {
    "always_notify": false,           // true = 每天不管有没有变化都推一条
    "notify_non_zhejiang": true       // false = 只在浙江有变动时才打扰你
  },

  "watch": {
    "province_keywords": ["浙江"],    // 标题含这些词就算「本省相关」
    "extra_keywords": [],             // 额外关注词，命中会在报告里单列
    "max_pages": 80,                  // 每个栏目最多翻多少页（1 页 10 条）
    "list_passes": 2,                 // 列表抓几遍再按 ID 合并（对抗缓存抖动）
    "digest_pages": 2,                // 对每个栏目最近 N 页抓正文指纹
    "removal_confirm_days": 7,        // 连续多少天抓不到才确认「下架」
    "page_delay": 0.3,                // 请求间隔秒数
    "timeout": 30, "retries": 3, "retry_delay": 3
  },

  "report": { "keep_last": 120 }      // reports/ 保留最近多少份
}
```

### 微信推送怎么开通

| 渠道 | 地址 | 拿什么 |
| --- | --- | --- |
| Server酱（推荐） | <https://sct.ftqq.com> | 微信扫码登录 → SendKey（`SCT` 开头） |
| PushPlus | <https://www.pushplus.plus> | 微信扫码登录 → 一对一推送 token（32 位） |

### 环境变量（可选，覆盖配置文件）

CI 里为了避免把密钥写进文件，优先用环境变量；本机也能用：

| 环境变量 | 作用 |
| --- | --- |
| `JSZG_SERVERCHAN_KEY` | 设置后自动启用 Server酱 |
| `JSZG_PUSHPLUS_TOKEN` | 设置后自动启用 PushPlus |
| `JSZG_DATA_DIR` | 数据目录（快照 / 报告 / 日志放哪） |
| `JSZG_CONFIG` | 指定配置文件路径 |
| `JSZG_WRITE_PARTIAL` | CI 里也写 `state/partial.json.gz`（默认不写） |

---

## 四、判断逻辑与误报控制（重要）

这个网站的列表页和详情页前面有一层**多版本缓存**：连续请求同一页，可能分别命中
「今天 / 3 天前 / 5 天前」等不同旧版本。实测 8 轮抓取里，某个公告只出现了 2 次；
发布日期、正文内容、甚至某条公告在不在列表里都会来回抖。所以脚本做了这些防护：

| 风险 | 处理方式 |
| --- | --- |
| 公告顺序 / 日期抖动 | 以**文章 ID**（`a1234...`）为唯一键判定新增，日期只作展示 |
| 整条公告偶尔抓不到 | 每个栏目列表**抓 2 遍**按 ID 合并（`watch.list_passes`） |
| 正文被抓到旧版本 | 连续两次取样都偏离历史版本才算「疑似改动」，且要**下一次运行仍一致**才确认 |
| 老公告被误判成新增 | 快照**只增不减**：曾经见过的 ID 永远不会被当成新增；某轮暂时缺失的公告恢复后归入报告的「暂时查不到 · 现已恢复」 |
| 漏抓导致误判下架 | 只有**连续 7 天**都抓不到才确认下架（`removal_confirm_days`），中途又抓到就清零 |
| 网络中断 | 抓取不完整时快照按合并方式保存，不会把没抓到的当新增 / 下架；彻底失败会推一条「抓取失败」 |

代价是：**正文改动和下架的告警会延迟**（分别需要两次运行 / 连续 7 天确认）。
对「每天看一眼有没有浙江新公告」这个需求，换来的几乎为零的误报是划算的。
想更灵敏就调小 `removal_confirm_days`、调大 `list_passes`。

顺带说明：这个网站「教师资格认定」栏目的公告标题里省份一定写全（如
「浙江省2026年下半年中小学教师资格认定公告」），按标题里的「浙江」匹配很可靠。
当前基线里与浙江相关的公告共 **24 条**：认定栏目 20 条，定期注册栏目 4 条；
最近一条浙江认定公告是 `2026-03-16`，最近一条浙江定期注册公告是 `2026-09-17`。

---

## 五、常见问题

**Q：Actions 里显示「首次运行 · 已建立基线」，但我明明跑过好几次了？**
A：说明 `state/snapshot.json.gz` 没被提交回仓库。检查工作流的「写回快照与报告」
步骤有没有报错（常见原因是仓库 Settings → Actions → Workflow permissions 里
把写权限关掉了，或仓库开了分支保护）。

**Q：每天都推一条「无变化」？**
A：`notify.always_notify` 被改成了 `true`，改回 `false` 即可。

**Q：只想收浙江的，别的省别烦我。**
A：`notify.notify_non_zhejiang` 改成 `false`。

**Q：微信一直收不到。**
A：先在 Actions 里用 `force` 手动跑一次看日志。`Server酱: 成功` 表示推送接口
收下了；收不到多半是没关注对应公众号 / token 复制错了。

**Q：网站改版抓不到了。**
A：先跑 `--self-test` 看两个栏目各能抓到多少条。若明显变少，多半是列表页结构
变了，改 `jszg_watch.py` 里的 `ITEM_RE` 正则即可。

**Q：快照会越来越大吗？**
A：不会。每条公告只存 `hashes`（最多 8 个历史正文指纹）等少量字段，gzip 后
约 40 KB，一年 365 次提交约 15 MB。`reports/` 默认只留最近 120 份。

**Q：我能把快照 / 报告换成别的存储吗（比如 Gist、S3）？**
A：可以。`load_snapshot()` / `write_snapshot()` 是唯一的读写入口，改成调用 API
即可，其余逻辑不用动。
