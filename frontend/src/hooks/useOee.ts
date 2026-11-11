import { useCallback, useEffect, useRef, useState } from "react";
import { getOeeCurrent, type OeeCurrent } from "../api";

const POLL_MS = 30_000;

/**
 * 本班 OEE：每 30 秒更新一次；即時資料裡的機台狀態一變（例如按停止）就立刻重抓，
 * 狀態與持續時間不必等下一次輪詢
 */
export function useOeeCurrent(deviceId: string, liveState: number | null | undefined) {
  const [data, setData] = useState<OeeCurrent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);

  const load = useCallback(async () => {
    const my = ++seq.current;
    try {
      const d = await getOeeCurrent(deviceId);
      if (my === seq.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (my === seq.current) setError(e instanceof Error ? e.message : "無法取得 OEE");
    }
  }, [deviceId]);

  useEffect(() => {
    const first = window.setTimeout(load, 0);   // 第一次也走計時器，不在 effect 裡直接觸發 setState
    const id = window.setInterval(load, POLL_MS);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(id);
    };
  }, [load]);

  const lastLive = useRef(liveState);
  useEffect(() => {
    if (liveState !== lastLive.current) {
      lastLive.current = liveState;
      const t = window.setTimeout(load, 0);
      return () => window.clearTimeout(t);
    }
  }, [liveState, load]);

  return { data, error, reload: load };
}

/** 每秒更新一次的「現在時間」，給持續時間顯示用 */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}
