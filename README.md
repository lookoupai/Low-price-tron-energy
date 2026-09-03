# Tron 能量查找机器人

这是一个用于查找低成本 Tron 能量代理地址的 Telegram 机器人。机器人可以自动查找并推送最新的低价能量代理地址信息。当前默认筛选区间为 **0.01-1 TRX**。

## 演示
telegram机器人：https://t.me/lowtronbot
telegram频道：https://t.me/lowtron

### 提醒
机器人通过块查找相关地址，查到的地址中，可能有一些地址有白名单设置，需要在他的机器人提交地址的才能获得能量，否则可能转TRX无法获得能量。

## 功能特点

1. **私聊功能**
   - `/start` - 开始使用机器人
   - `/help` - 查看帮助信息
   - `/query` - 查找低价能量地址（默认 0.01-1 TRX，最多 3 条）
   - `/query 0.01` - 精确查找 0.01 TRX
   - `/query 0.01-0.1` - 查找 0.01-0.1 TRX
   - `/myid` - 查看自己的 Telegram 用户 ID（用于填写 `ADMIN_USER_IDS`）

2. **频道/群组功能**
   - `/start_push` - 开启定时推送（默认 0.01-1 TRX，仅管理员可用）
   - `/start_push 0.01` - 按精确价格推送
   - `/start_push 0.01-0.1` - 按价格区间推送
   - `/stop_push` - 关闭定时推送（仅管理员可用）
   - `/query` - 立即查询一次
   - `/channels` - 查看已启用推送的频道及其价格筛选
   - 订阅写入数据库，容器重启后不会丢失
   - 开启推送后每小时自动推送最新地址（最多 3 条，6 小时内不重复推同一收款地址）

3. **黑名单功能（优化）**
   - `/blacklist_add <地址> [原因]` - 添加地址到黑名单
   - `/blacklist_check <地址>` - 查询地址黑名单状态
   - `/blacklist_remove <地址>` - 从黑名单移除地址（仅管理员）
   - `/blacklist_stats` - 查看黑名单统计信息
   - 自动检测用户发送的TRON地址并提示风险
   - 查询结果中显示黑名单警告信息
   - 仅保留“能量提供方 → 收款地址”的单向关联（默认开启，可用 `/assoc off` 关闭；`/assoc status` 查看状态）
   - “临时黑名单”：1 票反馈即生效并标注“（临时）”，同向票数达到 2 票后转为正式

4. **白名单功能（新增）**
   - `/whitelist_add <地址> <payment|provider> [原因]` - 将收款地址或能量提供方加入白名单（临时）
   - `/whitelist_check <地址> <payment|provider>` - 查询白名单状态
   - `/whitelist_remove <地址> <payment|provider>` - 从白名单移除（仅管理员）
   - `/whitelist_stats` - 查看白名单统计（单地址/组合数量）
   - 白名单优先于黑名单：
     - 若“收款地址+能量提供方”组合在白名单，则不显示黑名单警告，提示“曾有人成功获得能量租凭，因此已加入白名单”
     - 若仅一方在白名单，则提示“曾有人通过该【收款地址/能量提供方】成功，但不是当前组合”

5. **交互按钮（频道与私聊通用）**
   - 每条检索结果下方提供清晰可理解的按钮：
     - ✅ 我已成功获得能量（两者加入白名单，临时）
     - ❌ 我未获得能量（两者加入黑名单，临时）
     - ▶️ 更多操作（展开：仅收款地址成功/仅提供方成功/仅收款地址有问题/仅提供方有问题/撤回/取消）
   - 按钮说明：成功=两者加白；未成功=两者加黑；更多=展开单独添加/撤回
   - 频道场景采用"轻量投票"：点击即记录并回执（无需与机器人私聊）
   - 投票按人记录：同一用户重复点击只覆盖不叠加，改点反向按钮即改票
   - 撤回已可用：投票后 24 小时内可点“撤回”撤销自己的票，撤回后重算名单状态，票数归零则移除条目
   - 票数阈值：同向 1 票为“临时”，达到 2 票转正式；黑白票同时存在时展示双方票数，不再单向覆盖

6. **文件自动清理功能**
   - 自动清理过期的查询结果文件
   - 默认保留最近7天的历史记录
   - 防止 results 文件夹无限累积文件
   - 支持手动清理和自定义保留期限

7. **管理员设置**
   - 黑名单单向关联开关：`/assoc on` 开启、`/assoc off` 关闭、`/assoc status` 查看
   - 默认开启单向关联，仅当"能量提供方"在黑名单时，才会传播到"收款地址"（反向不传播）

8. **数据管理与维护功能**
   - **查看数据状态**：
     ```bash
     python verify_associations.py                    # 基本统计信息
     python verify_associations.py --detailed         # 详细信息（最近记录）
     python verify_associations.py --export          # 导出数据到CSV
     ```
   - **备份数据**：
     ```bash
     python clear_associations.py --backup-only      # 仅备份，不清理
     ```
   - **清理关联数据**：
     ```bash
     python clear_associations.py --clear-all        # 清空所有关联数据重新开始
     python clear_associations.py --clear-associations-only  # 只清空关联表
     ```
   - **恢复数据**：
     ```bash
     python clear_associations.py --restore <备份文件>  # 从备份恢复
     ```
   - **预览操作**：在任何命令后添加 `--dry-run` 可预览操作而不实际执行

## 环境要求

- Docker / Docker Compose（推荐）
- 或 Python 3.8+（不使用 Docker 时）
- PostgreSQL：默认用远程库（如 Supabase）；也可用本仓库自带 Postgres

## 推荐：用 GitHub 镜像运行

推送到 `main` 后，GitHub Actions 会构建并发布：

`ghcr.io/lookoupai/low-price-tron-energy:latest`

`docker compose up -d` **默认拉取这个镜像**，不会在本机构建。

1. 复制配置并填写密钥：

   ```bash
   cp .env.example .env
   ```

   必填：
   - `TRON_API_KEY_1`：https://tronscan.org/#/developer/api
   - `TELEGRAM_BOT_TOKEN`：找 `@BotFather` 创建机器人
   - `DATABASE_URL`：远程 PostgreSQL 连接串（推荐继续用 Supabase）

   可选：
   - `ADMIN_USER_IDS`：管理员用户 ID，逗号分隔，例如 `123456789,987654321`。
     管理命令（`/start_push`、`/stop_push`、`/blacklist_remove`、`/whitelist_remove`、`/assoc`、`/channels`）
     在私聊里只对名单内用户开放；**留空则私聊管理命令对所有人关闭**。
     在群组/频道里仍按 Telegram 自身的管理员身份判断。
     不知道自己的 ID 就先私聊机器人发 `/myid`。
   - `MIN_TRX_AMOUNT` / `MAX_TRX_AMOUNT`：筛选区间，默认 `0.01` / `1`
   - `TZ`：时区，默认 `Asia/Shanghai`（查询/推送时间和日志都用这个时区，不是 UTC）
   - `BOT_ADVERTISEMENT`：消息底部广告。内容里有 `@` 时必须加引号，例如 `"查询机器人 @lowtronbot"`

2. 如果 GHCR 包是私有的，先登录再启动：

   ```bash
   echo "$GITHUB_TOKEN" | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
   docker compose up -d
   docker compose logs -f bot
   ```

   公开包可直接 `docker compose up -d`。

3. 停止：

   ```bash
   docker compose down
   ```

说明：
- Compose 默认只启动 `bot`，数据库用 `.env` 里的远程 `DATABASE_URL`
- 部分云数据库只有 IPv6，本机 Docker 桥接网络可能连不上，所以 bot 使用 `network_mode: host`
- 容器时区默认 `Asia/Shanghai`，查询/推送消息里的时间和日志都按上海时间显示
- 黑名单/白名单/推送订阅表会在首次使用时自动创建
- 查询结果保存在 Docker 卷 `results_data`

## 本地改代码时构建镜像

开发或还没推送到 GitHub 时，才需要本机构建：

```bash
docker compose up -d --build
```

指定本机镜像、且不要每次都去拉 GHCR：

```bash
IMAGE_NAME=low-price-tron-energy:local PULL_POLICY=never docker compose up -d --build
```

## 使用本地 Postgres

没有远程库时：

```bash
docker compose --profile local-db up -d
```

同时把 `.env` 的 `DATABASE_URL` 改成：

```
DATABASE_URL=postgresql://tron:tron_energy_pass@127.0.0.1:5432/tron_energy
```

因为 bot 走 host 网络，这里要用 `127.0.0.1`，不能用 compose 服务名 `db`。

## GitHub 镜像发布

工作流：`.github/workflows/docker-publish.yml`

- 触发：推送 `main`、打 `v*` 标签、手动运行
- 镜像：`ghcr.io/lookoupai/low-price-tron-energy:latest`

仓库需要打开 Packages 写入权限：Settings → Actions → General → Workflow permissions → Read and write。

## 本地 Python 运行

1. 克隆代码仓库：
   ```bash
   git clone <repository_url>
   cd <repository_name>
   ```

2. 安装依赖：
   ```bash
   pip install -r requirements.txt
   ```

3. 配置环境变量：
   - 复制 `.env.example` 为 `.env`
   - 在 `.env` 文件中填入必要的配置信息：
     ```
     # TronScan API Keys
     TRON_API_KEY_1=your_first_api_key_here
     TRON_API_KEY_2=your_second_api_key_here

     # Telegram Bot Token
     TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here

     # 管理员用户 ID（逗号分隔，留空则私聊管理命令对所有人关闭）
     # 用 /myid 查自己的 ID
     ADMIN_USER_IDS=

     # 时区（默认上海时间）
     TZ=Asia/Shanghai

     # 广告内容（可选，含 @ 时必须加引号）
     BOT_ADVERTISEMENT="查询机器人 @lowtronbot"

     # 筛选区间（默认 0.01-1 TRX）
     MIN_TRX_AMOUNT=0.01
     MAX_TRX_AMOUNT=1

     # 数据库连接（黑名单功能）
     DATABASE_URL=postgresql://username:password@host:port/database
     ```

4. 初始化数据库（可选）：
   ```bash
   python init_database.py
   ```
   注意：如果不手动初始化，机器人会在首次使用黑名单功能时自动创建数据库表。

## 使用说明

1. **获取必要的配置信息**
   - **TronScan API Keys**：访问 https://tronscan.org/#/developer/api 申请（建议申请多个以提高限额）
   - **Telegram Bot Token**：与 @BotFather 对话创建机器人并获取 token
   - **数据库连接**：推荐使用 Supabase 免费 PostgreSQL 数据库（https://supabase.com）

2. **启动机器人**
   ```bash
   python telegram_bot.py
   ```

3. **使用机器人**
   - 私聊使用：
     1. 直接向机器人发送命令
     2. 使用 `/query` 查询默认区间，或 `/query 0.01` / `/query 0.01-0.1` 指定价格
   
   - 频道/群组使用：
     1. 将机器人添加到频道/群组并设置为管理员
     2. 使用 `/start_push`、`/start_push 0.01` 或 `/start_push 0.01-0.1` 开启定时推送
     3. 使用 `/stop_push` 关闭定时推送
     4. 使用 `/query` 立即查询一次
     5. 使用 `/channels` 查看已启用推送的频道及其价格筛选
     6. 拉机器人进频道不会自动开推送，需管理员手动执行 `/start_push`
   
   - 名单功能与交互按钮使用：
     1. 发送 `/blacklist_add TXxxxxxxxx 原因` 添加可疑地址
     2. 直接发送TRON地址到机器人，自动检查白/黑名单状态并显示分层提示
     3. 在结果消息下点击：
        - ✅ 我已成功获得能量（两者加入白名单，临时）
        - ❌ 我未获得能量（两者加入黑名单，临时）
        - ▶️ 更多操作（按需仅对“收款地址”或“提供方”加白/加黑，或撤回；撤回功能将于后续版本开放）
     4. 管理员可使用 `/blacklist_remove`、`/whitelist_remove` 移除误报地址
     5. 管理员可用 `/assoc on|off|status` 控制"提供方→收款地址"的自动关联
   
   - 数据管理使用：
     1. 使用 `python verify_associations.py` 查看当前关联数据状态
     2. 如需清空重新开始，先用 `python clear_associations.py --backup-only` 备份
     3. 使用 `python clear_associations.py --clear-all` 清空所有关联数据
     4. 重启机器人，新的关联将从此开始积累
     5. 如需恢复，使用 `python clear_associations.py --restore <备份文件>`

## 注意事项

1. **机器人权限**：确保机器人具有发送消息的权限
2. **管理员权限**：频道/群组中需要将机器人设置为管理员
3. **推送控制**：只有管理员可以控制定时推送功能
4. **API限制**：TronScan API 请求有频率限制，建议配置多个API Key
5. **数据库配置**：功能需要配置 PostgreSQL 连接，推荐使用 Supabase
6. **白名单优先**：组合白名单会覆盖黑名单警告；仅单方白名单时将与黑名单同时展示并给出综合建议
7. **临时状态**：1票反馈即生效并标注“（临时）”，后续可由更多反馈或管理员操作转为正式；撤回功能将于后续版本开放
8. **时区**：Docker 默认使用 `Asia/Shanghai`。如果消息时间差 8 小时，检查容器环境变量 `TZ`

## 技术支持

如有问题，请提交 Issue 或联系开发者。

## 许可证

MIT License 
