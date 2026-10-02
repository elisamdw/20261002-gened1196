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

## Install

Clone the repository and enter its folder:

```bash
git clone https://github.com/elisamdw/20261002-gened1196.git
cd 20261002-gened1196
chmod +x tarot
```

The script has no Python package dependencies. Install the open-source Google
Workspace CLI separately:

```bash
brew install googleworkspace-cli
```

The Google Workspace CLI requires a Google OAuth Desktop client.

If `gcloud` is installed locally, authenticate it once with:

```bash
./tarot google-auth
```

If `gcloud` is not installed locally, create the credential manually:

1. Open the OAuth consent screen for your project in Google Cloud Console.
2. Choose an external audience/testing mode and add your own Google account as
   a test user.
3. Open **Credentials**, create an OAuth client of type **Desktop app**, and
   download its JSON file.
4. Save the downloaded file as `~/.config/gws/client_secret.json`.
5. Run `./tarot google-auth`. It requests Drive scopes only and opens Google
   login in your browser.

You can run the tool as `./tarot`. To install a `tarot` command in an activated
virtual environment instead, run:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Configure the Replicate token, Anthropic API key, and Drive destination:

```bash
./tarot setup
```

Create an Anthropic API key in the [Claude Console](https://console.anthropic.com/settings/keys)
if you do not already have one. Each card uses one Claude request followed by
one Replicate request, so the number of API calls scales with the number of
words or quoted phrases.

The Drive folder ID is the long value after `/folders/` in the folder's URL.
Both API secrets are stored in macOS Keychain, not in the project files.

If your Anthropic key is organization-scoped, select a workspace before
generating. Find its `wrkspc_...` ID in Claude Console under **Settings →
Workspaces**, then run:

```bash
tarot set-workspace wrkspc_01...
```

This is the workspace ID, not the organization ID. Workspace-scoped API keys do
not require this setting.

## Use

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
