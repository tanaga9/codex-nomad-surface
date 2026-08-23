import { build } from "vite";

const result = await build({
  configFile: false,
  logLevel: "silent",
  build: {
    write: false,
    lib: {
      entry: new URL("../src/canvas-protocol.behavior-test.ts", import.meta.url)
        .pathname,
      formats: ["es"],
    },
  },
});

const output = Array.isArray(result) ? result[0] : result;
const chunk = output.output.find((item) => item.type === "chunk");
if (!chunk) throw new Error("Canvas protocol test bundle was not produced.");

try {
  await import(
    `data:text/javascript;base64,${Buffer.from(chunk.code).toString("base64")}`
  );
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
}
process.exit(0);
