# cc2go Implementation Plans

- [MCP工具短路](2026-05-17-mcp-bypass.md) — web_search等MCP工具直接处理不走LLM后端
- [多后端Key轮询](2026-05-17-key-rotation.md) — 支持逗号分隔多Key和roundrobin/random策略
- [Streaming规范化](2026-05-17-streaming-normalization.md) — SSE流式响应转换器统一Anthropic事件格式
- [错误自适应](2026-05-17-error-resilience.md) — 错误分类、指数退避、模型fallback