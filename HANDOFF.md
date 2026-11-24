# 小豆邀请码抓码工作流交接文档

本文给本地 ORCA bot（以及操作它的人）照着做。本地**不调用 X API**，用已经登录 X 的 **EGO Lite 浏览器**在 x.com 搜索；脚本只负责筛选、合并和提交 `codes.json`。所有时间都用**北京时间**。

此前在本机 `/workspace`、`/home/box` 里查找过含 `xiaodou` / `codes.json` / `since_id` 的巡检脚本，**没有发现可复用的独立脚本**。之前的巡检是 Grok Bot 用内置 X 搜索工具按规则执行的。下面的 `scripts/patrol.py` 是按现行规则新写的实现。

---

## 1. 目标

每 5 分钟从 X 上找出「真正分享了豆包小豆邀请码、而且当时还没有回复」的原帖，把码写进网站数据文件，部署到：

https://paipiange.github.io/xiaodou-codes/

一码只能用一次。出处记原始发帖人。不要用 BetterOPC 或其他第三方码站。不要卖码、不要收费代抢。

原定巡检截止：**2026-11-26 16:30 北京时间**。到期后脚本仍可运行，是否继续由你决定。不少码的 `expires` 也落在 11 月 26 日当天晚上。

---

## 2. 架构

```
EGO Lite（已登录 X）
    → 打开最新搜索页，抓帖子（含回访已有「可能可用」原帖的回复数）
    → ORCA bot 整理成 posts.json
    → python scripts/patrol.py --posts posts.json
         筛选 / 抽码 / 合并进 codes.json
         用 GitHub Contents API 提交（只改 codes.json）
    → GitHub Actions（pages.yml）部署到 GitHub Pages
    → 网站每 60 秒重新拉取 codes.json
```

有新的 0 回复码时，脚本会把码、帖子链接、北京时间发帖时间打印到控制台，由 ORCA / 任务计划程序的日志转发给用户。没有新码就只打印「没有需要写入的变化」，不必再通知。

Grok Bot 里还有一个抓码站 Canvas Page（id `0a711cdf-5053-4988-96a6-069e65221508`），**只在 Grok Bot 内能打开**，交接后不必再维护。对外以 GitHub Pages 网站为准。

---

## 3. 仓库与网站

| 项 | 值 |
| --- | --- |
| 仓库 | https://github.com/paipiange/xiaodou-codes （默认分支 `main`） |
| 网站 | https://paipiange.github.io/xiaodou-codes/ |
| 数据 | 根目录 `codes.json`（页面每 60 秒 fetch 一次） |
| 部署 | `.github/workflows/pages.yml`，`push` 到 `main` 或手动 `workflow_dispatch` |

仓库里现有文件：

- `index.html`：站点页面（含 Google 站长验证 meta、不蒜子统计、标题/描述/关键词）
- `codes.json`：邀请码数据，**巡检只改这一个文件**
- `robots.txt`、`sitemap.xml`
- `12fb0e8f8c287ee46be0571f729ca8df.txt`：IndexNow 公开验证文件（不是密钥）
- `.nojekyll`
- `.github/workflows/pages.yml`

工作流在「Prepare site」一步只把下面这些文件拷到 `_site/`：

```
index.html  codes.json  robots.txt  sitemap.xml  12fb0e8f8c287ee46be0571f729ca8df.txt
```

因此仓库里的 `HANDOFF.md`、`scripts/patrol.py` **不会出现在网站上**。不要改 `index.html` 或 `pages.yml` 来「顺便」部署文档。

---

## 4. `codes.json` 字段说明与示例

文件是 UTF-8 JSON，缩进 2 空格，末尾换行。顶层三个字段：

| 字段 | 说明 |
| --- | --- |
| `lastUpdated` | 本次写入时间，格式 `YYYY-MM-DD HH:MM 北京时间`，例如 `2026-11-24 21:12 北京时间` |
| `lastSinceId` | **上次处理到的帖子 ID**（X 雪花 ID），必须是**字符串**，不要写成数字，以免 JavaScript 丢精度。下次只把比它更新的帖当「新帖」处理。 |
| `codes` | 邀请码数组，**新码插在最前面**。同一 `code` 不重复，只更新 `replyCount` / `status` / `note`。 |

每条码的字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `code` | 是 | 邀请码本身，通常已是大写 |
| `postId` | 是 | 原帖雪花 ID，字符串 |
| `author` | 是 | 原帖作者用户名，不含 `@` |
| `postUrl` | 是 | `https://x.com/{author}/status/{postId}` |
| `postedAt` | 是 | `YYYY-MM-DD HH:MM:SS 北京时间` |
| `postedAtUtc` | 是 | UTC，`YYYY-MM-DDTHH:MM:SS.000Z` |
| `replyCount` | 是 | 整数，来自原帖当时的回复数 |
| `status` | 是 | 见下表 |
| `expires` | 否 | 帖子写了有效期才填，`YYYY-MM-DD HH:MM`（北京时间，**不加**「北京时间」后缀） |
| `officialUrl` | 否 | 官方邀请页，`https://activity.doubao.com/ug/invite-share/{code}` |
| `note` | 否 | 例如官方页说明，或「已确认被用」的证据链接 |

`status` 取值：

| 值 | 页面上的含义 | 规则 |
| --- | --- | --- |
| `maybe_available` | 可能可用 | 收录时 `replyCount` 为 0 |
| `maybe_used` | 可能已用 | 之后看到 `replyCount >= 1` |
| `confirmed_used` | 已确认被用 | 有人明确说这个码用掉了；`note` 里写证据帖链接。一旦确认，不要再改回前两个状态 |

访客点「我试过了，不能用」只存在浏览器 `localStorage` 里，**不要写入 `codes.json`**。

交接当时（以当时线上文件为准，跑脚本前请再读一次最新的 `codes.json`）快照：

- `lastUpdated`：`2026-11-24 21:12 北京时间`
- `lastSinceId`：`"2125215979454316748"`
- 约 33 个码。其中 `L4TT9C` 为 `confirmed_used`（@yitaoreal 确认已用）；`M-342WXG` 带官方邀请页；`KFCV50` 曾以 0 回复收录。

示例（节选，不是完整文件）：

```json
{
  "lastUpdated": "2026-11-24 21:12 北京时间",
  "lastSinceId": "2125215979454316748",
  "codes": [
    {
      "code": "FG83KC",
      "postId": "2125176786560676175",
      "author": "tsaielio",
      "postUrl": "https://x.com/tsaielio/status/2125176786560676175",
      "postedAt": "2026-11-24 18:36:44 北京时间",
      "postedAtUtc": "2026-11-24T10:36:44.000Z",
      "replyCount": 0,
      "status": "maybe_available",
      "expires": "2026-11-26 18:35"
    },
    {
      "code": "M-342WXG",
      "postId": "2125142511115190395",
      "author": "GH_Seauro",
      "postUrl": "https://x.com/GH_Seauro/status/2125142511115190395",
      "postedAt": "2026-11-24 16:20:32 北京时间",
      "postedAtUtc": "2026-11-24T08:20:32.000Z",
      "replyCount": 1,
      "status": "maybe_used",
      "officialUrl": "https://activity.doubao.com/ug/invite-share/M-342WXG",
      "note": "官方邀请页 https://activity.doubao.com/ug/invite-share/M-342WXG"
    },
    {
      "code": "L4TT9C",
      "postId": "2125142292248100960",
      "author": "windsolo1",
      "postUrl": "https://x.com/windsolo1/status/2125142292248100960",
      "postedAt": "2026-11-24 16:19:40 北京时间",
      "postedAtUtc": "2026-11-24T08:19:40.000Z",
      "replyCount": 0,
      "status": "confirmed_used",
      "expires": "2026-11-26 11:27",
      "note": "@yitaoreal 于 16:29:32 北京时间确认已用 https://x.com/yitaoreal/status/2125144776072634488"
    }
  ]
}
```

有变化才提交。只推进 `lastSinceId`、码列表完全没变时，**不要提交**，以免无意义地触发 Pages 部署。下一轮重复看到这些帖没关系，脚本会按 `code` 去重。

---

## 5. 完整筛选规则

在浏览器里搜到帖子后，先自己丢掉明显的转推，再交给脚本。脚本还会再筛一遍。

### 5.1 要找什么样的帖

- 最近大约 **6 分钟**内的原帖，按时间倒序（搜索页选「最新 / Live」）。
- 不含转推（搜索词带 `-filter:retweets`，看见 `RT @` 或转推标记的直接跳过）。
- 正文里**真正分享了小豆邀请码**，并且当时 **回复数为 0**。
- 出处记原始发帖人，不要记成转发者或评论者。

邀请码形态：

- 通常是 **5–8 位大写字母数字**（也有全是字母的，如 `DNWYWA`、`NDHGED`）。
- 或官方链接：`https://activity.doubao.com/ug/invite-share/{code}`，码里偶尔带连字符，如 `M-342WXG`。
- 页面上的 `t.co` 短链要展开后再看是不是这条官方邀请页（见第 6 节）。

帖子里写了有效期（「有效期至 11 月 26 日 18:35」等），就解析进 `expires`。同一帖若分享了多个码，每个码单独一条，共用同一个 `postId`。

### 5.2 明确不要收

| 类型 | 怎么判断 | 我们遇到过的例子 |
| --- | --- | --- |
| 求码帖 | 「求码」「求一个邀请码」「有没有码」「谁有邀请码」等，是在要码不是在给码 | 只喊「求一个小豆邀请码，有没有多余的」这类帖 |
| 接力楼空帖 | 「占楼」「接力」「路过」，或几乎没有正文、也没有码 | — |
| 卖码广告 | 「卖码」「出码」「代抢」「收费」「闲鱼」「xx 元一个」 | 闲鱼上已有人因卖码被骂骗子，不要沾 |
| 其他产品的码 | 码前后写着其他产品名就不收。即使搜索词命中「邀请码」，也不代表是小豆的 | **`BEGWUM` 是 Muse 的邀请码**，曾经被误收，后来已从网站删掉。码旁边写着 Muse / 即梦 / ChatGPT 等就丢掉 |
| 汇总 / 整理帖 | 「整理了」「汇总」「合集」「收集了」 | **@Lewis6666688888 的整理帖**，作者自己说里面的码是随便改过的、不保证能用，因此没有收录 |
| 声明码被改过 | 「随便改」「随机改」「不保证能用」 | 同上 |
| 第三方码站 | 不要去 BetterOPC 或任何别人的码站搬码 | 用户已要求从网站和巡检里去掉 BetterOPC |
| 已经收录过的码 | 同一 `code` 不重复插入 | 只更新回复数和状态 |

官方 `activity.doubao.com/ug/invite-share/` 链接里解析出的码，可以认定为小豆码（即使帖子别处提到了别的产品）。

### 5.3 收录之后怎么更新状态

- 回访已有原帖，若 `replyCount >= 1`，且当前是 `maybe_available`，改为 `maybe_used`。
- 有人明确确认用掉了，改为 `confirmed_used`，并在 `note` 写证据链接。
- 不要把 `confirmed_used` 改回去。

---

## 6. 用 EGO Lite 浏览器在 x.com 搜索

本地 ORCA bot **不要申请、也不要使用 X API Bearer Token**。用已经登录 X 的 EGO Lite 打开下面这些搜索页即可。

### 6.1 搜索地址（最新、排除转推）

每轮三种关键词都扫一遍，间隔几秒，不要连着打三个请求：

```
https://x.com/search?q=小豆%20邀请码%20-filter:retweets&f=live
https://x.com/search?q=小豆%20码%20-filter:retweets&f=live
https://x.com/search?q=豆包%20小豆%20邀请%20-filter:retweets&f=live
```

要点：

- `f=live` 是「最新」，不要用默认的「热门 / Top」。
- `-filter:retweets` 尽量排除转推，页面上若仍出现转推卡片，自己丢掉。
- 只需看大约最近 6 分钟的帖，**不要拼命下滚**；一般第一屏或轻轻下滚一点就够。
- 读完当前 `codes.json` 里的 `lastSinceId`，时间线里帖子 ID **小于等于**它的，不必当新帖处理（雪花 ID 越大越新）。

### 6.2 从页面上取字段

每条推文通常是一篇 `article`，按下面取：

| 字段 | 怎么取 |
| --- | --- |
| `id` | 帖子链接 `https://x.com/{用户名}/status/{数字ID}` 里的数字，或时间戳那一处的 `href`。这就是雪花 ID。 |
| `author` | 用户名（`@` 后面那串），不要用显示名。 |
| `text` | 帖子正文。把换行保留下来。 |
| `created_at` | `<time>` 的 `datetime` 属性，一般是 UTC，例如 `2026-11-24T12:39:00.000Z`。不要只抄「5 分钟前」这种相对时间。 |
| `reply_count` | 回复（气泡）按钮上的数字；**没有数字就是 0**。这是本轮最关键的数，看清楚再写。点开原帖后数字更可靠。 |
| `url` / `urls` | 原帖链接，外加正文里已经展开的外链（见下）。 |

同一帖在三个搜索结果里重复出现时，只保留一条。

### 6.3 展开 t.co

正文里的 `t.co` 短链，要知道它指向哪里，才能认出官方邀请页。优先用**页面上已经给出的展开信息**，不要另外写一个高频解开短链的爬虫：

- 链接可见文字经常已经是 `activity.doubao.com/ug/invite-share/...`
- `<a>` 的 `title`、可见文本、有时还有 `data-` 属性会带上展开后的 URL
- 仍看不清时，再在**同一个已登录的 EGO Lite** 里轻轻点开或悬停，把跳转后的地址记进 `urls`

官方邀请页形态：

```
https://activity.doubao.com/ug/invite-share/{code}
```

把展开后的地址放进该帖的 `urls`（或 `url`）即可，脚本会抽码。

### 6.4 回访已有「可能可用」的原帖

搜索页几乎不会再刷出几小时前的帖，但那些码的回复数会变。每轮请额外打开 `codes.json` 里 `status` 仍为 `maybe_available` 的 `postUrl`（数量通常不多），把最新 `reply_count` 一并写进 `posts.json`。脚本会用这些记录刷新状态，而**不会**因为它们的 ID 比 `lastSinceId` 小就再插一条新码。

`maybe_used` / `confirmed_used` 不必每轮都回访。

### 6.5 控制频率，降低风控

X 对自动化登录会话很敏感，请当成「每 5 分钟人工刷新一次」来做，而不是爬虫：

- 调度间隔 **不要短于 5 分钟**，更不要 10 秒一轮。
- 三个搜索页之间、回访原帖之间留 **5–15 秒** 空隙，不要并行开一堆标签狂刷。
- 只用已经登录的 EGO Lite 配置文件；**不要把 Cookie、账号密码写进仓库或本仓库的文件**。
- 同时只跑一个巡检实例。
- 若出现验证码、登录墙、「try again later」、异常跳转或功能残缺：**本轮立刻停**，等 30–60 分钟再试，不要自动反复重试。
- 不要用未登录会话、不要用数据中心 IP 去批量解开 `t.co`。

---

## 7. 交给脚本的帖子 JSON

ORCA bot 把上面抓到的帖子写成一个 UTF-8 JSON 文件（例如 `posts.json`），再调用脚本。两种写法都可以：

```json
{
  "posts": [
    {
      "id": "2125176786560676175",
      "author": "tsaielio",
      "text": "小豆邀请码 FG83KC，有效期至11月26日 18:35",
      "created_at": "2026-11-24T10:36:44.000Z",
      "reply_count": 0,
      "url": "https://x.com/tsaielio/status/2125176786560676175",
      "urls": ["https://activity.doubao.com/ug/invite-share/FG83KC"]
    }
  ]
}
```

或者直接是数组：`[ { ... }, { ... } ]`。

字段约定：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 帖子雪花 ID，字符串。也接受 `postId` |
| `author` | 是 | 用户名，不要 `@`。也接受 `username` |
| `text` | 是 | 正文。也接受 `content` |
| `created_at` | 是 | 优先 UTC ISO（`...Z`）。也接受 `YYYY-MM-DD HH:MM:SS 北京时间` |
| `reply_count` | 是 | 整数。也接受 `replyCount` |
| `url` | 否 | 单个链接（原帖或已展开的外链） |
| `urls` | 否 | 已展开的链接列表，可与 `url` 同时给 |
| `is_retweet` | 否 | 若是转推，请标 `true`（即使正文里没有 `RT @`） |

把「新搜到的帖」和「回访 maybe_available 原帖」放在**同一个文件**里即可。

---

## 8. 怎么运行脚本

环境：Windows、Python 3.10+（只用标准库，不必 `pip install`）。仓库里的路径是 `scripts/patrol.py`。

```bat
REM 先设好 PAT（不要写进脚本，也不要提交到 git）
set GITHUB_TOKEN=ghp_或_github_pat_开头的令牌

REM 假设 ORCA 已经写好 posts.json
python scripts\patrol.py --posts posts.json

REM 只看筛选结果、不提交
python scripts\patrol.py --posts posts.json --dry-run

REM 从标准输入读 JSON
python scripts\patrol.py --posts - < posts.json

REM 离线自测（不提交；可改用刚下载的 codes.json）
python scripts\patrol.py --self-test
python scripts\patrol.py --self-test --codes-json codes.json
```

`--dry-run` 且没有 `GITHUB_TOKEN` 时，脚本会读公开的 https://paipiange.github.io/xiaodou-codes/codes.json ，仍然会筛帖、打印新码，但不会写入仓库。

脚本自己会：

1. 丢掉转推、求码、卖码、整理/改码声明、接力空帖、其他产品码；
2. 从正文和已展开链接里抽码，解析有效期；
3. 只把当时 `reply_count == 0` 的分享帖作为新码，插到数组最前；
4. 用回访帖刷新已有记录的回复数和状态；
5. **有变化才**更新 `lastUpdated`、`lastSinceId` 并提交；
6. 把本次新增的 0 回复码打印到控制台。

---

## 9. 必须准备的凭据

只要 **GitHub fine-grained PAT**，不要 X API token。

1. 打开 https://github.com/settings/personal-access-tokens 创建 fine-grained token。
2. Resource owner 选 `paipiange`，仓库只勾 `xiaodou-codes`。
3. 权限： **Contents: Read and write**（Metadata 只读一般会自动带上）。不要开 Pages、Administration、Actions 等多余权限。
4. 把令牌放到机器环境变量 `GITHUB_TOKEN`，或任务计划程序的启动脚本里。**不要写入仓库、HANDOFF、posts.json 或聊天记录。**

脚本提交时会先 `GET /repos/paipiange/xiaodou-codes/contents/codes.json?ref=main` 取 blob `sha`，再 `PUT` 同一路径，body 里带 `sha`、`branch=main`、以及 **Base64** 后的完整文件内容。成功后 Actions 自动部署。

---

## 10. 没有顺利抓到搜索结果时怎么办

这套流程依赖已登录的 EGO Lite。若页面要验证码、掉登录，或 bot 暂时控制不了浏览器：

1. **人工打开**第 6.1 节的三个搜索链接，按最新排序，只看 0 回复的原帖，把字段按第 7 节写成 `posts.json`，再跑 `python scripts\patrol.py --posts posts.json`。半自动和全自动用的是同一份脚本。
2. 不要改去调用 X API，也不要去第三方码站搬码。
3. 恢复登录或风控解除后再把自动巡检接上。

---

## 11. Windows 每 5 分钟定时运行

用「任务计划程序」即可，不要用忙等循环。

1. 写一个启动脚本，例如 `C:\xiaodou-codes\run-patrol.bat`（路径按你本机改）。这个 bat 只负责：唤起 ORCA bot（让它用 EGO Lite 搜完并写出 `posts.json`），然后调用 `python scripts\patrol.py --posts posts.json`。`GITHUB_TOKEN` 在 bat 或系统环境变量里设置，不要勾选「把密码写进仓库」。
2. 打开「任务计划程序」→ 创建基本任务：
   - 触发器：每隔 **5 分钟**；也可先「每天」再在触发器里改成重复任务、间隔 5 分钟、持续时间无限或到 2026-11-26 16:30。
   - 操作：启动上述 bat。
   - 勾选「只有在用户登录后运行」更稳妥（EGO Lite 登录态通常在交互式会话里）。
3. 同一时间只允许一个实例：若上一轮还在跑，跳过本轮，避免两个浏览器抢同一个 X 会话。
4. 把标准输出记到日志文件，方便回看新码通知。

命令行参考（可按本机路径修改）：

```bat
schtasks /create /tn "XiaodouPatrol" /sc minute /mo 5 /tr "C:\xiaodou-codes\run-patrol.bat"
```

---

## 12. 冲突与部署失败

**sha 冲突：** 若 `PUT` 返回 HTTP 409，说明别人（或上一轮）刚改过 `codes.json`。`patrol.py` 会重新 `GET` 最新 sha 和内容，把本次新码和回复数再合并一次，最多重试 3 次。不要自己盲用旧 sha 硬提交。

**Pages 偶发失败：** 部署最后一步有时会报刚上传的 artifact 找不到，属于 GitHub 偶发问题，不是 `codes.json` 写坏了。现有 `pages.yml` 已在「Deploy to GitHub Pages」失败后等待并重试两次（中间分别 `sleep 30`、`sleep 60`）。若三次都失败，下一轮巡检再提交一次就会重新部署，网站最多晚几分钟，不必改工作流。

---

## 13. 搜索引擎与统计（现状）

这些已经做好，交接后不必重做，除非页面域名变了。

- **不蒜子**：页脚显示访问次数和人数，脚本为 `https://busuanzi.ibruce.info/busuanzi/2.3/busuanzi.pure.mini.js`。没有后台。**在部分 iOS 浏览器上计数可能不显示**，这是该服务的限制。
- **Google Search Console**：站点首页保留了 HTML 验证标记。sitemap 地址为 `https://paipiange.github.io/xiaodou-codes/sitemap.xml`；如需查看索引数据，请在自己的 Search Console 账户中验证新站点并提交该 sitemap。
- **Bing / IndexNow**：已用 IndexNow 提交过，接口返回 202。公开 key 文件是仓库根目录的 `12fb0e8f8c287ee46be0571f729ca8df.txt`（内容与文件名一致），不是密钥。若要在 Bing 后台看数据，可到 [Bing Webmaster Tools](https://www.bing.com/webmasters/) 用「从 Google Search Console 导入」。现行巡检**不会**在每次更新 `codes.json` 后再 ping 一次 IndexNow，需要的话可另行决定。

---

## 14. 已知未修问题

- 手机上的码表要左右滑动才能看到回复数和状态。若要改成卡片式布局，需要改 `index.html`（本交接默认不动它）。
- 不蒜子计数在部分 iOS 设备上可能空白。
- 回复数依赖 bot 回访 `maybe_available` 原帖；若某一轮漏回访，状态会晚几轮才变成「可能已用」。

---

## 15. 交接清单（你需要准备什么）

1. 一台已登录 X 的 Windows 电脑，EGO Lite 里 X 会话可用。
2. Python 3.10+（标准库即可）。
3. 本仓库的一份本地拷贝，至少要有 `scripts/patrol.py`。
4. 只开通 `xiaodou-codes` 的 Contents 读写的 GitHub fine-grained PAT，放入环境变量 `GITHUB_TOKEN`。
5. 用任务计划程序每 5 分钟跑：浏览器搜索 → 写 `posts.json` → `python scripts\patrol.py --posts posts.json`。
6. 第一次先跑 `python scripts\patrol.py --self-test`，再跑一次 `--posts ... --dry-run`，确认控制台输出无误后再去真实提交。
7. 跑之前重新读取线上 `codes.json` 的 `lastSinceId`，不要沿用本文里的快照值。

文档和脚本在 GitHub 上的位置：

- https://github.com/paipiange/xiaodou-codes/blob/main/HANDOFF.md
- https://github.com/paipiange/xiaodou-codes/blob/main/scripts/patrol.py
