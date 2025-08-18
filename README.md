# Tron 能量查找机器人

这是一个用于查找低成本 Tron 能量代理地址的 Telegram 机器人。机器人可以自动查找并推送最新的低价能量代理地址信息。

## 演示
telegram机器人：https://t.me/lowtronbot
telegram频道：https://t.me/lowtron

### 提醒
机器人通过块查找相关地址，查到的地址中，可能有一些地址有白名单设置，需要在他的机器人提交地址的才能获得能量，否则可能转TRX无法获得能量。

## 功能特点

1. **私聊功能**
   - `/start` - 开始使用机器人
   - `/help` - 查看帮助信息
   - `/query` - 立即查询低价能量地址

2. **频道/群组功能**
   - `/start_push` - 开启定时推送（仅管理员可用）
   - `/stop_push` - 关闭定时推送（仅管理员可用）
   - `/query` - 立即查询一次
   - 开启推送后每小时自动推送最新地址

3. **黑名单功能（优化）**
   - `/blacklist_add <地址> [原因]` - 添加地址到黑名单
   - `/blacklist_check <地址>` - 查询地址黑名单状态
   - `/blacklist_remove <地址>` - 从黑名单移除地址（仅管理员）
   - `/blacklist_stats` - 查看黑名单统计信息
   - 自动检测用户发送的TRON地址并提示风险
   - 查询结果中显示黑名单警告信息
   - 仅保留“能量提供方 → 收款地址”的单向关联（默认开启，可用 `/assoc off` 关闭；`/assoc status` 查看状态）
   - 新增“临时黑名单”概念：1票用户反馈即可生效并标注“（临时）”，方便小流量场景快速沉淀信息（支持24小时内撤回功能预留）

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
   - 频道场景采用“轻量投票”：点击即记录并回执（无需与机器人私聊）

6. **文件自动清理功能**
   - 自动清理过期的查询结果文件
   - 默认保留最近7天的历史记录
   - 防止 results 文件夹无限累积文件
   - 支持手动清理和自定义保留期限

7. **管理员设置**
   - 黑名单单向关联开关：`/assoc on` 开启、`/assoc off` 关闭、`/assoc status` 查看
   - 默认开启单向关联，仅当“能量提供方”在黑名单时，才会传播到“收款地址”（反向不传播）

## 环境要求

- Python 3.8 或更高版本
- 操作系统：Windows/Linux/MacOS

## 安装步骤

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
     
     # 广告内容（可选）
     BOT_ADVERTISEMENT=your_advertisement_here
     
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
     2. 使用 `/query` 立即查询地址
   
   - 频道/群组使用：
     1. 将机器人添加到频道/群组并设置为管理员
     2. 使用 `/start_push` 开启定时推送
     3. 使用 `/stop_push` 关闭定时推送
     4. 使用 `/query` 立即查询一次
   
   - 名单功能与交互按钮使用：
     1. 发送 `/blacklist_add TXxxxxxxxx 原因` 添加可疑地址
     2. 直接发送TRON地址到机器人，自动检查白/黑名单状态并显示分层提示
     3. 在结果消息下点击：
        - ✅ 我已成功获得能量（两者加入白名单，临时）
        - ❌ 我未获得能量（两者加入黑名单，临时）
        - ▶️ 更多操作（按需仅对“收款地址”或“提供方”加白/加黑，或撤回；撤回功能将于后续版本开放）
     4. 管理员可使用 `/blacklist_remove`、`/whitelist_remove` 移除误报地址
     5. 管理员可用 `/assoc on|off|status` 控制“提供方→收款地址”的自动关联

## 注意事项

1. **机器人权限**：确保机器人具有发送消息的权限
2. **管理员权限**：频道/群组中需要将机器人设置为管理员
3. **推送控制**：只有管理员可以控制定时推送功能
4. **API限制**：TronScan API 请求有频率限制，建议配置多个API Key
5. **数据库配置**：功能需要配置 PostgreSQL 连接，推荐使用 Supabase
6. **白名单优先**：组合白名单会覆盖黑名单警告；仅单方白名单时将与黑名单同时展示并给出综合建议
7. **临时状态**：1票反馈即生效并标注“（临时）”，后续可由更多反馈或管理员操作转为正式；撤回功能将于后续版本开放

## 技术支持

如有问题，请提交 Issue 或联系开发者。

## 许可证

MIT License 
