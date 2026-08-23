import process from "node:process";
import { defineConfig, UserConfig } from "vite";

export default defineConfig(() => {
  const isDev = process.env.NODE_ENV !== "production";
  return {
    base: "./",
    build: {
      minify: isDev ? false : "oxc",
      outDir: "build",
      sourcemap: isDev,
      lib: {
        entry: "./src/index.ts",
        name: "NomadText",
        formats: ["es"],
        fileName: "index-[hash]",
      },
      ...(!isDev && {
        rolldownOptions: {
          output: {
            minify: { compress: { dropConsole: true, dropDebugger: true } },
          },
        },
      }),
    },
  } satisfies UserConfig;
});
