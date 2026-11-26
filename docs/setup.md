# Setup

[Back to README](../readme.md) · [Import profiles](import-profiles.md) · [Troubleshooting](maintenance.md#troubleshooting)

This guide takes you from a new Docker installation to a Bridge connection that Paperless can trust.

## Before you begin

Check the [requirements](../readme.md#requirements). You need shell access to the Bridge server and access to your Paperless Compose configuration. For a new installation, download this repository using **Code → Download ZIP**, or clone it, then open its directory.

Run the commands from that directory on the Bridge server unless a step says otherwise. To update an existing installation, follow [Update](maintenance.md#update).

## 1. Start the container

Create the configuration for a new installation:

```sh
cp .env.example .env
```

### Choose an image

**Build locally:** keep `BRIDGE_IMAGE=paperless-proton:local` in `.env` and run:

```sh
docker build -t paperless-proton:local .
```

**Use a published version:** open the successful **Container** workflow for your chosen version in the repository's **Actions** tab. Copy the full image address, including `@sha256:…`, from its summary into `BRIDGE_IMAGE` in `.env`.

Published images are available only for signed version tags, such as `v1.2.3` or `v1.2.3-rc.1`, after checks pass for both architectures. Branches, pull requests, and manual runs do not publish release images. If no version has been published yet, build locally.

For a private image, sign in to the registry before pulling it:

```sh
docker login ghcr.io
```

Enter your GitHub username and a personal access token **(classic)** with `read:packages` as the password. Your account must have access to the image. See [GitHub's registry authentication guide](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry#authenticating-to-the-container-registry).

### Set the addresses

Edit `.env`. All example hostnames below are placeholders; replace them with addresses that resolve on your network.

| Setting          | What to enter                                                                                   |
| ---------------- | ----------------------------------------------------------------------------------------------- |
| `BRIDGE_IMAGE`   | Your local image name or the published image address with its digest                            |
| `BRIDGE_HOST`    | The Bridge server's hostname or IP, reachable from Paperless, such as `bridge.example.internal` |
| `BRIDGE_BIND_IP` | The Bridge server's LAN or VPN IP for incoming IMAP connections                                 |
| `BRIDGE_PORT`    | Published IMAP port; default `1993`                                                             |
| `WEB_ORIGIN`     | Exact browser address; initially `https://localhost:8443`                                       |
| `WEB_BIND_IP`    | Keep `127.0.0.1` for browser access from the Bridge host or an SSH tunnel                       |
| `WEB_PORT`       | Published web port; default `8443`                                                              |

The example file binds both ports to `127.0.0.1`. Set `BRIDGE_BIND_IP` to a LAN or VPN IP that Paperless can reach, and allow the IMAP port only from the Paperless server. Inside a container, `localhost` refers to that container, even when both applications run on the same host.

For a published image, first run `docker compose pull`. Then start the service:

```sh
docker compose up -d
docker compose ps
```

Wait for the container to become **healthy**. If it does not, check `docker compose logs --tail=50 bridge`.

## 2. Open the web interface

Export the web interface's public certificate authority:

```sh
docker compose exec -T bridge python3 -m bridge_container web-certificate > paperless-proton-web-ca.crt
```

Import this file into your browser's certificate authorities and trust it for websites. Open the HTTPS address configured as `WEB_ORIGIN`; the default is **https://localhost:8443** on the Bridge host.

Retrieve the one-time setup token:

```sh
docker compose exec bridge python3 -m bridge_container web-setup-token
```

Enter it in the web interface and choose an admin password with at least **12 characters**. This password is separate from your Proton password. The setup token stops working once setup is complete.

Under **Appearance**, choose **Light**, **Dark**, or **System**. The browser remembers your choice; system mode follows your device.

### Access from another computer

To keep the web port private, run this SSH tunnel on the computer with your browser. This example uses the default `WEB_PORT` and `WEB_ORIGIN`:

```sh
ssh -L 8443:127.0.0.1:8443 user@bridge.example.internal
```

Copy the exported web CA to that computer and trust it in the browser. With these defaults, open `https://localhost:8443`. If you use a different `WEB_PORT`, replace both `8443` values in the tunnel command and set `WEB_ORIGIN` to `https://localhost:<your-port>` before recreating the container.

For direct LAN or VPN access, set `WEB_BIND_IP` to the Bridge server's LAN or VPN IP and `WEB_ORIGIN` to the exact HTTPS address you will open, such as `https://bridge.example.internal:8443`. The port must match `WEB_PORT`. Run `docker compose up -d` to recreate the container and trust the web CA on that computer. Keep the web port off the public internet.

## 3. Connect Proton Mail

Open **Proton account** and sign in. Enter an authenticator code or separate mailbox password if required. If Proton requests human verification, follow the link and continue signing in.

If the account uses only a hardware security key, enable authenticator codes in Proton first. The remote container cannot use a security key attached to your browser.

The session is saved in the data volume. You can view Bridge credentials or disconnect an account from this screen. Paperless uses the generated **Bridge password**, not your Proton password.

## 4. Let Paperless trust Bridge

Open **Bridge connection** and confirm the hostname or IP that Paperless will use. If you change it, save and restart Bridge:

```sh
docker compose restart bridge
```

The address saved here takes precedence over the initial `BRIDGE_HOST` value. Change published ports and bind addresses in `.env`, then run `docker compose up -d`.

Click **Download certificate**. Copy `proton-bridge-ca.crt` to the Paperless Compose directory on the Paperless server.

| Certificate                   | Where to trust it                     |
| ----------------------------- | ------------------------------------- |
| `paperless-proton-web-ca.crt` | Your browser, for the admin interface |
| `proton-bridge-ca.crt`        | Paperless, for the IMAP connection    |

Add these entries to the Paperless service's **existing** environment and volume settings:

```yaml
environment:
  PAPERLESS_EMAIL_CERTIFICATE_LOCATION: /etc/paperless/proton-bridge-ca.crt
  PAPERLESS_EMAIL_ALLOW_INTERNAL_HOSTS: "true"
volumes:
  - ./proton-bridge-ca.crt:/etc/paperless/proton-bridge-ca.crt:ro
```

On the Paperless server, run `docker compose up -d` to recreate Paperless. A separate worker that fetches mail needs the same certificate mount and environment settings. Keep certificate verification enabled. See [Paperless mail certificates](https://docs.paperless-ngx.com/configuration/#PAPERLESS_EMAIL_CERTIFICATE_LOCATION).

## Next steps

[Create an import profile and import your first document](import-profiles.md). The connection is ready when **Test connection** succeeds and shows the intended **Document owner**. Finish with a test import to verify document processing and permissions.
