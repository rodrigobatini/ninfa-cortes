(() => {
  const key = "ninfa-theme";
  const system = window.matchMedia("(prefers-color-scheme: dark)");
  let preference;
  try { preference = localStorage.getItem(key); } catch { /* Use the system theme when storage is unavailable. */ }
  if (preference !== "dark" && preference !== "light") preference = null;

  function applyTheme() {
    const theme = preference ?? (system.matches ? "dark" : "light");
    const dark = theme === "dark";
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]').content = dark ? "#211d2b" : "#f6f0e8";
    const button = document.getElementById("toggle-theme");
    if (!button) return;
    button.setAttribute("aria-pressed", String(dark));
    button.title = dark ? "Usar tema claro" : "Usar tema escuro";
    button.firstElementChild.textContent = dark ? "☀" : "☾";
    window.chrome?.webview?.postMessage(dark ? "theme-dark" : "theme-light");
  }

  applyTheme();
  system.addEventListener("change", applyTheme);
  document.addEventListener("DOMContentLoaded", () => {
    applyTheme();
    document.getElementById("toggle-theme").addEventListener("click", () => {
      preference = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      try { localStorage.setItem(key, preference); } catch { /* Keep the choice for this session. */ }
      applyTheme();
    });
  });
})();
