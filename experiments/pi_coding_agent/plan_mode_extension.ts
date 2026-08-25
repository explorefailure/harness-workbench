import { appendFileSync } from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

type Mode = "plan" | "act";

const SAFE_COMMAND = "test -f seed.txt";

function configuredMode(): Mode {
  const value = process.env.PI_HWB_PLAN_MODE;
  if (value !== "plan" && value !== "act") {
    throw new Error("PI_HWB_PLAN_MODE must be 'plan' or 'act'");
  }
  return value;
}

export default function registerPlanMode(pi: ExtensionAPI) {
  const mode = configuredMode();
  const evidencePath = process.env.PI_HWB_PLAN_DECISION_PATH;
  if (!evidencePath) throw new Error("PI_HWB_PLAN_DECISION_PATH is required");

  function record(value: Record<string, unknown>) {
    appendFileSync(
      evidencePath,
      `${JSON.stringify({ schema: "pi-hwb-plan-decision/v0.1", mode, ...value })}\n`,
      { encoding: "utf8" },
    );
  }

  pi.on("session_start", () => {
    const activeTools = mode === "plan" ? ["read", "bash"] : ["read", "bash", "write"];
    pi.setActiveTools(activeTools);
    record({ event: "active_tools", activeTools });
  });

  pi.on("tool_call", (event) => {
    let decision = "allow";
    if (
      mode === "plan" &&
      event.toolName === "bash" &&
      (event.input as { command?: unknown }).command !== SAFE_COMMAND
    ) {
      decision = "block";
    }
    record({
      event: "tool_call",
      toolCallId: event.toolCallId,
      toolName: event.toolName,
      decision,
    });
    if (decision === "block") {
      return { block: true, reason: "Plan mode blocked a non-allowlisted command" };
    }
  });
}
