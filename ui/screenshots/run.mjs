// Start Vite, run the Playwright capture against screenshots.html, stop Vite.
import { createServer } from "vite";
import { spawn } from "node:child_process";

const server = await createServer({ server: { port: 1430, strictPort: false } });
await server.listen();
const url = `${server.resolvedUrls.local[0]}screenshots.html`;
const code = await new Promise((done) =>
  spawn(process.env.PYTHON ?? "python3", ["screenshots/capture.py", url], { stdio: "inherit" }).on("exit", done),
);
await server.close();
process.exit(code ?? 1);
