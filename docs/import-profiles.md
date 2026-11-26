# Import profiles

[Back to README](../readme.md) · [Setup](setup.md) · [Troubleshooting](maintenance.md#troubleshooting)

Each separate Proton account has one import profile with its own Paperless URL, API token, source folder, processed folder, filters, and linked email account and rule. Profiles can use different users on one Paperless server or different servers.

## Before you begin

Complete [setup](setup.md), install the Bridge certificate in Paperless, and give the intended Paperless user the [required permissions](#required-paperless-permissions). Create two different folders in Proton Mail, for example `Paperless` and `Processed`.

## Create a profile

1. Open **Paperless setup** and select the **Proton account** at the top.
2. Enter your Paperless URL, such as `https://paperless.example.internal`, and the API token of the user who should own the imported documents. Include the URL path prefix if your installation uses one.
3. Select the Proton email address and choose an **Email account name** and **Rule name**. Account names must be unique across the Paperless server; rule names must be unique for that Paperless user.
4. Choose the **Source folder** and **Move processed email to** folder from the dropdowns. Custom folders normally appear as `Folders/Paperless` and `Folders/Processed`. Click **Refresh folders** after creating or renaming them in Proton.
5. Set the attachment filter and maximum email age.
6. Click **Test connection** and check the displayed **Document owner**.
7. Click **Apply to Paperless** and confirm. This creates an email account and an enabled import rule owned by that user. The rule assigns newly imported documents to the same owner.

Repeat for each connected Proton account. Applying a profile again updates its linked email account and rule.

### Required Paperless permissions

Use a regular Paperless user for each person's profile and grant these permissions:

| Resource       | Permissions                                  |
| -------------- | -------------------------------------------- |
| UI settings    | View                                         |
| Email accounts | View, add, change                            |
| Mail rules     | View, add, change                            |
| Documents      | View, so the user can see imported documents |

The app needs **View UI settings** to identify the token's user. A valid token without that permission cannot complete setup.

### What the buttons do

| Button                 | Result                                                                                                                                      |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| **Save settings**      | Saves the selected profile in Paperless Proton                                                                                              |
| **Test connection**    | Saves the profile, identifies the token's user, checks linked object ownership, and asks Paperless to test its SSL/TLS connection to Bridge |
| **Apply to Paperless** | Saves and tests the profile, then creates or updates the email account and import rule in Paperless                                         |

Switching profiles prompts before discarding unsaved changes. After saving, profile switching remains disabled until the saved state has refreshed; if that refresh fails, the interface retries automatically.

### Filters and connection options

| Setting                                            | Behavior                                                                                                           |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| **Attachment filename filter**                     | Defaults to `*.pdf`, which selects PDF attachments                                                                 |
| **Maximum email age**                              | Accepts `0` to `32767` days; `0` includes mail of any age                                                          |
| **Paperless CA certificate**                       | Paste the public CA certificate here if Paperless uses a private HTTPS certificate                                 |
| **Allow unencrypted HTTP on a trusted LAN or VPN** | Explicitly allows an HTTP Paperless URL; this sends the API token and Bridge password without transport encryption |

Moving an old email into the source folder does not reset its age. Choose a suitable age limit before testing with older mail.

## Saved API tokens

Tokens are stored separately for each profile in the encrypted password store. After a successful save, the input clears and **API token saved securely** appears. Leave the field blank to keep the saved token, including after a restart. If saving fails, the entered token stays in the field so you can retry.

You can replace a token with another token for the **same Paperless user**. Once linked, a profile refuses a different user's token, including a superuser token.

Changing the Paperless URL requires a token again and clears the profile's links to the old server. Accounts and rules on the previous server remain there; the app does not remove them.

## Document ownership and permissions

The token's user owns the created email account and import rule. The rule assigns new documents to that user. Existing documents keep their current permissions.

The app checks ownership before updating linked accounts and rules. If an existing account or rule has no owner or the wrong owner, correct that ownership in Paperless before applying the profile.

Consider these access rules when managing multiple people:

- **Paperless superusers can see all documents.** Use regular users for personal profiles.
- **Workflows and sharing can grant further access.** Review Paperless workflows, document permissions, and shared objects such as tags that may expose metadata.
- **Bridge administrators can access connected accounts.** This is one central admin interface, not a separate login for each person.
- **Additional addresses may share a mailbox.** Profiles separate Proton accounts; choosing an address within an account does not create a separate privacy boundary.

See [Paperless permissions](https://docs.paperless-ngx.com/usage/#permissions) for its access model. An API token grants its user's permissions; it is not restricted to a single import folder.

## Verify your first import

1. Move an email with a PDF attachment into the selected source folder.
2. Wait for Paperless's scheduled mail fetch, or use its email account processing action. The default interval is ten minutes; see [Paperless's mail schedule](https://docs.paperless-ngx.com/configuration/#PAPERLESS_EMAIL_TASK_CRON).
3. Sign in to Paperless as the intended regular user and confirm the document appears with the correct owner.
4. For a shared installation, check that another regular user cannot see that document unless access was deliberately granted.

A successful connection test confirms connectivity, not document processing or the effects of your Paperless workflows. If nothing arrives, follow [No documents imported](maintenance.md#no-documents-imported).
