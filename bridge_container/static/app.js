const $ = (selector) => document.querySelector(selector);
let csrf = "";
let configured = true;
let authenticated = false;
let state = null;
let initialized = false;
let accountSignature = "";
let noticeTimer;
let polling = null;
let stateRevision = 0;
let folderAccount = null;
let folderRequest = 0;
let foldersReady = false;
let activeProfile = "";
let profileDirty = false;
let profileBusy = false;
let profileSyncPending = false;
let profileSignature = "";

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
  profileSignature = "";
  folderAccount = null;
  folderRequest += 1;
  foldersReady = false;
  activeProfile = "";
  profileDirty = false;
  profileSyncPending = false;
  stateRevision += 1;
  updateProfileControls();
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
    const configure = document.createElement("button");
    configure.className = "secondary";
    configure.textContent = "Configure Paperless";
    configure.addEventListener("click", () =>
      busy(configure, async () => {
        if (await selectProfile(account.id)) location.hash = "paperless";
      }),
    );
    actions.append(configure, credentials, logout);
    item.append(name, status, select, actions);
    parent.append(item);
  }
}

function currentProfile() {
  return {
    ...state.profile_defaults,
    ...state.profiles[activeProfile],
    proton_id: activeProfile,
  };
}

function renderProfileStatus() {
  const profile = currentProfile();
  $("#profile-status").textContent = !activeProfile
    ? "Connect a Proton account to create its import profile."
    : profileSyncPending
      ? "Settings saved. Refreshing this profile…"
      : profileDirty
        ? "Unsaved changes in this profile."
        : profile.applied
          ? "Applied to Paperless. Each account has its own import rule."
          : profile.paperless_url
            ? "Settings saved. Test and apply this profile to Paperless."
            : "New profile. Enter this account's Paperless token and folders.";
  $("#token-status").textContent = profile.token_saved
    ? "API token saved securely for this profile. Leave the field blank to keep it."
    : "No API token saved for this profile.";
  $("#paperless-token").placeholder = profile.token_saved
    ? "Saved — enter a new token to replace it"
    : "Paste this user's Paperless API token";
  $("#paperless-user").textContent = $("#paperless-token").value
    ? "Test the connection to verify the entered token’s Paperless user."
    : profile.paperless_user
      ? `Document owner: ${profile.paperless_user.username}${profile.paperless_user.is_superuser ? " (superuser)" : ""}.`
      : "Test the connection to verify the Paperless user who will own imported documents.";
}

function renderProfileChoices() {
  const select = $("#profile-account");
  const ids = new Set([
    ...state.accounts.map((account) => account.id),
    ...Object.keys(state.profiles),
    ...(activeProfile ? [activeProfile] : []),
  ]);
  const choices = [...ids].map((id) => {
    const account = state.accounts.find((item) => item.id === id);
    const label =
      account?.username || state.profiles[id]?.address || "Unavailable account";
    return [id, `${label}${account?.connected ? "" : " — disconnected"}`];
  });
  const signature = JSON.stringify(choices);
  if (signature !== profileSignature) {
    select.replaceChildren(new Option("Choose a Proton account", ""));
    for (const [id, label] of choices) select.add(new Option(label, id));
    profileSignature = signature;
  }
  if (!activeProfile) {
    activeProfile =
      Object.keys(state.profiles)[0] ||
      state.accounts.find((account) => account.connected)?.id ||
      "";
    fillProfile();
  }
  select.value = activeProfile;
  if (!profileBusy) renderAddressChoices();
  renderProfileStatus();
}

function renderAddressChoices(reset = false) {
  const settings = currentProfile();
  const account = state.accounts.find((item) => item.id === activeProfile);
  const addresses =
    account?.connected || account?.addresses.length
      ? account.addresses
      : settings.address
        ? [settings.address]
        : [];
  const select = $("#proton-address");
  const previous = select.value;
  const values = addresses.map((address) =>
    JSON.stringify([activeProfile, address]),
  );
  if (
    JSON.stringify([...select.options].map((option) => option.value)) !==
    JSON.stringify(["", ...values])
  ) {
    select.replaceChildren(new Option("Choose an email address", ""));
    for (const [index, address] of addresses.entries())
      select.add(new Option(address, values[index]));
  }
  select.value = reset
    ? JSON.stringify([activeProfile, settings.address || addresses[0]])
    : previous;
  if (!reset && previous !== select.value) profileDirty = true;
}

function fillProfile() {
  const settings = currentProfile();
  $("#paperless-token").value = "";
  $("#paperless-result").hidden = true;
  for (const input of $("#paperless-form").elements) {
    if (!input.name || input.name === "token") continue;
    if (input.type === "checkbox") input.checked = settings[input.name];
    else input.value = settings[input.name] ?? "";
  }
  renderAddressChoices(true);
  folderAccount = null;
  folderRequest += 1;
  foldersReady = false;
  profileDirty = false;
  renderProfileStatus();
  void loadFolders();
}

async function selectProfile(id) {
  if (profileSyncPending)
    throw new Error("Wait for the saved profile to refresh before switching.");
  if (profileBusy)
    throw new Error("Wait for the current profile operation to finish.");
  if (id === activeProfile) return true;
  if (
    profileDirty &&
    !(await confirmAction(
      "Switch import profile?",
      "Unsaved settings and the entered token will be discarded.",
    ))
  ) {
    $("#profile-account").value = activeProfile;
    return false;
  }
  activeProfile = id;
  $("#profile-account").value = id;
  fillProfile();
  return true;
}

$("#profile-account").addEventListener("change", () =>
  busy($("#profile-account"), () => selectProfile($("#profile-account").value)),
);
for (const event of ["input", "change"])
  $("#paperless-form").addEventListener(event, () => {
    profileDirty = true;
    renderProfileStatus();
  });

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

async function loadFolders(force = false) {
  const selected = $("#proton-address").value;
  if (!force && selected === folderAccount) return;
  const sameAccount = selected === folderAccount;
  folderAccount = selected;
  const revision = ++folderRequest;
  foldersReady = false;
  const selects = [$("#source-folder"), $("#destination-folder")];
  const settings = currentProfile();
  const savedAccount = JSON.stringify([activeProfile, settings.address]);
  const previous = selects.map((input) =>
    sameAccount && input.value
      ? input.value
      : selected === savedAccount
        ? settings[input.name]
        : "",
  );
  for (const input of selects) {
    input.disabled = true;
    input.replaceChildren(
      new Option(
        selected ? "Loading folders…" : "Choose an email address first",
        "",
      ),
    );
  }
  $("#folder-status").textContent = selected
    ? "Loading folders from Proton…"
    : "Choose a connected email address to load its folders.";
  if (!selected) return;
  try {
    const [id, address] = JSON.parse(selected);
    const result = await request("proton/folders", { id, address });
    if (revision !== folderRequest) return;
    for (const [index, input] of selects.entries()) {
      input.replaceChildren(new Option("Choose a folder", ""));
      for (const name of result.folders) input.add(new Option(name, name));
      input.value = result.folders.includes(previous[index])
        ? previous[index]
        : "";
      input.disabled = !result.folders.length;
    }
    foldersReady = result.folders.length > 0;
    const missing = previous.some(
      (name) => name && !result.folders.includes(name),
    );
    $("#folder-status").textContent = !foldersReady
      ? "No selectable folders found. Create folders in Proton Mail, then refresh."
      : missing
        ? "A previously selected folder no longer exists. Choose a folder from the list."
        : `${result.folders.length} folders loaded from Proton. Choose different source and destination folders.`;
  } catch (error) {
    if (revision !== folderRequest) return;
    for (const input of selects)
      input.replaceChildren(new Option("Folders unavailable", ""));
    $("#folder-status").textContent = error.message;
  }
}

$("#proton-address").addEventListener("change", () => loadFolders());
$("#refresh-folders").addEventListener("click", () =>
  busy($("#refresh-folders"), () => loadFolders(true)),
);

async function refresh(fresh = false) {
  if (!authenticated) return;
  if (polling) {
    if (!fresh) return;
    await Promise.allSettled([polling]);
    return refresh(true);
  }
  const revision = stateRevision;
  polling = request("state");
  try {
    const result = await polling;
    if (revision !== stateRevision) return;
    state = result;
    if (profileSyncPending) {
      profileSyncPending = false;
      profileDirty = false;
    }
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
    const profiles = Object.values(state.profiles);
    const applied = profiles.filter((profile) => profile.applied).length;
    $("#paperless-summary").textContent =
      `${profiles.length} import profile${profiles.length === 1 ? "" : "s"}`;
    $("#paperless-detail").textContent =
      `${applied} applied to Paperless. Manage each account separately.`;
    $("#restart-notice").hidden = state.active_host === state.settings.host;
    renderAccounts(state.accounts);
    renderLogin(state.login);
    renderProfileChoices();
    updateProfileControls();
    if (!initialized) {
      $("#bridge-host").value = state.settings.host;
      $("#bridge-port").value = state.settings.port;
      initialized = true;
    }
    void loadFolders();
  } finally {
    polling = null;
  }
}

$("#admin-form").addEventListener("submit", (event) => {
  event.preventDefault();
  void busy($("#admin-submit"), async () => {
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
    void busy(event.target.querySelector("button"), async () => {
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

async function paperlessAction(action, button) {
  await busy(button, async () => {
    const form = $("#paperless-form");
    if (!activeProfile || !foldersReady) {
      notice(
        "Load the Proton folders and choose a source and destination before saving.",
        true,
      );
      return;
    }
    if (!form.reportValidity()) return;
    const payload = Object.fromEntries(new FormData(form));
    const [id, address] = JSON.parse($("#proton-address").value);
    payload.proton_id = id;
    payload.address = address;
    payload.allow_http = $("#allow-http").checked;
    payload.maximum_age = Number(payload.maximum_age);
    profileBusy = true;
    updateProfileControls();
    try {
      if (
        action === "apply" &&
        !(await confirmAction(
          "Apply this profile to Paperless?",
          "This account's matching attachments will be imported for the user identified by its API token. Processed email will be moved to this profile's destination folder.",
        ))
      )
        return;
      await request("paperless/save", payload);
      $("#paperless-token").value = "";
      await refreshSavedProfile();
      profileDirty = false;
      renderProfileStatus();
      if (action === "save") {
        notice("Import profile saved.");
        return;
      }
      $("#paperless-result").hidden = false;
      $("#paperless-result").textContent = "Connecting to Paperless…";
      try {
        const result = await request(`paperless/${action}`, { proton_id: id });
        $("#paperless-result").textContent = result.message;
        await refreshSavedProfile();
      } catch (error) {
        $("#paperless-result").textContent = error.message;
        throw error;
      }
    } finally {
      profileBusy = false;
      updateProfileControls();
    }
  });
}

function updateProfileControls() {
  const disabled = profileBusy || profileSyncPending;
  $("#profile-fields").disabled = disabled;
  $("#profile-account").disabled = disabled;
}

async function refreshSavedProfile() {
  profileSyncPending = true;
  stateRevision += 1;
  updateProfileControls();
  renderProfileStatus();
  await refresh(true);
}

$("#paperless-form").addEventListener("submit", (event) => {
  event.preventDefault();
  void paperlessAction("save", event.submitter);
});
for (const action of ["test", "apply"]) {
  $(`#${action}-paperless`).addEventListener("click", () =>
    paperlessAction(action, $(`#${action}-paperless`)),
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
