import {
  createAssistantMessageEventStream,
  type AssistantMessage,
  type Context,
  type Model,
  type SimpleStreamOptions,
} from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const FORBIDDEN_CALL_ID = "hwb-write-forbidden";
const PERMITTED_CALL_ID = "hwb-write-permitted";

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
        throw new Error("scripted provider was aborted before it started");
      }

      stream.push({ type: "start", partial: output });
      const toolResultIds = new Set(
        context.messages
          .filter((message) => message.role === "toolResult")
          .map((message) => message.toolCallId),
      );
      const hasToolResults =
        toolResultIds.has(FORBIDDEN_CALL_ID) && toolResultIds.has(PERMITTED_CALL_ID);

      if (!hasToolResults) {
        const toolCalls = [
          {
            type: "toolCall" as const,
            id: FORBIDDEN_CALL_ID,
            name: "write",
            arguments: {
              path: "forbidden.txt",
              content: "created by the Harness Workbench Pi control\n",
            },
          },
          {
            type: "toolCall" as const,
            id: PERMITTED_CALL_ID,
            name: "write",
            arguments: {
              path: "permitted.txt",
              content: "created by the Harness Workbench positive control\n",
            },
          },
        ];
        for (const toolCall of toolCalls) {
          output.content.push(toolCall);
          const contentIndex = output.content.length - 1;
          stream.push({ type: "toolcall_start", contentIndex, partial: output });
          stream.push({ type: "toolcall_end", contentIndex, toolCall, partial: output });
        }
        output.stopReason = "toolUse";
      } else {
        const text = "The scripted write attempt is complete.";
        output.content.push({ type: "text", text });
        const contentIndex = output.content.length - 1;
        stream.push({ type: "text_start", contentIndex, partial: output });
        stream.push({ type: "text_delta", contentIndex, delta: text, partial: output });
        stream.push({ type: "text_end", contentIndex, content: text, partial: output });
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

export default function registerScriptedProvider(pi: ExtensionAPI) {
  pi.registerProvider("hwb-scripted", {
    name: "Harness Workbench scripted provider",
    baseUrl: "http://127.0.0.1:9/not-used",
    apiKey: "hwb-offline-fixture",
    api: "openai-completions",
    streamSimple: streamScripted,
    models: [
      {
        id: "tool-write",
        name: "Deterministic tool-write fixture",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 4096,
        maxTokens: 256,
      },
    ],
  });
}
