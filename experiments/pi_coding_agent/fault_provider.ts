import {
  createAssistantMessageEventStream,
  type AssistantMessage,
  type Context,
  type Model,
  type SimpleStreamOptions,
} from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

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

function streamFault(
  model: Model<any>,
  _context: Context,
  options?: SimpleStreamOptions,
) {
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
    stream.push({ type: "start", partial: output });

    if (model.id === "hang") {
      const timer = setInterval(() => undefined, 1000);
      options?.signal?.addEventListener("abort", () => clearInterval(timer), {
        once: true,
      });
      return;
    }

    if (model.id === "provider-error") {
      output.stopReason = "error";
      output.errorMessage = "intentional Harness Workbench provider failure";
      stream.push({ type: "error", reason: "error", error: output });
      stream.end();
      return;
    }

    const text = "Fault fixture reached its normal provider path.";
    output.content.push({ type: "text", text });
    stream.push({ type: "text_start", contentIndex: 0, partial: output });
    stream.push({ type: "text_delta", contentIndex: 0, delta: text, partial: output });
    stream.push({ type: "text_end", contentIndex: 0, content: text, partial: output });
    output.stopReason = "stop";
    stream.push({ type: "done", reason: "stop", message: output });
    stream.end();
  });
  return stream;
}

export default function registerFaultProvider(pi: ExtensionAPI) {
  pi.registerProvider("hwb-fault-scripted", {
    name: "Harness Workbench fault provider",
    baseUrl: "http://127.0.0.1:9/not-used",
    apiKey: "hwb-offline-fixture",
    api: "openai-completions",
    streamSimple: streamFault,
    models: ["provider-error", "hang", "ok"].map((id) => ({
      id,
      name: `Deterministic ${id} fixture`,
      reasoning: false,
      input: ["text"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 4096,
      maxTokens: 128,
    })),
  });
}
