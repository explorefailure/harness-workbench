import {
  createAssistantMessageEventStream,
  type AssistantMessage,
  type Context,
  type Model,
  type SimpleStreamOptions,
} from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const RESPONSE = "Independent text-only Pi workload completed.";

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

function streamText(
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

    try {
      if (options?.signal?.aborted) {
        throw new Error("text fixture was aborted before it started");
      }
      stream.push({ type: "start", partial: output });
      output.content.push({ type: "text", text: RESPONSE });
      stream.push({ type: "text_start", contentIndex: 0, partial: output });
      stream.push({
        type: "text_delta",
        contentIndex: 0,
        delta: RESPONSE,
        partial: output,
      });
      stream.push({
        type: "text_end",
        contentIndex: 0,
        content: RESPONSE,
        partial: output,
      });
      output.stopReason = "stop";
      stream.push({ type: "done", reason: "stop", message: output });
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

export default function registerTextProvider(pi: ExtensionAPI) {
  pi.registerProvider("hwb-text-scripted", {
    name: "Harness Workbench independent text provider",
    baseUrl: "http://127.0.0.1:9/not-used",
    apiKey: "hwb-offline-fixture",
    api: "openai-completions",
    streamSimple: streamText,
    models: [
      {
        id: "text-only",
        name: "Deterministic text-only fixture",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 4096,
        maxTokens: 128,
      },
    ],
  });
}
