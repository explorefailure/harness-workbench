import { appendFileSync } from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function register(pi: ExtensionAPI) {
  const path = process.env.PI_HWB_POLICY_PATH;
  if (!path) throw new Error("PI_HWB_POLICY_PATH is required");
  pi.on("tool_call", (event) => {
    if (event.toolCallId !== "hwb-compose-treatment") return;
    appendFileSync(path, `${JSON.stringify({ schema: "pi-hwb-policy-order/v0.1",
      handler: "allower", decision: "allow", observed:
        (event.input as { path?: unknown }).path })}\n`);
    return { block: false };
  });
}
