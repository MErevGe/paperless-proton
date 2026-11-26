# Paperless Proton

Import email attachments from Proton Mail into Paperless-ngx. This container provides an encrypted IMAP connection and supports **amd64** and **arm64**.

You need a paid Proton Mail plan, Docker with Compose, and an existing Paperless installation.

## 1. Get the image

### Build locally

From this project directory:

```sh
docker build -t paperless-proton:local .
cp .env.example .env
```

Keep `BRIDGE_IMAGE=paperless-proton:local` in `.env`.

### Use a published image

After an image has been published, open the latest successful **Container** workflow on `main` in this repository's **Actions** tab. Copy the image address including `@sha256:…` from its summary.

For a private image, sign in to the GitHub Container Registry:

```sh
docker login ghcr.io
```

Enter your GitHub username and use a **personal access token (classic)** with `read:packages` as the password. Download `compose.yaml` and `.env.example` to the bridge server, then copy `.env.example` to `.env`. Set `BRIDGE_IMAGE` to the copied image address.

## 2. Configure the connection

Edit `.env`:

| Setting | Value |
| --- | --- |
| `BRIDGE_IMAGE` | Your local image or the published image address |
| `BRIDGE_HOST` | The bridge server's DNS name or IP address, reachable from Paperless |
| `BRIDGE_BIND_IP` | The bridge server's LAN or VPN IP address |
| `BRIDGE_PORT` | `1993` |

Example hostname: `bridge.example.internal`. Replace it with a name that resolves from the Paperless container. Use exactly the same hostname or IP when configuring Paperless; the certificate is issued for this value.

The default bind address, `127.0.0.1`, allows connections only from the bridge host. For Paperless on another server, set `BRIDGE_BIND_IP` to the bridge host's LAN or VPN address. Allow port `1993` only from the Paperless server. Do not forward it from the internet.

If using a published image, download it:

```sh
docker compose pull
```

## 3. Sign in to Proton

```sh
docker compose run --rm --no-deps bridge setup
```

Enter these commands in the Bridge console:

```text
updates autoupdates disable
login
info
```

`login` asks for your Proton credentials and, if enabled, a two-factor authentication code. Save the username and **Bridge password** shown by `info` for Paperless. For multiple accounts, use `help info` to select an account.

Enter `exit`, then start the service:

```sh
docker compose up -d
docker compose ps
```

Your session is saved in the Docker volume. Updates are installed by replacing the container image.

The `healthy` status confirms that encrypted IMAP responds. Confirm the Proton login and Paperless import with the test in step 5.

## 4. Trust the certificate in Paperless

Export the public certificate:

```sh
docker compose exec -T bridge python3 -m bridge_container certificate > proton-bridge-ca.crt
```

Copy `proton-bridge-ca.crt` to the Paperless Compose directory on your Paperless server. Add these entries to the Paperless service's existing `environment` and `volumes` sections:

```yaml
environment:
  PAPERLESS_EMAIL_CERTIFICATE_LOCATION: /etc/paperless/proton-bridge-ca.crt
volumes:
  - ./proton-bridge-ca.crt:/etc/paperless/proton-bridge-ca.crt:ro
```

Recreate Paperless there with `docker compose up -d`. If a separate worker container fetches mail, give it the same setting and certificate mount.

## 5. Add the email account

Open **Settings → Mail** in Paperless and add an account:

| Field | Value |
| --- | --- |
| IMAP server | Your `BRIDGE_HOST` value |
| Port | `1993`, or your chosen `BRIDGE_PORT` |
| Security | **SSL/TLS** |
| Username | The username from Bridge `info` |
| Password | The **Bridge password** from `info` |
| Certificate verification | Enabled |

Bridge `info` also displays local addresses and ports. Those apply inside the container. Use the server, port and security settings from the table above in Paperless.

Create `Paperless` and `Processed` folders in Proton Mail. Add a Paperless mail rule:

- Account: your Proton account
- Folder: `Folders/Paperless`
- Attachment filter: `*.pdf`
- Action: move to `Folders/Processed`

Move an email with a PDF attachment into the Proton `Paperless` folder. Paperless checks mail every ten minutes by default. Confirm that the document appears in Paperless; a moved email alone does not confirm a successful document import.

## Update

For a published image, put the new image address from GitHub Actions in `.env`:

```sh
docker compose pull
docker compose up -d
```

For a local image, rebuild it and recreate the service:

```sh
docker build -t paperless-proton:local .
docker compose up -d --force-recreate
```

## Sign in again

Stop the running service before opening the setup console:

```sh
docker compose stop bridge
docker compose run --rm --no-deps bridge setup
docker compose up -d
```

## Troubleshooting

- **Connection fails:** Check `docker compose ps`, `docker compose logs --tail=50 bridge`, DNS and firewall rules. The hostname must be reachable from the Paperless container. Recent Paperless versions must allow internal mail servers (`PAPERLESS_EMAIL_ALLOW_INTERNAL_HOSTS=true`).
- **Certificate error:** Use exactly the hostname or IP set in `BRIDGE_HOST`. Check the certificate mount in Paperless. Restart the bridge after changing `BRIDGE_HOST`. If you replace the data volume, export and trust the new public certificate.
- **Wrong password:** Use the Bridge password from `info`. If it changes after signing in again, update the account in Paperless.
- **Folder not found:** Proton usually exposes custom folders under `Folders/`. Paperless lists available folders in its logs when it cannot find the configured folder.
- **No documents imported:** Check the folder, attachment filter and maximum email age in the mail rule.

## Back up your data

Back up the `paperless-proton_bridge-data` volume while the bridge is stopped. It contains your session, keys, certificates and mail cache. Protect it like a password: anyone with access can use the saved session. `docker compose down -v` deletes this data.

The server certificate is renewed when needed during startup. Restart the container at least once every eleven months. The public CA certificate stays the same and is valid for ten years.

[Proton Bridge CLI](https://proton.me/support/bridge-cli-guide) · [Paperless email import](https://docs.paperless-ngx.com/usage/#incoming-email) · [Paperless mail certificates](https://docs.paperless-ngx.com/configuration/#PAPERLESS_EMAIL_CERTIFICATE_LOCATION)
