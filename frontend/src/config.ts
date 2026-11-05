// 後端位址集中在這裡；要改 IP 或 port 時只改這個檔，或在 frontend/.env 設 VITE_API_BASE
export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://localhost:8000";

export const WS_URL: string = API_BASE.replace(/^http/, "ws") + "/ws/realtime";
