// 危險告警嗶聲：狀態列開關，預設關閉；設定記在這台瀏覽器
// 瀏覽器規定網頁要先被點過才能出聲，所以按開關那一下順便建立 AudioContext
import { useCallback, useEffect, useRef, useState } from "react";

const KEY = "scada.alarmSound";
const INTERVAL_MS = 2000;

function loadPref(): boolean {
  try {
    return localStorage.getItem(KEY) === "on";
  } catch {
    return false;
  }
}

export function useAlarmSound(active: boolean) {
  const [enabled, setEnabled] = useState<boolean>(loadPref);
  const ctxRef = useRef<AudioContext | null>(null);
  const [running, setRunning] = useState(false); // 瀏覽器准不准出聲

  const ensureCtx = useCallback(() => {
    try {
      if (!ctxRef.current) {
        const ctx = new AudioContext();
        ctx.onstatechange = () => setRunning(ctx.state === "running");
        ctxRef.current = ctx;
      }
      void ctxRef.current.resume().then(() => setRunning(ctxRef.current?.state === "running"));
    } catch {
      /* 不支援 Web Audio 就不出聲 */
    }
  }, []);

  const beep = useCallback(() => {
    const ctx = ctxRef.current;
    if (!ctx || ctx.state !== "running") return;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "square";
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.08, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.35);
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.35);
  }, []);

  const toggle = useCallback(() => {
    setEnabled((on) => {
      const next = !on;
      try {
        localStorage.setItem(KEY, next ? "on" : "off");
      } catch {
        /* 無痕模式等情況存不了，只影響下次開網頁 */
      }
      return next;
    });
    ensureCtx();
  }, [ensureCtx]);

  // 設定記著「開」但重新整理後還沒點過頁面：第一次點擊時補建 AudioContext
  useEffect(() => {
    if (!enabled || running) return;
    window.addEventListener("pointerdown", ensureCtx, { once: true });
    return () => window.removeEventListener("pointerdown", ensureCtx);
  }, [enabled, running, ensureCtx]);

  useEffect(() => {
    if (!enabled || !active) return;
    beep();
    const id = window.setInterval(beep, INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [enabled, active, beep]);

  // 開著聲音但瀏覽器還不准出聲（重新整理後沒點過頁面）
  return { enabled, toggle, blocked: enabled && !running };
}
