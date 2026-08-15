import { appendFileSync } from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function register(pi: ExtensionAPI) {
  const evidence = process.env.PI_HWB_COMPOSITION_PATH;
  if (!evidence) throw new Error("PI_HWB_COMPOSITION_PATH is required");
  pi.on("tool_call", (event) => {
    if (event.toolCallId !== "hwb-compose-treatment") return;
    const observed = (event.input as { path?: unknown }).path;
    const decision = observed === "redirected.txt" ? "block" : "allow";
    appendFileSync(evidence, `${JSON.stringify({ schema: "pi-hwb-composition/v0.1",
      handler: "guard", observed, decision })}\n`);
    if (decision === "block") return { block: true, reason: "guard blocked redirected target" };
  });
}
