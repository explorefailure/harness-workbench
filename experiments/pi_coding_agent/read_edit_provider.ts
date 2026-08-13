import {
  createAssistantMessageEventStream,
  type AssistantMessage,
  type Context,
  type Model,
  type SimpleStreamOptions,
} from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const READ_CALL_ID = "hwb-read-unicode";
const EDIT_CALL_ID = "hwb-edit-unicode";
const TARGET = "nested dir/naïve file.txt";

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

function streamScripted(
  model: Model<any>,
  context: Context,
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
        throw new Error("read/edit fixture was aborted before it started");
      }
      stream.push({ type: "start", partial: output });
      const toolResultIds = new Set(
        context.messages
          .filter((message) => message.role === "toolResult")
          .map((message) => message.toolCallId),
      );

      let toolCall:
        | {
            type: "toolCall";
            id: string;
            name: string;
            arguments: Record<string, unknown>;
          }
        | undefined;
      if (!toolResultIds.has(READ_CALL_ID)) {
        toolCall = {
          type: "toolCall",
          id: READ_CALL_ID,
          name: "read",
          arguments: { path: TARGET },
        };
      } else if (!toolResultIds.has(EDIT_CALL_ID)) {
        toolCall = {
          type: "toolCall",
          id: EDIT_CALL_ID,
          name: "edit",
          arguments: {
            path: TARGET,
            edits: [{ oldText: "status: old", newText: "status: verified" }],
          },
        };
      }

      if (toolCall) {
        output.content.push(toolCall);
        stream.push({ type: "toolcall_start", contentIndex: 0, partial: output });
        stream.push({ type: "toolcall_end", contentIndex: 0, toolCall, partial: output });
        output.stopReason = "toolUse";
      } else {
        const text = "Independent read/edit Pi workload completed.";
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

export default function registerReadEditProvider(pi: ExtensionAPI) {
  pi.registerProvider("hwb-read-edit-scripted", {
    name: "Harness Workbench read/edit provider",
    baseUrl: "http://127.0.0.1:9/not-used",
    apiKey: "hwb-offline-fixture",
    api: "openai-completions",
    streamSimple: streamScripted,
    models: [
      {
        id: "read-edit",
        name: "Deterministic read/edit fixture",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 4096,
        maxTokens: 128,
      },
    ],
  });
}
