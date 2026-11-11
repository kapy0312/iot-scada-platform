import { useCallback, useEffect, useState } from "react";
import { AUTH_REQUIRED_EVENT, getMe, login as apiLogin, logout as apiLogout, type AuthUser } from "../api";

/**
 * 登入狀態：
 * - 開啟頁面時問後端「我是誰」（cookie 由瀏覽器自動帶，網頁拿不到 token 本身）
 * - 任何 API 回 401（過期、被登出）時，清掉使用者並跳出登入視窗
 */
export function useAuth() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [checked, setChecked] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);

  useEffect(() => {
    getMe()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setChecked(true));
  }, []);

  useEffect(() => {
    const onRequired = () => {
      setUser(null);
      setDialogOpen(true);
    };
    window.addEventListener(AUTH_REQUIRED_EVENT, onRequired);
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, onRequired);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const u = await apiLogin(username, password);
    setUser(u);
    setDialogOpen(false);
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      setUser(null);
    }
  }, []);

  return {
    user,
    checked,
    dialogOpen,
    openDialog: () => setDialogOpen(true),
    closeDialog: () => setDialogOpen(false),
    login,
    logout,
  };
}
