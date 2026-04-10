# 每日日记报告

每天凌晨 2:00 自动从 Notion 拉取前一天的所有日记数据，生成结构化报告，发送到 Get 笔记。

## 报告内容

- ☁️ 天气（城市、气候、气温、湿度、风速、紫外线）
- 😴 睡眠（时长、质量、能量水平、梦境）
- ✅ 任务与事件
- 🎧 播客（筛选收听超过 10 分钟的，显示进度和摘要）
- 📚 阅读（书籍、划线笔记）
- 🎬 影视
- ☀️ 成功日记
- 💗 感恩日记
- 📝 文章与输入
- 🏃 健康与运动
- 💰 每日收支
- 📍 位置轨迹
- 💡 今日领悟

## 配置

### GitHub Secrets

| Secret | 说明 |
|--------|------|
| `NOTION_TOKEN` | Notion Integration Token |
| `GETNOTE_API_KEY` | Get 笔记 API Key |
| `GETNOTE_CLIENT_ID` | Get 笔记 Client ID |

### 本地测试

```bash
NOTION_TOKEN=your_token \
GETNOTE_API_KEY=your_key \
GETNOTE_CLIENT_ID=your_client_id \
python generate_report.py 2026-04-10
```

## License

MIT
