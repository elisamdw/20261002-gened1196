# Tarot CLI

For each requested card, ask Claude to invent a varied symbolic art direction,
generate the image with Replicate, save it locally, and upload it to a Google
Drive folder with the Google Workspace CLI (`gws`).

Generated files use this format:

```text
YYYYMMDD-HHMMSS-prompt-words.png
```

For example:

```text
20260902-140509-the-moon-silver-wolves.png
```

## Before you begin

Complete setup in a terminal on the same Mac where you will run `tarot`.
Authentication completed in Google Cloud Shell does not configure your local
computer.

You will need:

- a Replicate API token;
- an Anthropic API key;
- an Anthropic workspace ID beginning with `wrkspc_` if the key is scoped only
  to an organization rather than a workspace;
- a Google Cloud project **ID** (not merely its display name); and
- a Google Drive folder that the Google account used for OAuth can edit.

Each card makes one paid Claude request and one paid Replicate request. Finish
the Drive preflight below before generating the first card so an OAuth problem
does not occur after image generation.

## Install on macOS

Clone the repository and enter its folder:

```bash
git clone https://github.com/elisamdw/20261002-gened1196.git
cd 20261002-gened1196
chmod +x tarot
```

The script has no Python package dependencies. Install the open-source Google
Workspace CLI and Google Cloud CLI separately:

```bash
brew install googleworkspace-cli
brew install --cask gcloud-cli
```

Confirm that both commands are on `PATH`:

```bash
gws --version
gcloud --version
```

You can run the tool as `./tarot`. To install a `tarot` command in an activated
virtual environment instead, run:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Do not run `src/tarot_cli/cli.py` as a shell script. Use `./tarot` from the
repository or the installed `tarot` command.

## One-time Google Drive OAuth setup

### 1. Select the Google Cloud project

Log in locally and list the projects available to that account:

```bash
gcloud auth login
gcloud projects list --format="table(projectId,name)"
```

Copy the value under `PROJECT_ID`, not `NAME`, then run:

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud services enable drive.googleapis.com --project YOUR_PROJECT_ID
gcloud config get-value project
```

If `gcloud config set project` says the value is not a valid project ID, the
display name was used by mistake. Return to `gcloud projects list` and copy the
ID from the first column.

### 2. Configure the OAuth app

Open the [Google Auth Platform](https://console.cloud.google.com/auth/overview)
with the same project selected, then:

1. Configure **Branding** if Google asks for an app name and contact email.
2. Under **Audience**, choose **External** and leave the app in **Testing**
   mode for personal use.
3. Under **Test users**, add the exact Google email address that will authorize
   Drive access. This step is required while the app is in testing mode.
4. Under **Clients**, create an OAuth client whose application type is
   **Desktop app**. Do not choose **Web application**.
5. Download the client JSON.

Save the downloaded file where `gws` expects it:

```bash
mkdir -p "$HOME/.config/gws"
cp "$HOME/Downloads/YOUR_DOWNLOADED_CLIENT_FILE.json" \
  "$HOME/.config/gws/client_secret.json"
chmod 600 "$HOME/.config/gws/client_secret.json"
```

Check that Google created a Desktop credential. This must print
`['installed']`, not `['web']`:

```bash
python3 -c 'import json,pathlib; p=pathlib.Path.home()/".config/gws/client_secret.json"; print(list(json.loads(p.read_text())))'
```

Using a Web client produces `Error 400: redirect_uri_mismatch` because `gws`
uses a temporary localhost callback port. Do not manually register one of
those temporary port numbers; replace the client with a Desktop client.

As an alternative, `gws auth setup --project YOUR_PROJECT_ID` can walk through
project and client setup. If it asks whether to start login immediately, answer
`n`, then use the Drive-only login command in the next step. If it asks for an
OAuth client ID and secret, they must come from a **Desktop app** client.

### 3. Log in with Drive-only permissions

Run:

```bash
gws auth login -s drive
```

Keep the terminal command running while you open its URL and complete the
browser flow. Choose the same email added under **Test users**. Running plain
`gws auth login` can request unrelated Gmail, Calendar, Sheets, and other
scopes; this project needs only the Drive service.

Confirm authentication and make a harmless Drive API request:

```bash
gws auth status
gws drive files list --params '{"pageSize":1,"fields":"files(id,name)"}'
```

Do not generate a paid image until both commands succeed.

## Configure Tarot

Create an Anthropic API key in the
[Claude Console](https://console.anthropic.com/settings/keys) and a Replicate
API token, then run:

```bash
./tarot setup
```

The setup prompts for both API secrets, the Drive destination folder ID, and a
local output folder. The Drive folder ID is the long value after `/folders/`
in the folder's URL. Make sure the OAuth Google account can edit that folder.

Both API secrets are stored in macOS Keychain, not in the project files.

If the Anthropic key is organization-scoped, find the workspace ID in Claude
Console under **Settings → Workspaces**, then run:

```bash
./tarot set-workspace wrkspc_01...
```

This is the workspace ID, not the organization ID. Workspace-scoped API keys do
not require this setting.

Finally, check the local Tarot configuration:

```bash
./tarot doctor
```

`tarot doctor` checks installed commands, configuration, and locally stored
secrets. The two `gws` commands in the Drive preflight are still required to
verify live OAuth and API access.

## Use

The examples below use the installed `tarot` command. Use `./tarot` instead if
you are running directly from the cloned repository without installing it.

Every prompt word after `tarot` becomes a separate card. This command generates
and uploads six images:

```bash
tarot the moon silver wolves winding path
```

The files have names such as:

```text
20260902-140509-the.png
20260902-140509-moon.png
20260902-140509-silver.png
20260902-140509-wolves.png
20260902-140509-winding.png
20260902-140509-path.png
```

Quote multiple words when they should describe one card:

```bash
tarot "the moon" "silver wolves" "winding path"
```

That command generates three cards.

Follow a term with a number from 1 to 5 to generate that many independently
planned copies. For example, this creates three biscuit cards, two cards titled
"the moon", and one sun card:

```bash
tarot biscuit 3 "the moon" 2 sun
```

Each copy gets a fresh set of ten Claude candidates, a fresh random selection
of three visual elements, its own Replicate generation, and its own Drive
upload. When no number follows a term, the copy count defaults to one.

Before each Replicate generation, Claude Sonnet 5.5 creates exactly ten
candidate visual elements drawn from plants, animals, objects, celestial
motifs, landscapes, and materials. The local Python script then randomly picks
three distinct candidates without replacement and sends only those three to
Replicate. A fresh variation key also changes the ten-element pool between runs.

Use a repeatable seed:

```bash
tarot the moon silver wolves --seed 42
```

Generate without uploading:

```bash
tarot the moon silver wolves --local-only
```

Check setup:

```bash
tarot doctor
```

## Troubleshooting

### `No Drive folder is configured`

Run `tarot setup` and paste the folder ID from the Google Drive folder URL.

### Anthropic says the key is not scoped to a workspace

The key is organization-scoped. Save a workspace ID—not the organization ID:

```bash
tarot set-workspace wrkspc_YOUR_WORKSPACE_ID
```

### `gws: command not found`

Install it with `brew install googleworkspace-cli`, then open a new terminal.

### Drive says `No credentials found`

Run `gws auth login -s drive`, finish the browser flow, and verify with
`gws auth status`.

### `Error 400: redirect_uri_mismatch`

The OAuth client is probably a Web application. Create a new **Desktop app**
client, replace `~/.config/gws/client_secret.json`, and retry
`gws auth login -s drive`.

### `Error 403: access_denied` or “app has not completed verification”

For a personal app in testing mode, Google verification is unnecessary. Open
Google Auth Platform → **Audience** → **Test users**, add the exact Google email
used in the browser, save, wait briefly, and retry the Drive-only login.

### The login URL asks for Gmail, Calendar, and many unrelated permissions

Press `Ctrl+C` to cancel it and run:

```bash
gws auth login -s drive
```

### Drive reports `API not enabled` or `accessNotConfigured`

Enable the Drive API in the project that owns the OAuth client:

```bash
gcloud services enable drive.googleapis.com --project YOUR_PROJECT_ID
```

Wait several seconds, rerun the harmless `gws drive files list` preflight, and
then retry Tarot.

### Claude returns symbolic direction that is not valid JSON

Update to the latest version and retry. The current parser allows a larger
response and handles JSON wrapped in a Markdown code block:

```bash
git pull
```

### Image generation succeeded but Drive upload failed

The PNG remains in the configured local output folder; it does not need to be
generated again. After fixing Drive, upload the existing file directly:

```bash
gws drive +upload "/path/to/generated-card.png" \
  --parent "YOUR_DRIVE_FOLDER_ID" \
  --name "generated-card.png"
```

### Cloud Shell setup worked, but the local command is unauthenticated

Cloud Shell and the Mac have separate files, Keychains, and OAuth credentials.
Repeat the Google login and `gws` setup locally on the Mac where `tarot` runs.

## Authentication references

- [Google Workspace CLI authentication and troubleshooting](https://github.com/googleworkspace/cli#authentication)
- [Google OAuth for desktop applications](https://developers.google.com/identity/protocols/oauth2/native-app)
- [Install Google Cloud CLI with Homebrew](https://docs.cloud.google.com/sdk/docs/downloads-homebrew)
