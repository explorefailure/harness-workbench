import { appendFileSync } from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function register(pi: ExtensionAPI) {
  const path = process.env.PI_HWB_FAILURE_PATH;
  if (!path) throw new Error("PI_HWB_FAILURE_PATH is required");
  pi.on("tool_call", (event) => {
    if (event.toolCallId !== "hwb-compose-treatment") return;
    appendFileSync(path, `${JSON.stringify({ schema: "pi-hwb-handler-failure/v0.1",
      handler: "thrower", observed: (event.input as { path?: unknown }).path })}\n`);
    throw new Error("deterministic tool_call handler failure");
  });
}
