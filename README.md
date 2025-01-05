# Tron 能量查找机器人

这是一个用于查找低成本 Tron 能量代理地址的 Telegram 机器人。机器人可以自动查找并推送最新的低价能量代理地址信息。

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
     TRON_API_KEY=your_tron_api_key_here
     TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
     ```

## 使用说明

1. **获取 API Keys**
   - TronScan API Key：访问 https://tronscan.org/#/developer/api 申请
   - Telegram Bot Token：与 @BotFather 对话创建机器人并获取 token

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

## 注意事项

1. 确保机器人具有发送消息的权限
2. 频道/群组中需要将机器人设置为管理员
3. 只有管理员可以控制定时推送功能
4. API 请求可能有频率限制，请合理使用

## 技术支持

如有问题，请提交 Issue 或联系开发者。

## 许可证

MIT License 