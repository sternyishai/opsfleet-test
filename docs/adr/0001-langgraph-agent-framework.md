# LangGraph as the agent framework

The agent is built on LangGraph. The hardest requirements map directly onto its primitives: human-in-the-loop `interrupt()`/resume for destructive-op confirmation (Req 3), bounded retry loops as explicit graph edges for SQL self-correction (Req 5), per-conversation checkpointing, and per-node tracing via OpenTelemetry to Phoenix, Langfuse or LangSmith (Req 7). It is model-agnostic, so Gemini is the default without lock-in. The choice rests on these requirements. The author's experience (hands-on with LangGraph, none with the alternatives) is stated as a delivery risk in `docs/hld/10`, not as a reason for the choice.

## Considered Options

- **Google ADK**: Gemini-native and the strongest runner-up, but younger with a smaller ecosystem, and human-in-the-loop resume is more manual.
- **PydanticAI**: excellent typed tools, but less mature multi-step orchestration and no built-in durable interrupt.
- **Hand-rolled loop**: total control, but we would re-implement checkpointing, interrupts and tracing ourselves.
