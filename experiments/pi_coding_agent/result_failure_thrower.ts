import { appendFileSync } from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function register(pi: ExtensionAPI) {
  const path = process.env.PI_HWB_RESULT_FAILURE_PATH;
  if (!path) throw new Error("PI_HWB_RESULT_FAILURE_PATH is required");
  pi.on("tool_result", (event) => {
    if (event.toolCallId !== "hwb-compose-treatment") return;
    appendFileSync(path, `${JSON.stringify({ schema: "pi-hwb-result-failure/v0.1",
      handler: "thrower", observed: (event.input as { path?: unknown }).path,
      isError: event.isError })}\n`);
    throw new Error("deterministic tool_result handler failure");
  });
}
