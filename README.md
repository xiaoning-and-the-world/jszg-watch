# 浙江省教师资格认定公告监控（浙江省教育考试院）
# 【补充说明：此源发布的数据已落后，并非各市最新版。如有需要，请自行修改更新。】

每天自动检查 [浙江省教育考试院 · 中小学教师资格认定及定期注册](https://www.zjzs.net/col/col62/index.html)，
发现**新增 / 标题变化 / 下架**就推到微信，并重点高亮**教师资格认定**公告，
用来第一时间发现「浙江省2026年下半年中小学教师资格认定公告」这类新公告。
同一份脚本既能本机跑，也能放到 GitHub 上由 GitHub Actions 每天定时跑（推荐后者：不用开着电脑）。

监控栏目（单个）：

| 栏目 | 说明 |
| --- | --- |
| 中小学教师资格认定及定期注册（col62） | 认定 + 定期注册合并为一个列表，当前约 8 条（会随站点更新变化） |

> 说明：本栏目公告数据内嵌在列表页的 `<record><![CDATA[...]]>` 中，一次请求即可拿全，
> 不需要翻页（`totalRecord=8`、`perPage=12`）。

---

## 目录

- [一、放到 GitHub 上跑（可以全程可视化操作）](#一放到-github-上跑推荐可以全程可视化操作)
- [二、在本机 Windows 上跑](#二在本机-windows-上跑)
- [三、配置项说明](#三配置项说明)
- [四、判断逻辑与误报控制](#四判断逻辑与误报控制重要)
- [五、常见问题](#五常见问题)
- [六、备用定时（外部服务触发）](#六备用定时外部服务触发)

---

## 一、放到 GitHub 上跑（推荐，可以全程可视化操作）

**结论先说：定时工作流需要，但只需要放一次文件，之后全在网页上点。**
GitHub 没有「定时器」这种按钮，定时这件事本身必须由一个工作流文件（`.github/workflows/jszg-watch.yml`）
来描述——它就是那段 `cron: "47 22 * * *"`。文件放进去以后，启停、改时间、
手动触发、看结果、看日志都能在网页上完成，不用碰命令行。

### 1. 需要哪些文件（只要 8 个）

```
你的仓库/
├─ .github/workflows/jszg-watch.yml   ← 定时工作流（必需，定时就靠它）
├─ .github/workflows/smoke.yml    ← 改动代码时自动做冒烟测试（可选但建议）
├─ jszg_watch.py                  ← 主程序（必需）
├─ config.example.json            ← 配置模板，不含密钥（必需）
├─ state/snapshot.json.gz         ← 基线快照（必需，否则第一次要重建基线）
├─ reports/.gitkeep               ← 占位空文件，让 reports/ 目录存在于仓库里（必需）
├─ tools/ci_smoke_test.py         ← smoke.yml 要跑的自检脚本（放了 smoke.yml 就需要）
├─ .gitignore                     ← 防止把 config.json 等传上去（建议）
└─ README.md                      ← 说明书（建议）
```

**不需要上传的**：`config.json`（含你的密钥）、`logs/`、`__pycache__/`、
以及本机跑出来的报告文件。

本仓库已经带好了当前基线快照（`state/snapshot.json.gz`），传上去就是
「已知 8 条左右公告」的状态，不会把历史公告当成新增。

**`reports/` 里会自动生成这些**（每次运行更新，无需手工维护）：

| 文件 | 内容 |
| --- | --- |
| `latest.md` / `latest.html` | 最近一次运行的**变更日报**（新增 / 变化 / 下架…） |
| `snapshot.md` / `snapshot.html` | **通知公告快照**：抓到的**全部**公告标题完整列表（按发布日期倒序，含日期与原文链接） |
| `YYYYMMDD-HHMMSS.md/.html` | 按时间留存的历史日报，默认只保留最近 120 份（`report.keep_last`） |

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
3. 打开本项目所在目录，**全选里面的内容**拖进网页。
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

> **想推给多个账号**：在**同一个 Secret** 里把多个 key 用**逗号 / 分号 / 换行**隔开即可。
> 例如 `SERVERCHAN_SENDKEY = SCT账号A,SCT账号B`，工作流会自动拆成数组逐个推送；
> 只写一个 key 也完全不受影响。

> 顺便检查一下写权限：`Settings → Actions → General → Workflow permissions`
> 选 **Read and write permissions**。工作流每天要把新的快照提交回仓库，
> 没有这个权限就会报 `403`。

### 5. 在网页上打开并手动跑一次

1. 点仓库顶部 **Actions** 标签。
2. 左侧选 **浙江教师资格认定公告监控**。
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
| 立即跑一次 | `Actions → 浙江教师资格认定公告监控 → Run workflow` |
| 测试微信通道 | 同上，勾选 **force** |
| 重建基线 | 同上，勾选 **baseline** |
| 改运行时间 | 打开 `.github/workflows/jszg-watch.yml` → 右上角铅笔图标 → 改 `cron` → Commit |
| 暂停定时 | `Actions → 左侧选中该工作流 → 右侧 "..." → Disable workflow` |
| 恢复定时 | 同上，`Enable workflow` |
| 看今天的报告 | 点开仓库里的 `reports/latest.html` → 右上角 **Raw**/预览即可 |
| 看全部公告清单 | `reports/snapshot.md`（或 `snapshot.html`）—— 每次运行覆盖，含抓到的全部公告标题 |
| 看历史报告 | `reports/` 目录里按时间命名的文件 |
| 只看运行历史 | `Actions` 标签，绿勾=成功，红叉=失败，点进去看日志 |
| 手工新建文件 | 仓库首页 `Add file → Create new file`，路径里打 `/` 会自动建目录 |

**改时间的换算**：`cron` 用的是 UTC，`北京时间 = UTC + 8`，中国全年不用夏令时。

| 想在北京时间 | 填 cron（UTC） |
| --- | --- |
| 每天 06:47 | `47 22 * * *`（当前默认，刻意避开整点） |
| 每天 08:30 | `30 0 * * *` |
| 每天 12:00 | `0 4 * * *` |
| 每天 09:00 和 21:00 | `0 1,13 * * *` |

> **换算规则**：`cron` 用的是 UTC，**北京时间 = UTC + 8**。所以想要北京时间 06:47，
> 要写前一天的 UTC 22:47，也就是 `47 22 * * *`。
>
> **别用整点分钟（`0`）**：GitHub 官方说明整点是负载最高的时候，定时任务可能被延迟，
> 甚至被直接丢弃（"some queued jobs may be dropped"，也就是当天不跑了）。
> 用 `47 22 * * *` 这种非整点更容易准点执行。

### 7. 三个必须知道的 GitHub 行为

1. **定时会延迟、甚至被丢弃**：高峰期可能晚几分钟到几十分钟；GitHub 官方还说明
   整点是负载高峰，队列里的定时任务**可能被直接丢弃**（当天就不跑了）。
   所以本项目默认用非整点 `47 22 * * *`（北京时间 06:47），别改成 `0 22 * * *` 这类整点。
   这是平台调度，不是脚本问题；要精确到分钟就得自建服务器或云函数。
2. **仓库连续 60 天没有任何活动，schedule 会被自动停用**，并给你发邮件提醒。
   本工作流每天都会 `git commit` 一次快照，所以只要它正常跑着就不会被停；
   万一停了，去 Actions 页面点 **Enable workflow** 即可。
3. **Actions 有免费额度**：Public 仓库不限量；Private 仓库每月 2000 分钟，
   本任务每次约 1～2 分钟，一个月 60 分钟左右，完全够用。

---

## 二、在本机 Windows 上跑

不想用 GitHub 也可以纯本机跑（本机需已装 Python 3.8+；脚本只用标准库，不需要 pip 安装）。

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

在项目目录里执行：

```bat
python jszg_watch.py                 :: 立即检查一次（没变化不推送）
python jszg_watch.py --force         :: 强制推一条，测推送通道
python jszg_watch.py --dry-run       :: 只抓取对比并打印，不推送、不写文件
python jszg_watch.py --self-test     :: 自检：配置、网络、列表与正文是否抓得到
python jszg_watch.py --baseline      :: 重建基线
```

### 3. 设成每天 09:00 自动跑（可选）

用 Windows 自带的「任务计划程序」即可，不需要额外脚本：

1. `Win + R` → 输入 `taskschd.msc` → 回车；
2. 右侧「创建基本任务」→ 名称随便填（如「教师资格认定公告监控」）→
   触发器选「每天」、时间填 `09:00`；
3. 操作选「启动程序」：
   - **程序或脚本**：`python`
   - **添加参数**：`jszg_watch.py`
   - **起始于**：本项目所在目录（例如 `C:\path\to\jszg-watch-github`）
4. 完成后确定即可。

> 等价的一条命令（管理员 CMD 执行一次，注意把路径换成你自己的）：
> ```bat
> schtasks /Create /TN "jszg-watch" /SC DAILY /ST 09:00 /TR "cmd /c cd /d C:\path\to\jszg-watch-github && python jszg_watch.py"
> ```
> 删除任务：`schtasks /Delete /TN "jszg-watch" /F`

---

## 三、配置项说明

```jsonc
{
  "enabled": true,                    // false = 整体停用（CI 里仍然会跑，只是不推送）

  "channels": {
    // 单账号写字符串；同一渠道要推多个账号就写成数组，会逐个推送
    "serverchan": { "enabled": true, "sendkey": "SCT..." },
    "pushplus":   { "enabled": false, "token": ["token账号A", "token账号B"] }
  },

  "notify": {
    "always_notify": true,            // true = 每天都推（默认）：有重点变动推变动，否则推「最近公告」
    "notify_non_focus": true,         // 仅当 always_notify=false 时才有意义：非重点变动是否也推
    "recent_count": 5                 // 「最近公告」的条数（按发布日期倒序，0 = 不带）
  },

  "watch": {
    "list_url": "https://www.zjzs.net/col/col62/index.html", // 换栏目/换站只改这里
    "column_name": "中小学教师资格认定及定期注册",
    "focus_keywords": ["教师资格认定"], // 标题命中即「重点」：高亮 + 优先推送
    "extra_keywords": [],             // 额外关注词，命中会在报告里单列
    "digest_new": true,               // 只对新增公告抓正文（正文监控）
    "digest_max": 10,                 // 单轮最多为多少条新增公告抓正文
    "removal_confirm_days": 7,        // 连续多少天抓不到才确认「下架」
    "page_delay": 0.5,                // 请求间隔秒数
    "timeout": 30, "retries": 3, "retry_delay": 3,
    "allow_insecure_ssl": true        // 该站点证书链异常，需降级校验（见下）
  },

  "report": { "keep_last": 120 }      // reports/ 保留最近多少份
}
```

> **关于 `allow_insecure_ssl`**：`www.zjzs.net` 的 TLS 证书链不完整（链中出现自签名证书），
> Python 默认校验会直接失败（浏览器能容错，所以你在浏览器里打不开异常）。
> 为 `true` 时脚本会先按正常校验请求，失败后自动降级为「不校验证书」再重试一次——
> 站点哪天修好证书，就会自动恢复正常校验。若你更看重安全性，可设为 `false`，
> 但那样该站点很可能一直抓不到。

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

---

## 四、判断逻辑与误报控制（重要）

| 风险 | 处理方式 |
| --- | --- |
| 公告顺序 / 日期抖动 | 以**文章 ID**（`art_62_12627`）为唯一键判定新增，日期只作展示 |
| 老公告被误判成新增 | 快照**只增不减**：曾经见过的 ID 永远不会被当成新增 |
| 漏抓导致误判下架 | 只有**连续 7 天**都抓不到才确认下架（`removal_confirm_days`），中途又抓到就清零 |
| 下架后重复告警 | 确认下架的公告会**从快照中移除**，不会之后每天都报一次 |
| 抓取不完整 | 该轮**不做任何下架判定**，快照按合并方式保存，不会把没抓到的当新增 / 下架 |
| 换了站点/栏目 | 快照里记录了 `site`，一旦监控目标变化会自动**重建基线**，旧站点数据既不报新增也不报下架 |
| 网络中断 | 彻底失败会推一条「抓取失败」 |

**正文监控的口径**：只对**本次新增**的公告抓取正文（`watch.digest_new`），
正文摘要写进 Markdown / HTML 报告（**微信推送里只有标题 + 日期，不含正文摘要**）；
历史公告不再重复抓取，**也不检测历史公告的正文改动**，**不检测附件变化**。
因此每轮请求数很少（正常 1 次列表 + 新增条数这么多次正文）。

**推送消息长什么样**（默认**每天都推**，标题与正文分两种情况）：

| 情况 | 微信通知标题 | 正文 |
| --- | --- | --- |
| 有命中 `focus_keywords`（默认「教师资格认定」）的变动 | `浙江省教师资格认定公告有更新` | 只列**变动的那条/那几条**：加粗标题 + `发布时间：… ｜ 推送时间：…` |
| 其余情况（非重点变动 / 没有任何变动） | `浙江教师资格认定公告监控｜通知公告有更新` 或 `…｜通知公告无新增、无变化` | `推送时间：…` + **最近 N 条公告**（默认 5，`notify.recent_count` 控制） |

举例（命中重点时）：

```
浙江省教师资格认定公告有更新          ← 通知标题

浙江省2026年下半年中小学教师资格认定公告   ← 正文
发布时间：2026-10-08 ｜ 推送时间：2026-10-08 06:47:00
```

> **为什么要按 Markdown 排版**：正文用 `**加粗**` 当小标题、`- ` 当列表项、区块之间留空行，
> 这样在微信里才会正常换行。Markdown 会把**单个换行折叠成空格**，不做列表/空行处理就会挤成"一片"。
> 如果某个渠道仍显示成整段，说明该渠道没按 Markdown 渲染。
>
> **注意**：非重点的「下架 / 重新出现」不会在推送里单独列出（只在 `reports/latest.html` 报告里），
> 因为它们不属于"命中关键词的变动"，会走"最近 N 条公告"那一支。

代价是：**下架的告警会延迟**（需要连续 7 天确认）。对「每天看一眼有没有新公告」这个需求，
换来的几乎为零的误报是划算的；想更灵敏就把 `removal_confirm_days` 调小。

**重点公告**：标题命中 `watch.focus_keywords`（默认 `["教师资格认定"]`）的新增/变化公告
会高亮为「重点关注」并优先推送——等 2026 年下半年认定公告时就会第一时间提醒你。

---

## 五、常见问题

**Q：Actions 里显示「首次运行 · 已建立基线」，但我明明跑过好几次了？**
A：说明 `state/snapshot.json.gz` 没被提交回仓库。检查工作流的「写回快照与报告」
步骤有没有报错（常见原因是仓库 Settings → Actions → Workflow permissions 里
把写权限关掉了，或仓库开了分支保护）。

**Q：到了时间 Actions 却没有自动运行？**
A：按下面顺序查（从最常见到最少见）：

1. **先看有没有 `schedule` 触发的记录**：`Actions` → 左侧选中该工作流，列表的 `Event` 列会写
   `schedule` 还是 `workflow_dispatch`。
   - 有 `schedule` 记录但失败 → 点进去看哪一步挂了，多半是「写回快照与报告」的 `git push`
     （写权限没开）。
   - **完全没有 `schedule` 记录 → 是调度层面的问题**，继续往下查。
2. **工作流是否被停用**：左侧选中后，右侧若出现 `Enable workflow` 就点它。
   仓库「连续 60 天没有任何活动」时 GitHub 会自动停用定时（本工作流每天提交快照，正常不会被停）。
3. **工作流文件必须在默认分支上**：`schedule` 只在默认分支（一般是 `main`）生效。
   如果 `.github/workflows/jszg-watch.yml` 传到了别的分支，定时不会触发。
4. **Actions 是否可用 / 额度是否用完**：`Settings → Actions → General` 确认允许 Actions；
   私有仓库每月免费分钟数用完后 Actions 会停止运行（`Settings → Billing` 可查）。
5. **是不是被整点拥堵吞掉了**：本工作流默认已改成非整点 `47 22 * * *`（北京时间 06:47）。
6. **用一次手动 `Run workflow` 交叉验证**：手动能跑通 → 脚本和权限都没问题，纯粹是调度，
   等下一次即可。

**Q：想让两个微信号都收到推送？**
A：两种方式：
- **不同渠道各一个**：Server酱 和 PushPlus 都设 `enabled: true`、各填一个 key，会各推一条；
- **同一渠道多个账号**：
  - **本机跑** → 把 `config.json` 里的 `sendkey` / `token` 写成**数组**，例如
    `"serverchan": { "enabled": true, "sendkey": ["SCT账号A", "SCT账号B"] }`；
  - **GitHub Actions** → 在**同一个 Secret** 里用逗号 / 分号 / 换行分隔，例如
    `SERVERCHAN_SENDKEY = SCT账号A,SCT账号B`（工作流会自动拆成数组）。

  脚本会逐个推送；**只有失败的账号会重试**，成功的账号不会收到重复消息。
  只要其中某一个账号重试后仍失败，任务退出码会变成 4（Actions 显示红叉），
  方便你发现「只发出去一半」。

**Q：Actions 报错 `EnvironmentFileNotFound: '.../environment.yml' file not found`，
还打印一堆 ANACONDA TERMS OF SERVICE？**
A：**这不是本项目的报错**，是 GitHub 自带的 Anaconda 入门模板（`python-package-conda.yml`）
在跑。去 `Actions` 左侧栏看失败的那次运行叫什么名字——如果叫
「Python Package using Conda」，就把它删掉：

`Settings` 左边的 `Code` → 进 `.github/workflows/` 目录 → 打开
`python-package-conda.yml` → 右上角 `...` → **Delete file** → Commit。

删掉后只留下 `jszg-watch.yml` 和 `smoke.yml` 两个就没问题了。
判断依据：本项目的工作流把代码检出到 `workspace/` 子目录，日志里的路径会带
`/workspace/`；而那个报错路径是 `/home/runner/work/jszg-watch/jszg-watch/environment.yml`，
正好是「没有子目录」的检出方式，说明它来自别的文件。本项目不用 conda、也不用 pip。

**Q：只想要定时任务，不想要「每次提交都跑测试」。**
A：删掉 `.github/workflows/smoke.yml` 即可，`jszg-watch.yml` 不依赖它
（`tools/ci_smoke_test.py` 可以留着，也可以一起删）。

**Q：每天都推一条「无变化」？**
A：`notify.always_notify` 被改成了 `true`，改回 `false` 即可。

**Q：只想收重点公告（教师资格认定）的提醒。**
A：`notify.notify_non_focus` 改成 `false`；想改「什么算重点」就调 `watch.focus_keywords`。

**Q：微信一直收不到。**
A：先在 Actions 里用 `force` 手动跑一次看日志。`Server酱: 成功` 表示推送接口
收下了；收不到多半是没关注对应公众号 / token 复制错了。

**Q：网站改版抓不到了。**
A：先跑 `--self-test` 看能抓到多少条（正常应为 8 条左右）。若为 0 或明显偏少，
多半是列表页 `<record><![CDATA[...]]>` 的结构变了，改 `jszg_watch.py` 里的
`RECORD_RE` 等相关正则即可。

**Q：快照会越来越大吗？**
A：不会。栏目只有 8 条公告，每条只存标题 / 日期 / 链接等少量字段，gzip 后仅几 KB。
`reports/` 默认只留最近 120 份。

**Q：想换成别的栏目 / 别的站点？**
A：改配置里的 `watch.list_url` 和 `watch.column_name` 即可，不用动代码。换站后脚本会
自动识别 `site` 变化并重建基线，不会把新站点的历史公告当成新增，也不会把旧站点数据
误判成下架。

**Q：我能把快照 / 报告换成别的存储吗（比如 Gist、S3）？**
A：可以。`load_snapshot()` / `write_snapshot()` 是唯一的读写入口，改成调用 API
即可，其余逻辑不用动。

---

## 六、备用定时（外部服务触发）

GitHub 的 `schedule` 是**"尽力而为"**的：会被延迟，队列忙时会被丢弃；极少数仓库/账号上
甚至**完全不触发**——表现就是：`Actions` 里永远只有手动运行，`Event` 列从没出现过 `schedule`。

这种情况**不是你文件写错了**（文件、分支、权限、Actions 开关都可以是正常的），
继续改 cron 也没用。解决办法是：**用免费的外部定时服务调用 GitHub 的 API，
等价于每天替你点一次「Run workflow」按钮**——它走的是 `workflow_dispatch`，
所以**只要你能手动运行，它就一定能触发**。

### 1. 生成一个 PAT（访问令牌）

`GitHub 头像 → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token`

- **Repository access**：选 `Only select repositories` → 只勾这个监控仓库
- **Permissions → Repository permissions → Actions**：改成 **Read and write**
- 生成后立刻复制（形如 `github_pat_...`，**只显示一次**）

### 2. 在 cron-job.org 建一个定时任务（免费）

注册 <https://cron-job.org> → `Create cronjob`，按下表填写：

| 字段 | 值 |
| --- | --- |
| Title | `jszg-watch`（随便填） |
| URL | `https://api.github.com/repos/<用户名>/<仓库名>/actions/workflows/jszg-watch.yml/dispatches` |
| Schedule | 每天，北京时间 **06:47** |
| Request method | `POST` |
| Header | `Authorization: Bearer github_pat_你的令牌` |
| Header | `Accept: application/vnd.github+json` |
| Request body | `{"ref":"main"}` |

保存后点一次 **TEST RUN**：

- 返回 **`204 No Content`** → 成功；
- 回 `Actions` 页面刷新，会立刻多出一条运行记录（显示为 `Manually run by ...`，因为 API 触发就是 dispatch）。

### 3. 几点说明

- **可能重复推送**：如果同时保留 `jszg-watch.yml` 里的 `schedule`（万一哪天 GitHub 又好了）
  又用外部定时器，同一天可能被触发两次、收到两条推送。想避免的话，把 `jszg-watch.yml` 里
  `on.schedule` 那两行删掉，只保留一种触发方式即可。
- **PAT 可以设置有效期**，到期前换一个即可；只勾这一个仓库、只给 Actions 权限，风险很小。
- 如果你干脆不想要 GitHub 的定时了，把 `jszg-watch.yml` 里 `on.schedule` 那两行删掉也可以，
  外部定时器照常工作。
