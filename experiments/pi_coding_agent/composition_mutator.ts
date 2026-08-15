import { appendFileSync } from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function register(pi: ExtensionAPI) {
  const path = process.env.PI_HWB_COMPOSITION_PATH;
  if (!path) throw new Error("PI_HWB_COMPOSITION_PATH is required");
  pi.on("tool_call", (event) => {
    if (event.toolCallId !== "hwb-compose-treatment") return;
    const input = event.input as { path?: unknown };
    const before = input.path;
    input.path = "redirected.txt";
    appendFileSync(path, `${JSON.stringify({ schema: "pi-hwb-composition/v0.1",
      handler: "mutator", before, after: input.path })}\n`);
  });
}
