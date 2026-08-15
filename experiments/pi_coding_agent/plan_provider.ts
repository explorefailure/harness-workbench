import {
  createAssistantMessageEventStream,
  type AssistantMessage,
  type Context,
  type Model,
  type SimpleStreamOptions,
} from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const STEPS = [
  { id: "hwb-plan-read", name: "read", arguments: { path: "seed.txt" } },
  { id: "hwb-plan-safe-bash", name: "bash", arguments: { command: "test -f seed.txt" } },
  {
    id: "hwb-plan-direct-write",
    name: "write",
    arguments: { path: "direct.txt", content: "direct plan-mode effect\n" },
  },
  {
    id: "hwb-plan-shell-write",
    name: "bash",
    arguments: { command: "printf 'shell plan-mode effect\\n' > shell.txt" },
  },
] as const;

function emptyUsage() {
  return {
    input: 0,
    output: 0,
    cacheRead: 0,
    cacheWrite: 0,
    totalTokens: 0,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
  };
}

function streamScripted(model: Model<any>, context: Context, options?: SimpleStreamOptions) {
  const stream = createAssistantMessageEventStream();
  queueMicrotask(() => {
    const output: AssistantMessage = {
      role: "assistant",
      content: [],
      api: model.api,
      provider: model.provider,
      model: model.id,
      usage: emptyUsage(),
      stopReason: "pending",
      timestamp: Date.now(),
    };
    try {
      if (options?.signal?.aborted) throw new Error("plan fixture was aborted");
      stream.push({ type: "start", partial: output });
      const results = new Set(
        context.messages
          .filter((message) => message.role === "toolResult")
          .map((message) => message.toolCallId),
      );
      const step = STEPS.find((item) => !results.has(item.id));
      if (step) {
        const toolCall = { type: "toolCall" as const, ...step };
        output.content.push(toolCall);
        stream.push({ type: "toolcall_start", contentIndex: 0, partial: output });
        stream.push({ type: "toolcall_end", contentIndex: 0, toolCall, partial: output });
        output.stopReason = "toolUse";
      } else {
        const text = "Plan-mode control sequence complete.";
        output.content.push({ type: "text", text });
        stream.push({ type: "text_start", contentIndex: 0, partial: output });
        stream.push({ type: "text_delta", contentIndex: 0, delta: text, partial: output });
        stream.push({ type: "text_end", contentIndex: 0, content: text, partial: output });
        output.stopReason = "stop";
      }
      stream.push({ type: "done", reason: output.stopReason, message: output });
      stream.end();
    } catch (error) {
      output.stopReason = options?.signal?.aborted ? "aborted" : "error";
      output.errorMessage = error instanceof Error ? error.message : String(error);
      stream.push({ type: "error", reason: output.stopReason, error: output });
      stream.end();
    }
  });
  return stream;
}

export default function registerPlanProvider(pi: ExtensionAPI) {
  pi.registerProvider("hwb-plan-scripted", {
    name: "Harness Workbench plan-mode provider",
    baseUrl: "http://127.0.0.1:9/not-used",
    apiKey: "hwb-offline-fixture",
    api: "openai-completions",
    streamSimple: streamScripted,
    models: [{
      id: "plan-policy",
      name: "Deterministic plan policy fixture",
      reasoning: false,
      input: ["text"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 4096,
      maxTokens: 128,
    }],
  });
}
