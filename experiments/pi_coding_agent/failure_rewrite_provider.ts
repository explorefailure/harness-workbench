import {
  createAssistantMessageEventStream,
  type AssistantMessage,
  type Context,
  type Model,
  type SimpleStreamOptions,
} from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const STEPS = [
  {
    id: "hwb-failure-rewrite-treatment",
    name: "bash",
    arguments: {
      command: "test -d seed.txt && printf 'impossible treatment effect\\n' > attempted.txt",
    },
  },
  {
    id: "hwb-failure-rewrite-control",
    name: "write",
    arguments: { path: "permitted.txt", content: "failure rewrite control\n" },
  },
] as const;

function usage() {
  return { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } };
}

function stream(model: Model<any>, context: Context, options?: SimpleStreamOptions) {
  const result = createAssistantMessageEventStream();
  queueMicrotask(() => {
    const output: AssistantMessage = { role: "assistant", content: [], api: model.api,
      provider: model.provider, model: model.id, usage: usage(), stopReason: "pending",
      timestamp: Date.now() };
    try {
      if (options?.signal?.aborted) throw new Error("failure rewrite fixture aborted");
      result.push({ type: "start", partial: output });
      const seen = new Set(context.messages.filter((message) => message.role === "toolResult")
        .map((message) => message.toolCallId));
      const step = STEPS.find((item) => !seen.has(item.id));
      if (step) {
        const toolCall = { type: "toolCall" as const, ...step };
        output.content.push(toolCall);
        result.push({ type: "toolcall_start", contentIndex: 0, partial: output });
        result.push({ type: "toolcall_end", contentIndex: 0, toolCall, partial: output });
        output.stopReason = "toolUse";
      } else {
        const text = "Failed-write rewrite control complete.";
        output.content.push({ type: "text", text });
        result.push({ type: "text_start", contentIndex: 0, partial: output });
        result.push({ type: "text_delta", contentIndex: 0, delta: text, partial: output });
        result.push({ type: "text_end", contentIndex: 0, content: text, partial: output });
        output.stopReason = "stop";
      }
      result.push({ type: "done", reason: output.stopReason, message: output });
      result.end();
    } catch (error) {
      output.stopReason = options?.signal?.aborted ? "aborted" : "error";
      output.errorMessage = error instanceof Error ? error.message : String(error);
      result.push({ type: "error", reason: output.stopReason, error: output });
      result.end();
    }
  });
  return result;
}

export default function register(pi: ExtensionAPI) {
  pi.registerProvider("hwb-failure-rewrite", { name: "HWB failure rewrite provider",
    baseUrl: "http://127.0.0.1:9/not-used", apiKey: "hwb-offline-fixture",
    api: "openai-completions", streamSimple: stream,
    models: [{ id: "failure-rewrite", name: "Failure rewrite fixture", reasoning: false,
      input: ["text"], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 4096, maxTokens: 128 }] });
}
