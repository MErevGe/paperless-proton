# Updates, recovery, and troubleshooting

[Back to README](../readme.md) · [Setup](setup.md) · [Import profiles](import-profiles.md)

Run commands from your Paperless Proton project directory unless stated otherwise.

## Update

Back up the data volume and `.env` before updating. Accounts, settings, and certificates remain in the volume when the container is recreated.

**Local image:** get the source files for the version you want, then rebuild and recreate:

```sh
docker build -t paperless-proton:local .
docker compose up -d --force-recreate
```

**Published image:** change `BRIDGE_IMAGE` in `.env` to the new version's image digest from the **Container** workflow summary, then run:

```sh
docker compose pull
docker compose up -d
```

Check `docker compose ps`, open the interface, and test each import profile. Bridge's own automatic updates are disabled; updates come from the container image.

### Migrating from one shared Paperless setup

The existing setup becomes the profile of the previously selected Proton account. Its token, folders, and Paperless links are preserved. The previous settings are backed up as `web/settings.before-profiles.json` inside the data volume.

Test that profile and check the displayed document owner before applying it. Configure other Proton accounts separately. The settings backup is not a substitute for a full volume backup.

## Back up and restore

The supplied Compose file sets the project name to `paperless-proton`, so its default volume is **`paperless-proton_bridge-data`**. If you override the project name with `-p` or `COMPOSE_PROJECT_NAME`, the volume is named `<project-name>_bridge-data`. Use the same project name when backing up and restoring.

List the volume used by your deployment:

```sh
docker compose volumes -q bridge
```

It contains the Proton session, encryption keys, API tokens, settings, certificates, and mail cache. Protect backups like passwords. Also keep your `.env` and Compose configuration; back up Paperless itself separately.

Stop Bridge before taking a volume backup with your normal backup tool:

```sh
docker compose stop bridge
```

After the backup finishes, restart it:

```sh
docker compose up -d
```

To restore, stop Bridge, restore the complete volume with its file permissions intact, and use the matching configuration and image version. Start the service and test the saved profiles. Restoring only the settings file does not restore the keys or credentials.

**`docker compose down -v` deletes the data volume.** Replacing the volume also replaces the certificate authorities; your browser and Paperless will need to trust the new certificates.

### Certificate renewal

Server certificates last one year. They renew at startup when fewer than 30 days remain, so restart the container during that renewal window. The public CA certificates stay the same during renewal and are valid for ten years.

## Admin password recovery

Change your password under **Admin access**. Browser sessions expire after one hour; changing the password signs out every session.

If you forget the password, reset access on the Bridge server:

```sh
docker compose stop bridge
docker compose run --rm --no-deps bridge reset-web-access
docker compose up -d
docker compose exec bridge python3 -m bridge_container web-setup-token
```

Use the new token to choose a password. Your Proton accounts and Paperless settings remain saved.

## Troubleshooting

Start with the container status and recent logs:

```sh
docker compose ps
docker compose logs --tail=50 bridge
```

For errors reported by Paperless, check the Paperless service and mail worker logs on its server immediately after reproducing the problem.

### Web interface and Proton sign-in

| Symptom                                   | What to check                                                                                                                                             |
| ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The web page rejects its address          | Open exactly `WEB_ORIGIN`, with the same port as `WEB_PORT`. Recreate the container after changes.                                                        |
| The browser reports a certificate error   | Trust the [web CA](setup.md#2-open-the-web-interface), and check that the browser address matches `WEB_ORIGIN`.                                           |
| Bridge keeps starting                     | Wait briefly, then check the container logs and the server's internet connection.                                                                         |
| Proton needs another login                | Sign in under **Proton account**. If the Bridge password changed, apply the affected profile again.                                                       |
| Proton folders are missing                | Select the correct account and address, create the folders in Proton, then click **Refresh folders**. Select replacements for renamed or deleted folders. |
| The API token field is empty after saving | This is expected when **API token saved securely** is shown. Leave it blank to keep the stored token.                                                     |

### Paperless connection and permissions

| Symptom                                                    | What to check                                                                                                                                                                                                                   |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Paperless cannot connect to Bridge                         | Check the hostname, IMAP port, DNS, and firewall from the Paperless container. Use a reachable LAN or VPN address for `BRIDGE_HOST` and `BRIDGE_BIND_IP`; enable `PAPERLESS_EMAIL_ALLOW_INTERNAL_HOSTS`.                        |
| Paperless reports a certificate error                      | Check the [Bridge certificate mount](setup.md#4-let-paperless-trust-bridge) and `PAPERLESS_EMAIL_CERTIFICATE_LOCATION`, including separate mail workers. Restart Bridge after changing its hostname.                            |
| Paperless returns HTTP 500                                 | Check its logs. The certificate path must be a readable file, not a directory. Recreate Paperless after changing mounts or environment settings.                                                                                |
| Paperless rejects the token or reports missing permissions | Check the token, URL path prefix, and [required permissions](import-profiles.md#required-paperless-permissions), including **View UI settings**.                                                                                |
| A profile belongs to another Paperless user                | Restore the original user's token. A superuser token cannot take over a profile. Correct missing or mismatched ownership of linked objects in Paperless.                                                                        |
| A linked account or rule is missing                        | Check its owner and permissions. Deleted links are automatically cleared only for the profile's original user if that user is a superuser. Otherwise, ask the Paperless administrator to restore or resolve the linked objects. |
| An account was created but the rule failed                 | Correct the reported error and apply again; the saved account is reused. Check for duplicate account or rule names in Paperless.                                                                                                |
| Paperless rejects the maximum email age                    | Use a value from `0` to `32767`. Set `0` to include mail of any age.                                                                                                                                                            |

### No documents imported

Check these in order:

1. Confirm the email account and its enabled rule exist in Paperless for the intended user. **Save settings** alone does not create them; use **Apply to Paperless**.
2. Use Paperless's **email account processing** action, or wait for scheduled mail fetching. Processing the local consume directory does not fetch email.
3. Confirm the message is in the selected Proton source folder. Custom folders normally start with `Folders/`.
4. Check that the message contains a matching attachment. The default filter is `*.pdf`.
5. Check the maximum email age. Moving an old message into the folder does not change its age; use `0` to remove the age limit.
6. Check Paperless's processing tasks and logs, then sign in as the intended document owner. Document processing can fail after a successful mail connection.

Paperless also tracks messages it has already processed. If you are deliberately repeating a test with the same email, check its **Processed Mails** list; see [Paperless email import](https://docs.paperless-ngx.com/usage/#incoming-mail).
