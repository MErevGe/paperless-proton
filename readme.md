# Paperless Proton

Connect Proton Mail to Paperless-ngx through an encrypted IMAP connection. Use the web interface to sign in to Proton, configure the connection, and create an email account and import rule in Paperless. Each separate Proton account has its own import profile with a Paperless API token and folders.

You need a **paid Proton Mail plan**, Docker with Compose, and an existing Paperless-ngx installation. Images support **amd64** and **arm64**.

## 1. Start the container

Build the image in this project directory:

```sh
docker build -t paperless-proton:local .
cp .env.example .env
```

Edit `.env`:

| Setting | What to enter |
| --- | --- |
| `BRIDGE_IMAGE` | Keep `paperless-proton:local` for a local build |
| `BRIDGE_HOST` | The bridge server's hostname or IP, reachable from Paperless |
| `BRIDGE_BIND_IP` | The bridge server's LAN or VPN IP |
| `BRIDGE_PORT` | Published IMAP port, normally `1993` |
| `WEB_ORIGIN` | Browser address, initially `https://localhost:8443` |
| `WEB_BIND_IP` | Keep `127.0.0.1` for access from the bridge host |
| `WEB_PORT` | Published web port, normally `8443` |

Example bridge hostname: `bridge.example.internal`. Use a name that resolves from the Paperless container.

If Paperless runs on another server, change `BRIDGE_BIND_IP` from `127.0.0.1` to the bridge server's LAN or VPN IP. Allow the IMAP port only from the Paperless server.

```sh
docker compose up -d
docker compose ps
```

Wait for the container to become healthy.

### Using a published image

Images are published for signed version tags such as `v1.2.3` or `v1.2.3-rc.1`, after all checks pass for amd64 and arm64. Open the successful **Container** workflow for your chosen version in the repository's **Actions** tab. Copy the image address, including `@sha256:…`, from its summary into `BRIDGE_IMAGE`. The summary also shows the version tag.

Branch pushes, pull requests and manual runs execute the checks, including temporary images for the container and browser tests. They do not publish release images. Release tags must have a signature verified by GitHub.

For a private image, run `docker login ghcr.io`. Enter your GitHub username and a **personal access token (classic)** with `read:packages` as the password. Then run:

```sh
docker compose pull
docker compose up -d
```

## 2. Open the web interface

The web interface uses HTTPS. Export its public certificate authority:

```sh
docker compose exec -T bridge python3 -m bridge_container web-certificate > paperless-proton-web-ca.crt
```

Import `paperless-proton-web-ca.crt` into your browser's certificate authorities and trust it for websites. Open **https://localhost:8443** on the bridge host.

Retrieve the one-time setup token:

```sh
docker compose exec bridge python3 -m bridge_container web-setup-token
```

Enter the token in the web interface and choose an admin password with at least 12 characters. The token stops working once setup is complete. This admin password is separate from your Proton password.

Use **Appearance** to choose **Light**, **Dark** or **System**. Your browser remembers the choice. **System** follows your device's appearance, and the logo lettering switches between black and white automatically.

### Opening the interface from another computer

You can keep the web port private and use an SSH tunnel:

```sh
ssh -L 8443:127.0.0.1:8443 user@bridge.example.internal
```

Copy the exported web certificate to that computer, trust it in its browser, and open `https://localhost:8443`.

For direct LAN or VPN access, set `WEB_BIND_IP` to the bridge server's LAN or VPN IP and `WEB_ORIGIN` to the exact HTTPS address you will open, such as `https://bridge.example.internal:8443`. Its port must match `WEB_PORT`. Recreate the container with `docker compose up -d` and trust its web certificate on that computer. Keep the web port off the public internet.

## 3. Connect Proton Mail

Open **Proton account** in the web interface and sign in. Enter an authenticator code and a separate mailbox password if your account requires them. If Proton requests human verification, follow the link and continue the sign-in.

An account that only uses a hardware security key needs an authenticator code enabled in Proton first. A security key connected to your browser cannot be used by the remote container.

Your Proton session is saved in the Docker volume. You can view the generated Bridge credentials or disconnect an account in this screen. Paperless uses the **Bridge password**, not your Proton password.

## 4. Prepare the Bridge connection

Open **Bridge connection**. Check the hostname or IP that Paperless will use. You can change it here; after saving, run:

```sh
docker compose restart bridge
```

The saved hostname takes precedence over the initial `BRIDGE_HOST` value. Published ports and bind addresses are changed in `.env` and take effect after `docker compose up -d`.

Download the **Bridge certificate** from this screen. This is the certificate for Paperless; the web interface has its own certificate.

Copy `proton-bridge-ca.crt` to your Paperless Compose directory. Add these entries to the Paperless service's existing configuration:

```yaml
environment:
  PAPERLESS_EMAIL_CERTIFICATE_LOCATION: /etc/paperless/proton-bridge-ca.crt
  PAPERLESS_EMAIL_ALLOW_INTERNAL_HOSTS: "true"
volumes:
  - ./proton-bridge-ca.crt:/etc/paperless/proton-bridge-ca.crt:ro
```

Recreate Paperless with `docker compose up -d` on its server. A separate worker that fetches email needs the same certificate and environment setting. Keep certificate verification enabled.

## 5. Set up an import profile for each account

This is a central admin interface. Connect each person's separate Proton account under **Proton account**, then configure its own profile under **Paperless setup**. Profiles can use the same Paperless server with different users, or different servers.

For each Proton account:

1. Create source and processed folders in Proton Mail, for example `Paperless` and `Processed`.
2. Select the **Proton account** at the top of **Paperless setup**.
3. Enter the Paperless URL, such as `https://paperless.example.internal`, and the API token of the Paperless user who should own the documents. That user needs permission to view, add and change email accounts and mail rules.
4. Choose the Proton email address and give the email account and rule recognizable names.
5. Choose the source and destination from the folders loaded from that Proton account. For example, select `Folders/Paperless` and `Folders/Processed`. After creating or renaming folders, click **Refresh folders**. The default filename filter imports `*.pdf` attachments.
6. Click **Test connection**. Check the displayed **Document owner**. The test verifies the token's user, existing account and rule ownership, and Paperless's SSL/TLS connection to Bridge. The Bridge address must be reachable from Paperless; `localhost` points to the Paperless container itself.
7. Click **Apply to Paperless** and confirm. The app creates an email account and enabled import rule owned by that Paperless user. The rule assigns new imported documents to the same user. Later applications update this profile's account and rule. Check the permissions of documents already in Paperless separately.

Repeat for the next account. Each profile keeps its own token, folders, filters, age limit and Paperless links. Switching profiles clears the token field; unsaved changes require confirmation before they are discarded. **Save settings** stores the selected profile in this app. **Apply to Paperless** also updates its rule in Paperless.

**Maximum email age** accepts 0 to 32767 days. Use 0 to include email of any age. Moving an old email into the source folder does not make it newer.

The API tokens are stored separately in the encrypted password store. After a successful save, the field clears and **API token saved securely** appears. Leave it empty to keep that profile's saved token, including after a restart. If saving fails, the entered token stays in the field so you can retry. You can replace a token with another token for the same Paperless user. Changing the Paperless URL requires a token again and creates new links on that server; accounts and rules on the previous server remain there.

If Paperless uses a private HTTPS certificate, paste its CA certificate under **Connection options**. If it only supports HTTP on your trusted LAN or VPN, explicitly enable HTTP there. HTTP sends the API token and Bridge password without transport encryption.

### Who can see the documents?

Use a separate, regular Paperless user for each person's profile. A token belongs to a user; it does not limit that user to one folder. The app checks ownership before updating linked accounts and rules. It refuses a different user's token once a profile has been linked, including a superuser token. For an existing setup without an owner or with the wrong owner, correct the email account and rule's ownership in Paperless before applying the profile.

Paperless superusers can see all documents. Paperless workflows can override ownership or grant access, and shared tags or other objects can reveal metadata. Review your workflows and document permissions in Paperless. The central admin interface and the Bridge server also have access to all connected accounts; profiles do not hide mail from those administrators. See [Paperless permissions](https://docs.paperless-ngx.com/usage/#permissions).

Profiles separate **Proton accounts**. Multiple addresses within one Proton account may share a mailbox; choosing an address does not create isolation between those addresses.

Move an email with a PDF attachment into the source folder. Paperless checks mail every ten minutes by default. Sign in as the intended Paperless user and confirm that the document appears with the correct owner. A successful connection test does not verify document processing or permissions applied by your workflows.

## Update

For a local image:

```sh
docker build -t paperless-proton:local .
docker compose up -d --force-recreate
```

For a published image, update `BRIDGE_IMAGE` in `.env`, then run `docker compose pull` and `docker compose up -d`.

Accounts, settings and certificates remain in the data volume. When updating from a version with one shared Paperless setup, it is automatically kept as the profile of the previously selected Proton account. Its token, folders and existing Paperless links are preserved. The previous settings are backed up as `web/settings.before-profiles.json` in that volume. Test this profile and check its displayed owner before applying it. Configure the other accounts separately. Bridge's own automatic updates are disabled; updates come from the container image.

## Admin password and recovery

Change the admin password under **Admin access**. Browser sessions expire after one hour. Changing the password signs out every browser session.

If you forget the password, reset admin access on the server:

```sh
docker compose stop bridge
docker compose run --rm --no-deps bridge reset-web-access
docker compose up -d
docker compose exec bridge python3 -m bridge_container web-setup-token
```

Use the new token to choose a password. Your Proton accounts and Paperless settings remain saved.

## Troubleshooting

- **Web page rejects the address:** Open exactly the address in `WEB_ORIGIN`. Check that its port matches `WEB_PORT`, then recreate the container after changes.
- **Browser certificate error:** Trust the web CA exported in step 2. The browser address must match `WEB_ORIGIN`.
- **Bridge is starting:** Wait briefly, then check `docker compose logs --tail=50 bridge` and the server's internet connection.
- **Paperless connection fails:** Check the bridge hostname, port, DNS and firewall from the Paperless server. Use a LAN or VPN address for both the saved Bridge hostname and `BRIDGE_BIND_IP`; `localhost` and `127.0.0.1` cannot connect two servers. Recent Paperless versions must allow internal mail servers (`PAPERLESS_EMAIL_ALLOW_INTERNAL_HOSTS=true`).
- **Paperless returns HTTP 500:** Check the Paperless container logs immediately after the test. Confirm the Bridge address is reachable and that the certificate path inside Paperless points to a readable certificate file, not a missing file or directory. Recreate Paperless after changing its mounts or environment.
- **Proton folders are missing:** Check the selected account and email address, then click **Refresh folders**. Create folders in Proton Mail first. If a saved folder was deleted or renamed, select its replacement before saving.
- **Paperless certificate error:** Check the certificate mount and `PAPERLESS_EMAIL_CERTIFICATE_LOCATION`, including any separate mail worker. Restart the bridge after changing its hostname.
- **Paperless rejects the API token:** Check the token and the user's permissions. Include a path prefix in the Paperless URL if your installation uses one.
- **A profile belongs to another Paperless user:** Restore the original user's token. A superuser cannot silently take over an existing profile. If the linked account or rule has no owner or a different owner, correct it in Paperless before trying again.
- **A linked account or rule is missing:** Check its owner and permissions in Paperless. The app only clears deleted links for the profile's original user if that user is a superuser. Restore deleted objects or have an administrator of your Paperless server resolve the setup.
- **An account was created but the rule failed:** Correct the reported problem and apply again. The saved account is reused. If an object was deleted manually in Paperless, restore it or adjust the setup there.
- **No documents imported:** Check the Proton folder names, filename filter, maximum email age, and Paperless logs. Custom Proton folders normally start with `Folders/`.
- **Proton needs another login:** Sign in again under **Proton account**, then apply the Paperless setup again if the Bridge password changed.

## Back up your data

Back up the `paperless-proton_bridge-data` volume while the container is stopped. It contains your Proton session, encryption keys, API tokens, settings, certificates and mail cache. Protect it like a password. `docker compose down -v` deletes this data.

Server certificates renew during startup. Restart the container at least once every eleven months. The public CA certificates stay the same and are valid for ten years. Replacing the data volume also replaces both certificate authorities; trust the new certificates in your browser and in Paperless.

[Proton Bridge help](https://proton.me/support/bridge) · [Paperless email import](https://docs.paperless-ngx.com/usage/#incoming-email) · [Paperless mail certificates](https://docs.paperless-ngx.com/configuration/#PAPERLESS_EMAIL_CERTIFICATE_LOCATION)
