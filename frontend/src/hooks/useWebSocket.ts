// useWebSocket.ts
import { useState, useEffect, useRef, useCallback } from "react";
import { writeTag, type ManualSource } from "../api";

export interface AnomalyResult {
  is_anomaly: boolean;
  score: number;
  status: string;
  remaining?: number;
  elapsed?: number;     // paused_recovering：恢復運轉後已經過幾秒
  missing?: string[];
  threshold?: number;   // 目前模型的門檻（每個模型不同）
  rule_alarm?: boolean; // 任一訊號達警戒值或危險值
  note?: string;
  ai_analysis?: string;
  ai_source?: ManualSource | null; // 診斷依據的手冊章節（後端選定）
  incident_id?: number | null;     // 所屬事件；事件第一秒還沒寫進資料庫時是 null
}

export interface ProductionStatus {
  state: number | null;        // 0=計畫停機 1=運轉 2=待機 3=故障 4=換模
  state_text: string | null;
  good_total: number | null;
  ng_total: number | null;
  product_id: number | null;
}

export interface PlcData {
  timestamp: number;
  values?: Record<string, number>; // 所有訊號（依設備規格），新後端提供
  motor_speed?: number;            // 頂層欄位：舊後端相容
  temperature?: number;
  pressure?: number;
  motor_enable: number;
  anomaly: AnomalyResult;
  production?: ProductionStatus | null; // PLC 沒有生產變數時為 null
  source?: string;
  device_id?: string;
}

// 每秒一筆，保留 10 分鐘給趨勢圖
const HISTORY_MAX = 600;

export function useWebSocket(url: string) {
  const [data, setData] = useState<PlcData | null>(null);
  const [history, setHistory] = useState<PlcData[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const ws = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const closedByUs = useRef(false);

  const connect = useCallback(() => {
    ws.current = new WebSocket(url);
    ws.current.onopen = () => setIsConnected(true);
    ws.current.onclose = () => {
      setIsConnected(false);
      if (!closedByUs.current) reconnectTimer.current = setTimeout(connect, 3000);
    };
    ws.current.onmessage = (e) => {
      const parsed: PlcData = JSON.parse(e.data);
      setData(parsed);
      setHistory((prev) => {
        // 同一秒可能收到兩次（AI 診斷完成時後端會再廣播一次），用新的取代舊的
        const last = prev[prev.length - 1];
        const base = last && last.timestamp === parsed.timestamp ? prev.slice(0, -1) : prev;
        return [...base.slice(-(HISTORY_MAX - 1)), parsed];
      });
    };
  }, [url]);

  useEffect(() => {
    closedByUs.current = false;
    connect();
    return () => {
      closedByUs.current = true;
      clearTimeout(reconnectTimer.current);
      ws.current?.close();
    };
  }, [connect]);

  const sendCommand = useCallback((tag: string, value: number) => writeTag(tag, value), []);

  return { data, history, isConnected, sendCommand };
}
