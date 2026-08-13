import { appendFileSync } from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

type GuardMode = "block" | "allow";

function guardMode(): GuardMode {
  const value = process.env.PI_HWB_GUARD_MODE;
  if (value !== "block" && value !== "allow") {
    throw new Error("PI_HWB_GUARD_MODE must be 'block' or 'allow'");
  }
  return value;
}

export default function registerGuard(pi: ExtensionAPI) {
  const mode = guardMode();
  const decisionPath = process.env.PI_HWB_DECISION_PATH;
  if (!decisionPath) {
    throw new Error("PI_HWB_DECISION_PATH is required");
  }

  pi.on("tool_call", (event) => {
    if (event.toolName !== "write") return;
    const input = event.input as { path?: unknown };
    if (input.path !== "forbidden.txt") return;

    const decision = mode === "block" ? "block" : "allow";
    appendFileSync(
      decisionPath,
      `${JSON.stringify({
        schema: "pi-hwb-guard-decision/v0.1",
        toolCallId: event.toolCallId,
        toolName: event.toolName,
        path: input.path,
        mode,
        decision,
      })}\n`,
      { encoding: "utf8" },
    );

    if (mode === "block") {
      return {
        block: true,
        reason: "Harness Workbench control blocked forbidden.txt",
      };
    }
  });
}
