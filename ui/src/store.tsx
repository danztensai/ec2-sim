// App-wide data: server status, instances, AMIs, key pairs, settings (polled).
import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import {
  api,
  describeImages,
  describeInstances,
  describeKeyPairs,
  Image,
  Instance,
  KeyPair,
  Settings,
  Status,
} from "./api";

type Store = {
  status: Status | null;
  settings: Settings | null;
  instances: Instance[];
  images: Image[];
  keys: KeyPair[];
  loaded: boolean;
  starting: boolean;
  refresh: () => Promise<void>;
  refreshStatus: () => Promise<void>;
  setSettings: (s: Settings) => Promise<void>;
  startServer: () => Promise<void>;
  stopServer: () => Promise<void>;
  imageById: (id: string) => Image | undefined;
};

const Ctx = createContext<Store | null>(null);

export function StoreProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [settings, setSettingsState] = useState<Settings | null>(null);
  const [instances, setInstances] = useState<Instance[]>([]);
  const [images, setImages] = useState<Image[]>([]);
  const [keys, setKeys] = useState<KeyPair[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [starting, setStarting] = useState(false);
  const autoStarted = useRef(false);

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await api.status());
    } catch {
      /* keep last status */
    }
  }, []);

  const refresh = useCallback(async () => {
    const st = await api.status().catch(() => null);
    if (st) setStatus(st);
    if (!st?.running) {
      setInstances([]);
      setLoaded(true);
      return;
    }
    const [i, im, k] = await Promise.allSettled([describeInstances(), describeImages(), describeKeyPairs()]);
    if (i.status === "fulfilled") setInstances(i.value);
    if (im.status === "fulfilled") setImages(im.value);
    if (k.status === "fulfilled") setKeys(k.value);
    setLoaded(true);
  }, []);

  const startServer = useCallback(async () => {
    setStarting(true);
    try {
      await api.startServer();
      // Wait for the API to answer (first start may build base images in the background).
      for (let n = 0; n < 60; n++) {
        const st = await api.status();
        setStatus(st);
        if (st.running) break;
        await new Promise((r) => setTimeout(r, 1000));
      }
      await refresh();
    } finally {
      setStarting(false);
    }
  }, [refresh]);

  const stopServer = useCallback(async () => {
    await api.stopServer();
    await refresh();
  }, [refresh]);

  const setSettings = useCallback(async (s: Settings) => {
    await api.saveSettings(s);
    setSettingsState(s);
  }, []);

  // Initial load + optional auto-start.
  useEffect(() => {
    (async () => {
      const s = await api.getSettings();
      setSettingsState(s);
      const st = await api.status();
      setStatus(st);
      if (!st.running && s.auto_start && !autoStarted.current) {
        autoStarted.current = true;
        startServer().catch(() => setLoaded(true));
      } else {
        await refresh();
      }
    })();
  }, [refresh, startServer]);

  // Poll: fast while instances are changing state, slower otherwise.
  const busy = instances.some((i) => ["pending", "stopping", "shutting-down"].includes(i.state));
  useEffect(() => {
    const t = setInterval(refresh, busy ? 1500 : 4000);
    return () => clearInterval(t);
  }, [refresh, busy]);

  const imageById = useCallback((id: string) => images.find((i) => i.id === id), [images]);

  return (
    <Ctx.Provider
      value={{ status, settings, instances, images, keys, loaded, starting, refresh, refreshStatus, setSettings, startServer, stopServer, imageById }}
    >
      {children}
    </Ctx.Provider>
  );
}

export function useStore(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error("useStore outside StoreProvider");
  return s;
}
