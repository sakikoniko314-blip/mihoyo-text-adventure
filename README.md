# 米哈游文字冒险

基于 DeepSeek AI 的互动文字冒险游戏，支持原神和崩坏：星穹铁道世界。

## 特性

- **双 AI 架构**：叙事 AI + 选项 AI 独立调用，选项质量高且永远不会缺失
- **智能结局**：AI 判断剧情终点，触发终章叙事
- **RAG 世界观**：基于游戏数据的角色/地点/阵营信息检索
- **打字机效果**：叙事逐字显现，沉浸感强
- **免费游玩**：每 IP 40 轮免费额度，也可用自己的 DeepSeek API Key
- **本地存档**：浏览器 localStorage 保存/加载进度
- **支持世界**：原神（GI）、崩坏：星穹铁道（HSR）

## 快速开始

### 依赖
- Python 3.6+
- aiohttp, httpx

```bash
pip install aiohttp httpx
```

### 配置

创建 `adventure_config.json`：

```json
{
  "deepseek": {
    "api_key": "sk-your-key-here",
    "model": "deepseek-v4-flash",
    "temperature": 0.3,
    "max_tokens": 1600
  },
  "admin_key": "your-admin-password"
}
```

或设置环境变量 `DEEPSEEK_API_KEY` 和 `ADMIN_KEY`。

### 运行

```bash
python3 adventure_server.py
# 访问 http://localhost:8888
```

### 数据准备

需要 `world_info.json`（角色/地点/阵营数据）和 `character_personas.json`（角色人设）。运行：

```bash
python3 build_characters.py   # 从 data/ 生成 world_info.json
python3 build_personas.py     # 生成 character_personas.json
```

### 管理页面

访问 `/arc-5813f?key=<admin_key>` 查看访客配额。

## 文件结构

```
adventure_server.py      # 主服务端（aiohttp）
adventure_index.html     # 前端单页（零依赖）
build_characters.py      # 构建角色数据
build_personas.py        # 构建人设数据
world_info.json          # 角色/地点/阵营索引
character_personas.json  # 角色人设摘要
```

## License

MIT
