import { appendFileSync } from "node:fs";
import {
  createAssistantMessageEventStream,
  type AssistantMessage,
  type Context,
  type Model,
  type SimpleStreamOptions,
} from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const TREATMENT = {
  id: "hwb-failure-rewrite-treatment",
  name: "bash",
  arguments: {
    command: "test -d seed.txt && printf 'impossible treatment effect\\n' > attempted.txt",
  },
} as const;
const RECOVERY = {
  id: "hwb-branch-recovery",
  name: "write",
  arguments: { path: "recovery.txt", content: "recovered after observed failure\n" },
} as const;
const TRUSTED = {
  id: "hwb-branch-trusted-success",
  name: "write",
  arguments: { path: "trusted.txt", content: "continued after claimed success\n" },
} as const;
const CONTROL = {
  id: "hwb-branch-control",
  name: "write",
  arguments: { path: "permitted.txt", content: "branch rewrite control\n" },
} as const;

function usage() {
  return { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } };
}

function nextStep(context: Context) {
  const results = context.messages.filter((message) => message.role === "toolResult");
  const seen = new Set(results.map((message) => message.toolCallId));
  if (!seen.has(TREATMENT.id)) return TREATMENT;

  const treatment = results.find((message) => message.toolCallId === TREATMENT.id);
  if (!treatment) throw new Error("treatment result disappeared from context");
  const branch = treatment.isError ? RECOVERY : TRUSTED;
  if (!seen.has(branch.id)) {
    const evidence = process.env.PI_HWB_BRANCH_PATH;
    if (!evidence) throw new Error("PI_HWB_BRANCH_PATH is required");
    appendFileSync(evidence, `${JSON.stringify({ schema: "pi-hwb-branch/v0.1",
      observedIsError: treatment.isError,
      selected: treatment.isError ? "recovery" : "trusted-success" })}\n`);
    return branch;
  }
  if (!seen.has(CONTROL.id)) return CONTROL;
  return undefined;
}

function stream(model: Model<any>, context: Context, options?: SimpleStreamOptions) {
  const result = createAssistantMessageEventStream();
  queueMicrotask(() => {
    const output: AssistantMessage = { role: "assistant", content: [], api: model.api,
      provider: model.provider, model: model.id, usage: usage(), stopReason: "pending",
      timestamp: Date.now() };
    try {
      if (options?.signal?.aborted) throw new Error("branch rewrite fixture aborted");
      result.push({ type: "start", partial: output });
      const step = nextStep(context);
      if (step) {
        const toolCall = { type: "toolCall" as const, ...step };
        output.content.push(toolCall);
        result.push({ type: "toolcall_start", contentIndex: 0, partial: output });
        result.push({ type: "toolcall_end", contentIndex: 0, toolCall, partial: output });
        output.stopReason = "toolUse";
      } else {
        const text = "Result-driven branch control complete.";
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
  pi.registerProvider("hwb-branch-rewrite", { name: "HWB branch rewrite provider",
    baseUrl: "http://127.0.0.1:9/not-used", apiKey: "hwb-offline-fixture",
    api: "openai-completions", streamSimple: stream,
    models: [{ id: "branch-rewrite", name: "Branch rewrite fixture", reasoning: false,
      input: ["text"], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 4096, maxTokens: 128 }] });
}
