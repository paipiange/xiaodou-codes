# 小豆邀请码抓码站

社区收集的豆包「小豆」邀请码，来自 X 上的公开分享帖。**这不是豆包或字节跳动的官方网站。**

- 站点：https://paipiange.github.io/xiaodou-codes/
- 数据：根目录的 [`codes.json`](./codes.json)

## 更新数据

巡检只需改 `codes.json`，不必改 `index.html`。页面每 60 秒会重新拉取该文件。

字段说明：

- `lastUpdated`：本次写入时间，须标注「北京时间」
- `lastSinceId`：下次在 X 搜索时使用的 `since_id`
- `codes`：邀请码数组，新码放在最前面。同一 `code` 不要重复，只更新 `replyCount` / `status` / `note`
- `status` 取值：`maybe_available`（可能可用）、`maybe_used`（可能已用）、`confirmed_used`（已确认被用）

「我试过了，不能用」只保存在访客浏览器的 localStorage 里，不要写入 `codes.json`。
