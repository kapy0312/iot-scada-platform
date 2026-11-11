import { useEffect, useState } from "react";
import { getDataSource, getModelInfo, reloadModel, setDataSource, type ModelInfo, type Source } from "../api";
import { WS_URL } from "../config";

interface Props {
  historyCount: number;
  threshold: number;
  profileFromServer: boolean;
  lastUpdate?: number;
}

export default function SystemSettings({ historyCount, threshold, profileFromServer, lastUpdate }: Props) {
  const [current, setCurrent] = useState<Source | null>(null);
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [model, setModel] = useState<ModelInfo | null>(null);
  const [reloading, setReloading] = useState(false);

  useEffect(() => {
    getDataSource().then(setCurrent).catch(() => setError("無法取得目前資料源"));
    getModelInfo().then(setModel).catch(() => setModel(null));
  }, []);

  const onReload = async () => {
    setReloading(true);
    try {
      setModel(await reloadModel());
    } catch (e) {
      setError(`重新載入模型失敗：${e instanceof Error ? e.message : ""}`);
    } finally {
      setReloading(false);
    }
  };

  const switchTo = async (source: Source) => {
    if (source === current || switching) return;
    const warn = source === "opcua" ? "切換到 OPC UA 會連線實體 PLC（或 PLCSIM），確定嗎？" : "切換到模擬資料源，確定嗎？";
    if (!window.confirm(warn)) return;
    setSwitching(true);
    setError(null);
    try {
      setCurrent(await setDataSource(source));
    } catch (e) {
      setError(e instanceof Error ? e.message : "切換失敗");
    } finally {
      setSwitching(false);
    }
  };

  const rows: Array<[string, string]> = [
    ["WebSocket", WS_URL],
    ["最後更新", lastUpdate ? new Date(lastUpdate * 1000).toLocaleString("zh-TW", { hour12: false }) : "—"],
    ["趨勢緩衝", `${historyCount} / 600 筆（10 分鐘）`],
    ["異常模型", model?.loaded ? `Isolation Forest（30 筆視窗 · ${model.feature_count ?? "?"} 特徵）` : "尚未載入"],
    ["模型檔", model?.file ?? "—"],
    ["訓練資料來源", model?.trained_on ? `${model.trained_on}${model.samples ? `（${model.samples} 筆）` : ""}` : "—"],
    ["模型使用的訊號", model?.tags?.join("、") ?? "—"],
    ["異常門檻", `分數連續 5 秒低於 ${model?.threshold ?? threshold}`],
    ["設備規格來源", profileFromServer ? "後端 /api/device-profile" : "前端預設值（後端未回應）"],
  ];

  return (
    <div className="columns">
      <section className="panel col-side" aria-labelledby="src-h">
        <div className="panel-head">
          <h2 id="src-h" className="panel-title">資料源</h2>
        </div>
        <div className="seg" role="group" aria-label="資料源">
          <button type="button" className="btn" aria-pressed={current === "mock"} disabled={switching} onClick={() => switchTo("mock")}>
            MOCK 模擬
          </button>
          <button type="button" className="btn" aria-pressed={current === "opcua"} disabled={switching} onClick={() => switchTo("opcua")}>
            OPC UA
          </button>
        </div>
        <p className="small muted">
          模擬資料以 S7-1511T-MOCK 存放，不會進入模型訓練。觸發模擬異常的按鈕在「監控」分頁的控制面板。
        </p>
        {error && <p className="error">{error}</p>}
      </section>

      <section className="panel col-wide" aria-labelledby="sys-h">
        <div className="panel-head">
          <h2 id="sys-h" className="panel-title">系統資訊</h2>
        </div>
        {rows.map(([k, v]) => (
          <div className="kv" key={k}>
            <span className="muted">{k}</span>
            <span className="mono" style={{ textAlign: "right", overflowWrap: "anywhere" }}>{v}</span>
          </div>
        ))}
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12, flexWrap: "wrap" }}>
          <button type="button" className="btn" onClick={onReload} disabled={reloading}>
            {reloading ? "載入中…" : "重新載入最新模型"}
          </button>
          <span className="small muted">手動訓練（python -m ml.train）後按這裡，不必重啟後端</span>
        </div>
      </section>
    </div>
  );
}
