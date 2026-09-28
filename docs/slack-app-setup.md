How to Create and Install Slack Apps via CLI (Non-Admin Guide)

As a developer without workspace admin privileges, you can still programmatically create Slack apps, configure them, and send an installation request to workspace admins entirely from your terminal using the official Slack CLI.

Prerequisites

The Slack CLI installed on your machine.

Python (or Node.js/Java depending on your preferred Bolt framework).

Step 1: Authenticate the CLI

Before creating an app, you need to authorize your terminal to communicate with your Slack workspace.

Run the login command:

slack login


The CLI will output a string that looks like /slack authticket [ALPHANUMERIC_STRING].

Copy this string, open your Slack workspace, paste it into any message box (e.g., a DM to yourself), and send it.

Your terminal will confirm that you are successfully authenticated.

Step 2: Scaffold the App

Next, create the boilerplate code for your bot. For a standard Python bot, we use the Bolt starter template.

Run the create command:

slack create my-awesome-bot


Select the bolt-python-starter-template (or your preferred language) when prompted.

Navigate into your new project directory:

cd my-awesome-bot


Note: You can edit the manifest.json file in this directory to configure your bot's name, scopes, and event subscriptions before proceeding.

Step 3: Trigger Creation & Request Approval

Now, you need to register the app with Slack's servers and request permission to install it.

Run the local development server:

slack run


The CLI will prompt you to select your connected workspace.

What happens under the hood:

The CLI reads your manifest.json.

It creates the app structure on Slack's backend.

Because you are a non-admin, it halts the installation and generates a Request to Install notification for your workspace administrators.

You will see output similar to this:

🔔 App Create
   Creating app manifest for "my-awesome-bot" in "Your-Workspace"

🔔 Administrator approval is required to install this app
🔔 Your request to install the app is pending
   You will receive a Slackbot message after an admin has reviewed your request


Step 4: Await Approval & Connect

Wait for your workspace admin to approve the request. You will receive an automated DM from Slackbot once it is approved.

Once approved, return to your terminal and re-run:

slack run


The CLI will now successfully complete the installation, securely fetch your App and Bot tokens (xapp- and xoxb-) in the background, and start your bot's listener via Socket Mode.

Your bot is now live and ready to develop!