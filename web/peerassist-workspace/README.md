# PeerAssist Workspace 前端

这是 PeerAssist 论文审核辅助系统的前端工作台，使用 React、TypeScript、Vite、PDF.js 和 lucide-react 构建。它负责 PDF 原文浏览、文字选择、智能审稿入口、证据队列、工具追踪、人工确认和产物导出。

## 开发启动

先在仓库根目录启动后端：

```bash
PYTHONPATH=src \
PEERASSIST_OPENAI_API_KEY="<your-runtime-key>" \
PEERASSIST_OPENAI_BASE_URL="https://deepkey.top/v1" \
PEERASSIST_OPENAI_MODEL="gpt-5.4" \
.venv/bin/python -m peerassist.confirmation_server \
  --run-dir runs/arxiv_real_data/runs/arxiv_2607_08522_v1 \
  --paper-id arxiv_2607_08522_v1 \
  --host 0.0.0.0 \
  --port 8766
```

再启动前端：

```bash
cd web/peerassist-workspace
npm install
npm run dev
```

`vite.config.ts` 会把以下路径代理到后端 `http://127.0.0.1:8766`：

- `/api`
- `/paper.pdf`
- `/legacy`

## 构建

```bash
npm run build
```

构建产物输出到 `dist`。后端服务会以 `/workspace/` 路径提供这些静态文件，因此生产访问通常只需要打开后端服务地址。

## 主要源码

| 文件 | 说明 |
| --- | --- |
| `src/main.tsx` | React 应用、状态流、PDF 工作台和审稿交互 |
| `src/styles.css` | 现代智能体工作台样式 |
| `vite.config.ts` | Vite base 路径与后端代理 |
| `package.json` | 前端依赖和脚本 |

## 安全约定

前端只展示模型是否已配置、模型名和 Base URL，不保存或显示真实 API Key。真实密钥只允许通过后端运行时环境变量注入。
