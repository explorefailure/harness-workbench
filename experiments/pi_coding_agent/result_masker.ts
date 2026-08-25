import { appendFileSync } from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

function firstText(content: Array<{ type: string; text?: string }>): unknown {
  return content.find((item) => item.type === "text")?.text;
}

export default function register(pi: ExtensionAPI) {
  const path = process.env.PI_HWB_RESULT_REWRITE_PATH;
  if (!path) throw new Error("PI_HWB_RESULT_REWRITE_PATH is required");
  pi.on("tool_result", (event) => {
    if (event.toolCallId !== "hwb-compose-treatment") return;
    appendFileSync(path, `${JSON.stringify({ schema: "pi-hwb-result-rewrite/v0.1",
      handler: "masker", observedIsError: event.isError,
      observedText: firstText(event.content) })}\n`);
    return {
      isError: true,
      content: [{ type: "text", text: "synthetic masked failure" }],
    };
  });
}
