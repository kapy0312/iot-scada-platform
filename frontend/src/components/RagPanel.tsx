import { useEffect, useState } from "react";
import { API_BASE } from "../config";

interface RagDocument {
  file_hash: string;
  filename: string;
  device_id: string;
  chunk_count: number;
  enabled: boolean;
}

export default function RagPanel() {
  const [documents, setDocuments] = useState<RagDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [deviceId, setDeviceId] = useState("S7-1511T");
  const [file, setFile] = useState<File | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const fetchDocuments = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/rag/documents`);
      const data = await res.json();
      setDocuments(data.documents ?? []);
    } catch {
      setMessage({ ok: false, text: "無法取得文件清單" });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDocuments();
  }, []);

  const handleUpload = async () => {
    if (!file) return;
    setUploading(true);
    setMessage(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("device_id", deviceId);
      const res = await fetch(`${API_BASE}/api/rag/upload`, { method: "POST", body: formData });
      const data = await res.json();
      if (data.status === "ok") {
        setFile(null);
        setMessage({ ok: true, text: `已上傳 ${data.filename}，切成 ${data.chunks_embedded} 段` });
        await fetchDocuments();
      } else if (data.status === "skipped") {
        setMessage({ ok: false, text: "檔案已存在，未重複嵌入" });
      } else {
        setMessage({ ok: false, text: data.reason ?? "上傳失敗" });
      }
    } catch {
      setMessage({ ok: false, text: "上傳失敗，請確認後端服務正常" });
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (fileHash: string, filename: string) => {
    if (!window.confirm(`確定刪除「${filename}」？此操作無法復原。`)) return;
    try {
      await fetch(`${API_BASE}/api/rag/documents/${fileHash}`, { method: "DELETE" });
      await fetchDocuments();
    } catch {
      setMessage({ ok: false, text: "刪除失敗" });
    }
  };

  const handleToggle = async (fileHash: string, currentEnabled: boolean) => {
    // 樂觀更新，先更新畫面再打 API，失敗時回滾
    setDocuments((prev) => prev.map((d) => (d.file_hash === fileHash ? { ...d, enabled: !currentEnabled } : d)));
    try {
      const res = await fetch(`${API_BASE}/api/rag/documents/${fileHash}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !currentEnabled }),
      });
      if (!res.ok) throw new Error();
    } catch {
      setMessage({ ok: false, text: "切換狀態失敗" });
      setDocuments((prev) => prev.map((d) => (d.file_hash === fileHash ? { ...d, enabled: currentEnabled } : d)));
    }
  };

  return (
    <section className="panel" aria-labelledby="rag-h">
      <div className="panel-head">
        <h2 id="rag-h" className="panel-title">設備手冊（RAG 知識庫）</h2>
      </div>
      <p className="small muted" style={{ marginTop: 0 }}>
        上傳 PDF 後會依章節切段並轉成向量。AI 診斷只會參考有勾選的手冊。device_id 填設備型號（例如 S7-1511T），模擬資料共用同一份手冊。
      </p>

      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-end", gap: 8, paddingBottom: 16, marginBottom: 16, borderBottom: "1px solid var(--line)" }}>
        <div style={{ flex: "1 1 260px", display: "flex", flexDirection: "column", gap: 4 }}>
          <label htmlFor="rag-file" className="muted" style={{ fontSize: 13 }}>PDF 檔案</label>
          <input id="rag-file" type="file" accept="application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <label htmlFor="rag-device" className="muted" style={{ fontSize: 13 }}>device_id</label>
          <input id="rag-device" className="input mono" type="text" value={deviceId}
            onChange={(e) => setDeviceId(e.target.value)} style={{ width: 140 }} />
        </div>
        <button type="button" className="btn primary" onClick={handleUpload} disabled={!file || uploading}>
          {uploading ? "上傳中…" : "上傳"}
        </button>
      </div>

      {message && <p className={message.ok ? "small muted" : "error"} role="status">{message.text}</p>}

      {loading ? (
        <p className="muted">載入中…</p>
      ) : documents.length === 0 ? (
        <p className="muted">尚未上傳任何手冊</p>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 6 }}>
          {documents.map((doc) => (
            <li key={doc.file_hash} style={{
              display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12,
              padding: "8px 10px", background: "var(--track)", border: "1px solid var(--line)", borderRadius: 4,
            }}>
              <label style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer", opacity: doc.enabled ? 1 : 0.6 }}>
                <input type="checkbox" checked={doc.enabled} onChange={() => handleToggle(doc.file_hash, doc.enabled)} />
                <span style={{ display: "flex", flexDirection: "column" }}>
                  <span>{doc.filename}</span>
                  <span className="mono small muted">
                    {doc.device_id} · {doc.chunk_count} 段 · {doc.enabled ? "啟用" : "停用（不參與檢索）"}
                  </span>
                </span>
              </label>
              <button type="button" className="btn ghost" onClick={() => handleDelete(doc.file_hash, doc.filename)}>
                刪除
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
