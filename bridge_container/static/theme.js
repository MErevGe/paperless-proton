(() => {
  const key = "paperless-proton-theme";
  const choices = ["system", "light", "dark"];
  const system = window.matchMedia("(prefers-color-scheme: dark)");
  let preference = "system";
  try {
    const saved = window.localStorage.getItem(key);
    if (choices.includes(saved)) preference = saved;
  } catch {
    preference = "system";
  }

  function apply() {
    document.documentElement.dataset.theme =
      preference === "system"
        ? system.matches
          ? "dark"
          : "light"
        : preference;
    document.querySelectorAll("[data-theme-select]").forEach((select) => {
      select.value = preference;
    });
  }

  apply();
  system.addEventListener("change", apply);
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-theme-select]").forEach((select) => {
      select.addEventListener("change", () => {
        preference = select.value;
        apply();
        try {
          window.localStorage.setItem(key, preference);
        } catch {
          return;
        }
      });
    });
    apply();
  });
})();
