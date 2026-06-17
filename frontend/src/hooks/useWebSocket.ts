// useWebSocket.ts

import { useState, useEffect, useRef, useCallback } from 'react';

export interface AnomalyResult {
  is_anomaly: boolean;
  score: number;
  status: string;
  remaining?: number;
  ai_analysis?: string;
}

export interface PlcData {
  timestamp: number;
  motor_speed: number;
  temperature: number;
  pressure: number;
  motor_enable: number;
  anomaly: AnomalyResult;
}

export function useWebSocket(url: string) {
    const [data, setData] = useState<PlcData | null>(null);
    const [history, setHistory] = useState<PlcData[]>([]);
    const [isConnected, setIsConnected] = useState(false);
    const ws = useRef<WebSocket | null>(null);
    const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

    const connect = useCallback(() => {
        ws.current = new WebSocket(url);
        ws.current.onopen = () => setIsConnected(true);
        ws.current.onclose = () => {
            setIsConnected(false);
            reconnectTimer.current = setTimeout(connect, 3000);
        };
        ws.current.onmessage = (e) => {
            const parsed: PlcData = JSON.parse(e.data);
            setData(parsed);
            setHistory(prev => [...prev.slice(-120), parsed]);
        };
    }, [url]);

    useEffect(() => {
        connect();
        return () => {
            clearTimeout(reconnectTimer.current);
            ws.current?.close();
        };
    }, [connect]);

    const sendCommand = useCallback((tag: string, value: number) => {
        fetch('http://localhost:8000/api/control/write', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tag, value }),
        });
    }, []);

    return { data, history, isConnected, sendCommand };
}