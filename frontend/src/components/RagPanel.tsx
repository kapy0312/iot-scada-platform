import { useEffect, useState } from "react";

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
  const [error, setError] = useState<string | null>(null);

  const fetchDocuments = async () => {
    setLoading(true);
    try {
      const res = await fetch("http://localhost:8000/api/rag/documents");
      const data = await res.json();
      setDocuments(data.documents ?? []);
    } catch {
      setError("無法取得文件清單");
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
    setError(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("device_id", deviceId);
      const res = await fetch("http://localhost:8000/api/rag/upload", {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      if (data.status === "ok") {
        setFile(null);
        await fetchDocuments();
      } else if (data.status === "skipped") {
        setError("檔案已存在，未重複嵌入");
      } else {
        setError(data.reason ?? "上傳失敗");
      }
    } catch {
      setError("上傳失敗，請確認後端服務正常");
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (fileHash: string, filename: string) => {
    if (!window.confirm(`確定刪除「${filename}」？此操作無法復原。`)) return;
    try {
      await fetch(`http://localhost:8000/api/rag/documents/${fileHash}`, {
        method: "DELETE",
      });
      await fetchDocuments();
    } catch {
      setError("刪除失敗");
    }
  };

  const handleToggle = async (fileHash: string, currentEnabled: boolean) => {
    // 樂觀更新，先更新畫面再打 API，失敗時回滾
    setDocuments((prev) =>
      prev.map((d) =>
        d.file_hash === fileHash ? { ...d, enabled: !currentEnabled } : d
      )
    );
    try {
      const res = await fetch(
        `http://localhost:8000/api/rag/documents/${fileHash}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: !currentEnabled }),
        }
      );
      if (!res.ok) throw new Error();
    } catch {
      setError("切換狀態失敗");
      setDocuments((prev) =>
        prev.map((d) =>
          d.file_hash === fileHash ? { ...d, enabled: currentEnabled } : d
        )
      );
    }
  };

  return (
    <div
      style={{
        background: "var(--bg-card)",
        border: "1px solid var(--border-dim)",
        borderRadius: 2,
        padding: "20px 24px",
      }}
    >
      <div
        style={{
          fontFamily: "var(--font-ui)",
          fontSize: 11,
          letterSpacing: 3,
          color: "var(--text-secondary)",
          textTransform: "uppercase",
          marginBottom: 16,
        }}
      >
        RAG Knowledge Base
      </div>

      <div
        style={{
          display: "flex",
          gap: 8,
          marginBottom: 16,
          paddingBottom: 16,
          borderBottom: "1px solid var(--border-dim)",
          flexWrap: "wrap",
        }}
      >
        <input
          type="file"
          accept="application/pdf"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "var(--text-secondary)",
            flex: "1 1 200px",
          }}
        />
        <input
          type="text"
          value={deviceId}
          onChange={(e) => setDeviceId(e.target.value)}
          placeholder="device_id"
          style={{
            background: "var(--bg-deep)",
            border: "1px solid var(--border-mid)",
            color: "var(--accent-cyan)",
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            padding: "6px 10px",
            width: 110,
            borderRadius: 1,
            outline: "none",
          }}
        />
        <button
          onClick={handleUpload}
          disabled={!file || uploading}
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 12,
            fontWeight: 600,
            letterSpacing: 1,
            textTransform: "uppercase",
            padding: "6px 16px",
            background: "transparent",
            color: "var(--accent-cyan)",
            border: "1px solid var(--border-mid)",
            borderRadius: 1,
            cursor: !file || uploading ? "not-allowed" : "pointer",
            opacity: !file || uploading ? 0.5 : 1,
          }}
        >
          {uploading ? "上傳中..." : "上傳"}
        </button>
      </div>

      {error && (
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "var(--accent-red)",
            marginBottom: 12,
          }}
        >
          {error}
        </div>
      )}

      {loading ? (
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            color: "var(--text-dim)",
          }}
        >
          載入中...
        </div>
      ) : documents.length === 0 ? (
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            color: "var(--text-dim)",
          }}
        >
          尚未上傳任何文件
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {documents.map((doc) => (
            <div
              key={doc.file_hash}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "8px 10px",
                background: "var(--bg-deep)",
                border: "1px solid var(--border-dim)",
                borderRadius: 2,
                opacity: doc.enabled ? 1 : 0.5,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <input
                  type="checkbox"
                  checked={doc.enabled}
                  onChange={() => handleToggle(doc.file_hash, doc.enabled)}
                  style={{
                    cursor: "pointer",
                    accentColor: "var(--accent-cyan)",
                  }}
                />
                <div
                  style={{ display: "flex", flexDirection: "column", gap: 2 }}
                >
                  <span
                    style={{
                      fontFamily: "var(--font-ui)",
                      fontSize: 12,
                      color: "var(--text-primary)",
                    }}
                  >
                    {doc.filename}
                  </span>
                  <span
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 10,
                      color: "var(--text-dim)",
                    }}
                  >
                    {doc.device_id} · {doc.chunk_count} chunks
                  </span>
                </div>
              </div>
              <button
                onClick={() => handleDelete(doc.file_hash, doc.filename)}
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  padding: "3px 8px",
                  background: "transparent",
                  color: "var(--accent-red)",
                  border: "1px solid rgba(255,61,90,0.4)",
                  borderRadius: 2,
                  cursor: "pointer",
                }}
              >
                刪除
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
