const $ = (selector) => document.querySelector(selector);
let csrf = "";
let configured = true;
let authenticated = false;
let state = null;
let initialized = false;
let accountSignature = "";
let noticeTimer;
let polling = false;

function notice(message, error = false) {
  const node = $("#notice");
  node.textContent = message;
  node.classList.toggle("error", error);
  node.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(
    () => {
      node.hidden = true;
    },
    error ? 12000 : 7000,
  );
}

async function request(path, data) {
  const options = { credentials: "same-origin", headers: {} };
  if (data !== undefined) {
    options.method = "POST";
    options.headers = {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf,
    };
    options.body = JSON.stringify(data);
  }
  const response = await fetch(`/api/${path}`, options);
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401) showGate();
    throw new Error(result.error || "The request failed. Please try again.");
  }
  return result;
}

async function busy(button, operation) {
  if (button.disabled) return;
  button.disabled = true;
  try {
    await operation();
  } catch (error) {
    notice(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function clearSecrets() {
  document.querySelectorAll('input[type="password"]').forEach((input) => {
    input.value = "";
  });
  $("#bridge-password").type = "password";
  $("#bridge-password").value = "";
  $("#bridge-user").value = "";
  $("#credentials-card").hidden = true;
}

function showGate() {
  authenticated = false;
  csrf = "";
  initialized = false;
  accountSignature = "";
  clearSecrets();
  $("#admin-form").hidden = false;
  $("#gate").hidden = false;
  $("#workspace").hidden = true;
  $("#gate-title").textContent = configured
    ? "Welcome back"
    : "Make yourself at home.";
  $("#gate-description").textContent = configured
    ? "Sign in to manage your connection."
    : "Create an admin password to set up your private connection.";
  $("#bootstrap").hidden = configured;
  $("#password-hint").hidden = configured;
  $("#setup-token").required = !configured;
  $("#admin-password").minLength = configured ? 1 : 12;
  $("#admin-password").autocomplete = configured
    ? "current-password"
    : "new-password";
  $("#admin-submit").textContent = configured ? "Sign in" : "Create workspace";
}

function showWorkspace() {
  $("#notice").hidden = true;
  authenticated = true;
  configured = true;
  $("#gate").hidden = true;
  $("#workspace").hidden = false;
  navigate();
}

function navigate() {
  const valid = ["overview", "proton", "connection", "paperless", "security"];
  const page = valid.includes(location.hash.slice(1))
    ? location.hash.slice(1)
    : "overview";
  document.querySelectorAll(".page").forEach((node) => {
    node.hidden = node.id !== page;
  });
  document.querySelectorAll("[data-page]").forEach((node) => {
    if (node.dataset.page === page) node.setAttribute("aria-current", "page");
    else node.removeAttribute("aria-current");
  });
  if (page !== "proton") {
    $("#bridge-password").value = "";
    $("#bridge-user").value = "";
    $("#credentials-card").hidden = true;
  }
}
window.addEventListener("hashchange", navigate);

async function confirmAction(title, description) {
  const dialog = $("#confirmation");
  $("#confirm-title").textContent = title;
  $("#confirm-description").textContent = description;
  dialog.returnValue = "cancel";
  dialog.showModal();
  return new Promise((resolve) => {
    dialog.addEventListener(
      "close",
      () => resolve(dialog.returnValue === "confirm"),
      { once: true },
    );
  });
}

function renderAccounts(accounts) {
  const signature = JSON.stringify(accounts);
  if (signature === accountSignature) return;
  accountSignature = signature;
  const parent = $("#accounts");
  parent.replaceChildren();
  const addressSelect = $("#proton-address");
  const selected = addressSelect.value;
  addressSelect.replaceChildren(
    new Option("Choose a connected email address", ""),
  );
  if (!accounts.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No accounts yet. Sign in to get started.";
    parent.append(empty);
  }
  for (const account of accounts) {
    const item = document.createElement("article");
    item.className = "account-item";
    const name = document.createElement("strong");
    name.textContent = account.username;
    const status = document.createElement("p");
    status.textContent = account.connected
      ? "Connected"
      : "Signed out — sign in again to reconnect.";
    const select = document.createElement("select");
    select.setAttribute("aria-label", `Email address for ${account.username}`);
    for (const address of account.addresses) {
      select.add(new Option(address, address));
      if (account.connected)
        addressSelect.add(
          new Option(address, JSON.stringify([account.id, address])),
        );
    }
    const actions = document.createElement("div");
    actions.className = "actions";
    const credentials = document.createElement("button");
    credentials.className = "secondary";
    credentials.textContent = "View Bridge credentials";
    credentials.disabled = !account.connected;
    credentials.addEventListener("click", () =>
      busy(credentials, async () => {
        const result = await request("proton/credentials", {
          id: account.id,
          address: select.value,
        });
        $("#bridge-user").value = result.username;
        $("#bridge-password").value = result.password;
        $("#bridge-password").type = "password";
        $("#reveal-password").textContent = "Show password";
        $("#credentials-card").hidden = false;
        $("#credentials-card").scrollIntoView({
          behavior: "smooth",
          block: "nearest",
        });
      }),
    );
    const logout = document.createElement("button");
    logout.className = "quiet";
    logout.textContent = "Disconnect";
    logout.disabled = !account.connected;
    logout.addEventListener("click", () =>
      busy(logout, async () => {
        if (
          !(await confirmAction(
            "Disconnect Proton?",
            "Paperless will stop receiving mail from this account until you sign in again.",
          ))
        )
          return;
        await request("proton/logout", { id: account.id });
        clearSecrets();
        notice("Sign-out requested.");
        await refresh();
      }),
    );
    actions.append(credentials, logout);
    item.append(name, status, select, actions);
    parent.append(item);
  }
  addressSelect.value =
    selected ||
    JSON.stringify([state.settings.proton_id, state.settings.address]);
}

function renderLogin(login) {
  const step = login.step;
  $("#proton-form").hidden = !["idle", "complete", "error"].includes(step);
  $("#challenge-form").hidden = ![
    "two_factor",
    "mailbox_password",
    "verification",
  ].includes(step);
  $("#login-progress").hidden = step !== "waiting";
  $("#cancel-login").hidden = ["idle", "complete", "error"].includes(step);
  $("#login-error").hidden = !login.error;
  $("#login-error").textContent = login.error || "";
  const labels = {
    two_factor: "Authenticator code",
    mailbox_password: "Mailbox password",
    verification: "Proton password",
  };
  $("#challenge-label").textContent = labels[step] || "Code or password";
  $("#challenge-description").textContent =
    step === "verification"
      ? "Complete Proton's verification, then enter your Proton password again to continue."
      : "Complete the next step to finish signing in.";
  $("#challenge-password").autocomplete =
    step === "two_factor" ? "one-time-code" : "current-password";
  const link = $("#verification-link");
  link.hidden = step !== "verification";
  if (step === "verification") link.href = login.url;
  else link.removeAttribute("href");
}

function fillSettings(settings) {
  $("#bridge-host").value = settings.host;
  $("#bridge-port").value = settings.port;
  for (const input of $("#paperless-form").elements) {
    if (!input.name || input.name === "token") continue;
    if (input.type === "checkbox") input.checked = settings[input.name];
    else input.value = settings[input.name] ?? "";
  }
  $("#proton-address").value = JSON.stringify([
    settings.proton_id,
    settings.address,
  ]);
}

async function refresh() {
  if (!authenticated || polling) return;
  polling = true;
  try {
    state = await request("state");
    $("#service-badge").textContent = state.ready
      ? "Bridge running"
      : "Bridge starting";
    const connected = state.accounts.filter(
      (account) => account.connected,
    ).length;
    $("#account-summary").textContent = connected
      ? `${connected} account${connected === 1 ? "" : "s"} connected`
      : "No account connected";
    $("#bridge-summary").textContent =
      `${state.active_host}:${state.settings.port}`;
    $("#paperless-summary").textContent = state.settings.rule_id
      ? "Import rule configured"
      : state.settings.paperless_url
        ? "Ready to test"
        : "Not configured";
    $("#paperless-detail").textContent = state.settings.rule_id
      ? "Confirm your first document in Paperless."
      : "Connect your document archive.";
    $("#restart-notice").hidden = state.active_host === state.settings.host;
    renderAccounts(state.accounts);
    renderLogin(state.login);
    if (!initialized) {
      fillSettings(state.settings);
      initialized = true;
    }
  } finally {
    polling = false;
  }
}

$("#admin-form").addEventListener("submit", (event) => {
  event.preventDefault();
  busy($("#admin-submit"), async () => {
    const data = { password: $("#admin-password").value };
    if (!configured) data.setup_token = $("#setup-token").value;
    clearSecrets();
    const result = await request(configured ? "login" : "setup", data);
    csrf = result.csrf;
    showWorkspace();
    await refresh();
  });
});

$("#sign-out").addEventListener("click", () =>
  busy($("#sign-out"), async () => {
    await request("logout", {});
    showGate();
  }),
);

for (const [id, path] of [
  ["proton-form", "proton/login"],
  ["challenge-form", "proton/answer"],
  ["connection-form", "connection"],
  ["password-form", "password"],
]) {
  $(`#${id}`).addEventListener("submit", (event) => {
    event.preventDefault();
    busy(event.target.querySelector("button"), async () => {
      const payload = Object.fromEntries(new FormData(event.target));
      event.target
        .querySelectorAll('input[type="password"]')
        .forEach((input) => {
          input.value = "";
        });
      const result = await request(path, payload);
      if (path === "password") {
        showGate();
        notice("Password changed. Sign in again.");
        return;
      }
      if (path === "connection") notice(result.message);
      await refresh();
    });
  });
}

$("#cancel-login").addEventListener("click", () =>
  busy($("#cancel-login"), async () => {
    await request("proton/cancel", {});
    clearSecrets();
    await refresh();
  }),
);
$("#hide-credentials").addEventListener("click", clearSecrets);
$("#reveal-password").addEventListener("click", () => {
  const input = $("#bridge-password");
  input.type = input.type === "password" ? "text" : "password";
  $("#reveal-password").textContent =
    input.type === "password" ? "Show password" : "Hide password";
});

async function savePaperless() {
  const form = $("#paperless-form");
  if (!form.reportValidity()) return false;
  const payload = Object.fromEntries(new FormData(form));
  const [id, address] = JSON.parse($("#proton-address").value);
  payload.proton_id = id;
  payload.address = address;
  payload.allow_http = $("#allow-http").checked;
  payload.maximum_age = Number(payload.maximum_age);
  $("#paperless-token").value = "";
  await request("paperless/save", payload);
  await refresh();
  return true;
}

$("#paperless-form").addEventListener("submit", (event) => {
  event.preventDefault();
  busy(event.submitter, async () => {
    if (await savePaperless()) notice("Paperless settings saved.");
  });
});
for (const action of ["test", "apply"]) {
  $(`#${action}-paperless`).addEventListener("click", () =>
    busy($(`#${action}-paperless`), async () => {
      if (!$("#paperless-form").reportValidity()) return;
      if (
        action === "apply" &&
        !(await confirmAction(
          "Apply to Paperless?",
          "This will create or update the email account and enable the import rule. Matching attachments will be imported and processed email moved to the destination folder.",
        ))
      )
        return;
      if (!(await savePaperless())) return;
      $("#paperless-result").hidden = false;
      $("#paperless-result").textContent = "Connecting to Paperless…";
      try {
        const result = await request(`paperless/${action}`, {});
        $("#paperless-result").textContent = result.message;
        await refresh();
      } catch (error) {
        $("#paperless-result").textContent = error.message;
        throw error;
      }
    }),
  );
}

async function start() {
  try {
    const session = await request("session");
    configured = session.configured;
    csrf = session.csrf;
    if (session.authenticated) {
      showWorkspace();
      await refresh();
    } else showGate();
  } catch (error) {
    notice(error.message, true);
  }
}
await start();
setInterval(() => {
  if (authenticated && !document.hidden)
    refresh().catch((error) => notice(error.message, true));
}, 3000);
