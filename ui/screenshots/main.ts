// Screenshot entry: demo backend first, then the real app.
import { emitRunOutput, installMock } from "./mock";

installMock();
(window as any).__emitRunOutput = emitRunOutput;
await import("../src/main");
