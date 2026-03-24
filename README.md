# 飞书 ↔ Kiro CLI 桥接服务（WebSocket 长连接版）

在飞书中 @机器人 发消息，自动调用 Kiro CLI 处理并回复结果。

**无需公网 IP、无需端口开放、无需 nginx 反向代理。**

## 架构

```
飞书用户 @Bot "分析一下成本"
       ↓
飞书云 ←—WebSocket 长连接—→ 本服务 (Python)
                                ↓ subprocess
                           kiro-cli chat --prompt "分析一下成本"
                                ↓
                           本服务收到结果
                                ↓ 飞书 SDK reply
                           飞书用户收到回复
```

服务主动向飞书建立 WebSocket 出站连接，飞书通过该连接推送事件。连接方向是出站的，所以：
- 不需要公网 IP
- 不需要开放端口
- 不需要处理飞书 IP 白名单
- 天然穿透 NAT / 防火墙

## 部署步骤

### 第一步：飞书开放平台创建应用

1. 打开 https://open.feishu.cn/app 登录
2. 点击「创建企业自建应用」
3. 记录 `App ID` 和 `App Secret`

### 第二步：添加机器人能力 & 权限

1. 应用详情 → 「添加应用能力」→ 选择「机器人」
2. 「权限管理」→ 开通：
   - `im:message` — 获取与发送消息
   - `im:message:send_as_bot` — 以应用身份发送消息

### 第三步：配置事件订阅（关键！）

1. 应用详情 → 「事件与回调」→ 「事件配置」
2. **接收方式选择「使用长连接接收事件」**（不是 Webhook URL）
3. 添加事件：`im.message.receive_v1`（接收消息）

### 第四步：发布应用

1. 「版本管理与发布」→ 创建版本 → 提交审核 → 发布

### 第五步：配置本服务

```bash
cd /home/ubuntu/feishu-kiro-bot
cp .env.example .env
vim .env   # 填入 APP_ID、APP_SECRET
```

### 第六步：启动服务

```bash
# 前台运行（调试用）
./start.sh

# systemd 后台运行（生产用）
sudo cp feishu-kiro-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable feishu-kiro-bot
sudo systemctl start feishu-kiro-bot
```

## 依赖

```bash
pip3 install lark-oapi
```

## 查看日志

```bash
sudo journalctl -u feishu-kiro-bot -f
```

## 常见问题

**Q: 连接不上飞书？**
A: 确认事件订阅已切换为「使用长连接接收事件」，且应用已发布。

**Q: 机器人不回复？**
A: 检查日志，确认 kiro-cli 可正常运行：`kiro-cli chat --prompt "hello"`

**Q: 断线会自动重连吗？**
A: 会。飞书 SDK 内置自动重连机制。
