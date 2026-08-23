import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";
import process from "node:process";
import { defineConfig, UserConfig } from "vite";

const packageLock = JSON.parse(
  readFileSync(new URL("./package-lock.json", import.meta.url), "utf8"),
) as {
  packages: Record<string, { version?: string }>;
};
const tldrawVersion = packageLock.packages["node_modules/tldraw"]?.version;
if (!tldrawVersion) {
  throw new Error(
    "The resolved tldraw version was not found in package-lock.json.",
  );
}

/**
 * Vite configuration for Streamlit Custom Component v2 development using React.
 *
 * @see https://vitejs.dev/config/ for complete Vite configuration options.
 */
export default defineConfig(() => {
  const isProd = process.env.NODE_ENV === "production";
  const isDev = !isProd;

  return {
    base: "./",
    plugins: [react()],
    define: {
      // We are building in library mode, we need to define the NODE_ENV
      // variable to prevent issues when executing the JS.
      "process.env.NODE_ENV": JSON.stringify(process.env.NODE_ENV),
      __TLDRAW_VERSION__: JSON.stringify(tldrawVersion),
    },
    build: {
      minify: isDev ? false : "oxc",
      outDir: "build",
      sourcemap: isDev,
      lib: {
        entry: "./src/index.tsx",
        name: "NomadCanvas",
        formats: ["es"],
        fileName: "index-[hash]",
      },
      ...(!isDev && {
        rolldownOptions: {
          output: {
            minify: {
              compress: {
                dropConsole: true,
                dropDebugger: true,
              },
            },
          },
        },
      }),
    },
  } satisfies UserConfig;
});
