import {
  createAssistantMessageEventStream,
  type AssistantMessage,
  type Context,
  type Model,
  type SimpleStreamOptions,
} from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const READ_IMPLEMENTATION = "hwb-coding-read-implementation";
const READ_TESTS = "hwb-coding-read-tests";
const RUN_FAILING_TESTS = "hwb-coding-run-failing-tests";
const EDIT_IMPLEMENTATION = "hwb-coding-edit-implementation";
const RUN_PASSING_TESTS = "hwb-coding-run-passing-tests";

type ScriptedToolCall = {
  type: "toolCall";
  id: string;
  name: string;
  arguments: Record<string, unknown>;
};

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
        throw new Error("coding fixture was aborted before it started");
      }
      stream.push({ type: "start", partial: output });
      const toolResultIds = new Set(
        context.messages
          .filter((message) => message.role === "toolResult")
          .map((message) => message.toolCallId),
      );

      const steps: ScriptedToolCall[] = [
        {
          type: "toolCall",
          id: READ_IMPLEMENTATION,
          name: "read",
          arguments: { path: "slugger.py" },
        },
        {
          type: "toolCall",
          id: READ_TESTS,
          name: "read",
          arguments: { path: "test_slugger.py" },
        },
        {
          type: "toolCall",
          id: RUN_FAILING_TESTS,
          name: "bash",
          arguments: { command: "python3 -m unittest -q >/dev/null 2>&1" },
        },
        {
          type: "toolCall",
          id: EDIT_IMPLEMENTATION,
          name: "edit",
          arguments: {
            path: "slugger.py",
            edits: [
              {
                oldText:
                  'def slugify(text):\n    """Return a URL-safe identifier for a short ASCII label."""\n    return text.lower().replace(" ", "-")',
                newText:
                  'import re\n\n\ndef slugify(text):\n    """Return a URL-safe identifier for a short ASCII label."""\n    words = re.findall(r"[a-z0-9]+", text.casefold())\n    return "-".join(words)',
              },
            ],
          },
        },
        {
          type: "toolCall",
          id: RUN_PASSING_TESTS,
          name: "bash",
          arguments: { command: "python3 -m unittest -q >/dev/null 2>&1" },
        },
      ];
      const toolCall = steps.find((step) => !toolResultIds.has(step.id));

      if (toolCall) {
        output.content.push(toolCall);
        stream.push({ type: "toolcall_start", contentIndex: 0, partial: output });
        stream.push({
          type: "toolcall_end",
          contentIndex: 0,
          toolCall,
          partial: output,
        });
        output.stopReason = "toolUse";
      } else {
        const text = "Bug reproduced, implementation repaired, and tests pass.";
        output.content.push({ type: "text", text });
        stream.push({ type: "text_start", contentIndex: 0, partial: output });
        stream.push({
          type: "text_delta",
          contentIndex: 0,
          delta: text,
          partial: output,
        });
        stream.push({
          type: "text_end",
          contentIndex: 0,
          content: text,
          partial: output,
        });
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

export default function registerCodingProvider(pi: ExtensionAPI) {
  pi.registerProvider("hwb-coding-scripted", {
    name: "Harness Workbench coding provider",
    baseUrl: "http://127.0.0.1:9/not-used",
    apiKey: "hwb-offline-fixture",
    api: "openai-completions",
    streamSimple: streamScripted,
    models: [
      {
        id: "bug-fix",
        name: "Deterministic coding fixture",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 4096,
        maxTokens: 128,
      },
    ],
  });
}
