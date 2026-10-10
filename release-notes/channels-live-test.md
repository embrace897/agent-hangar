# Live test of the chat and e-mail channels

The automated tests cover Slack, Teams and the IMAP inbox with simulated services (signatures, retries, the people
lookup, the replies). This checklist is for a run against real accounts before relying on them in production. It
takes about 30 minutes and needs a public URL for the central (`PUBLIC_BASE_URL`).

## What you need

- A Slack workspace where you can install apps (a free test workspace is fine).
- A Microsoft 365 tenant with Teams, a team where you can create an outgoing webhook, and Microsoft sign-in configured
  in the hangar (SSO).
- A mailbox with IMAP over SSL and an app password (Gmail, Outlook or the company's server).
- A Digital employee in probation or active, and two people in its team: a requester (developer) and the manager.

## Slack

1. *Digital employees → Decision channels*: copy the manifest, create the app *From a manifest*, install it, and paste
   the bot token and the signing secret. Slack verifies the events URL when you save the manifest: it must answer.
2. In a channel, invite the app and write `@Agent Hangar <employee name>, <a request>`.
   - [ ] the reply arrives in the thread with the task number;
   - [ ] the task shows "via Slack" and the requester's e-mail.
3. Send a direct message to the app with the same text.
   - [ ] a task is created and the reply comes in the DM.
4. Use a request that needs an approval (an action outside the authority).
   - [ ] the manager gets the DM with buttons; *Approve* in Slack resumes the task.
5. When the task ends:
   - [ ] the result is posted in the original thread.
6. Write without the employee's name, and from a Slack account whose e-mail is not in the hangar.
   - [ ] the bot lists who you can ask; and says it did not find you.

## Teams

1. In the team: *Manage team → Apps → Create an outgoing webhook*, with the URL shown in *Decision channels →
   Microsoft Teams*. Paste the security token in the hangar.
2. Sign in to the portal once with the Microsoft account of the requester.
3. In the channel, mention the webhook: `@<webhook name> <employee name>: <a request>`.
   - [ ] the answer with the task number arrives in the channel within 5 seconds.
4. When the task ends:
   - [ ] the result reaches the requester through their notification channel (*Decisions → My channel*).
5. Mention it from an account that never signed in with Microsoft.
   - [ ] the answer asks the person to sign in once.

## E-mail inbox

1. Employee *Settings → E-mail inbox*: server, port 993, user, app password, folder; save and *Test connection*.
   - [ ] it reports the number of unread e-mails.
2. Send an e-mail from a company address.
   - [ ] within `INBOX_POLL_S` seconds, a task is created with the subject as its title;
   - [ ] with `SMTP_*` set, the result comes back by e-mail when the task ends.
3. Send the same e-mail again (forward with the same `Message-ID` is not enough; resend from the server's sent folder),
   and one from an outside address.
   - [ ] no second task; the outside sender is refused and counted.

## Afterwards

Remove the test app, the outgoing webhook and the app password, or keep them only in a test environment. Write down
what failed and open an issue with the step number.
