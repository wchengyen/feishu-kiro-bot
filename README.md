# 飞书 ↔ Kiro CLI 桥接服务

在飞书中 @机器人 发消息，自动调用 Kiro CLI 处理并回复结果。

## 架构

```
飞书用户 @Bot "分析一下成本"
       ↓
飞书开放平台 (事件订阅)
       ↓ POST /webhook/event
本服务 (Flask, port 9800)
       ↓ subprocess
kiro-cli chat --prompt "分析一下成本"
       ↓
本服务收到结果
       ↓ POST /im/v1/messages/:id/reply
飞书用户收到回复
```

## 部署步骤

### 第一步：飞书开放平台创建应用

1. 打开 https://open.feishu.cn/app 登录
2. 点击「创建企业自建应用」
3. 填写应用名称（如 "Kiro Assistant"）和描述
4. 进入应用 → 「凭证与基础信息」，记录：
   - `App ID`
   - `App Secret`

### 第二步：添加机器人能力

1. 应用详情 → 「添加应用能力」→ 选择「机器人」
2. 进入「权限管理」→ 搜索并开通以下权限：
   - `im:message` — 获取与发送单聊、群组消息
   - `im:message:send_as_bot` — 以应用身份发送消息
   - `im:chat:readonly` — 获取群组信息

### 第三步：配置事件订阅

1. 应用详情 → 「事件与回调」→ 「事件配置」
2. 请求地址填写：`http://<你的服务器IP>:9800/webhook/event`
3. 记录页面上的 `Verification Token`
4. 添加事件：搜索 `im.message.receive_v1`（接收消息）并订阅
5. **注意**：配置回调地址时飞书会发送 challenge 验证，需要服务已启动

### 第四步：配置本服务

```bash
cd /home/ubuntu/feishu-kiro-bot
cp .env.example .env
vim .env   # 填入 APP_ID、APP_SECRET、VERIFICATION_TOKEN
```

### 第五步：启动服务

```bash
# 方式 A：前台运行（调试用）
./start.sh

# 方式 B：systemd 后台运行（生产用）
sudo cp feishu-kiro-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable feishu-kiro-bot
sudo systemctl start feishu-kiro-bot
sudo systemctl status feishu-kiro-bot
```

### 第六步：验证回调地址

1. 确保服务已启动且端口 9800 可从公网访问
2. 回到飞书开放平台 → 「事件与回调」→ 保存请求地址
3. 飞书会发送 challenge 请求，服务自动响应

### 第七步：发布应用

1. 应用详情 → 「版本管理与发布」→ 创建版本
2. 提交审核（企业内部应用通常自动通过）
3. 发布后，在飞书中搜索机器人名称即可使用

### 第八步：测试

在飞书中：
- 私聊机器人：直接发消息
- 群聊中：@机器人 + 你的问题

## 网络要求

服务器需要：
- 端口 9800 对公网开放（飞书回调需要）
- 能访问 `open.feishu.cn`（调用飞书 API）
- 如果服务器在内网，需要用 nginx 反向代理或 frp 内网穿透

### Nginx 反向代理示例

```nginx
server {
    listen 443 ssl;
    server_name kiro-bot.yourdomain.com;

    ssl_certificate     /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location /webhook/ {
        proxy_pass http://127.0.0.1:9800;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 130s;
    }
}
```

飞书回调地址改为：`https://kiro-bot.yourdomain.com/webhook/event`

## 查看日志

```bash
# systemd 方式
sudo journalctl -u feishu-kiro-bot -f

# 前台方式直接看终端输出
```

## 常见问题

**Q: 飞书提示回调地址验证失败？**
A: 确保服务已启动、端口可访问、.env 配置正确

**Q: 机器人不回复？**
A: 检查日志，确认 kiro-cli 可正常运行：`kiro-cli chat --prompt "hello"`

**Q: 回复太慢？**
A: 调整 KIRO_TIMEOUT，复杂任务建议提示用户耐心等待
