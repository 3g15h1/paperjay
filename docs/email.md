# Documents from email

PaperJay can check your mailboxes every night and add the PDFs and photos attached to emails: bills,
statements, receipts, policies. **Settings → Email → Add mailbox.**

## Adding a mailbox

1. Type the email address your bills arrive at. PaperJay works out the rest: Gmail, Google Workspace,
   iCloud, Yahoo, AOL, Fastmail, Zoho, GMX, WEB.DE and Yandex are built in, and other providers are looked up
   from where their mail goes and in Thunderbird's public list of providers (only the part after the @ is
   sent there).
2. Make an app password: a separate password just for PaperJay, so it never needs your real one. PaperJay
   shows the steps for your provider and a button that opens the right page; paste the password it gives you.
   (GMX and WEB.DE use your normal password once you allow access for other programs.)
3. Pick the senders. PaperJay signs in and lists everyone who sent you emails with a PDF or photo attached in
   the last four months, most first, with an example subject. Untick shops and newsletters; tick
   **Also take documents from new senders** if you'd rather collect everything at first.

**Start collecting** saves the mailbox and collects the documents from those emails straight away.
**Edit** changes everything afterwards (filters by sender or subject words, unread only, moving emails to
a folder), and **Set it up by hand** on the first step skips the help for unusual setups.

**Outlook.com, Hotmail and Microsoft 365** don't allow app passwords any more and PaperJay can't use
Microsoft's own sign-in yet: add an Outlook rule that forwards your bills to another mailbox and add that one.
**Proton Mail** works only through Proton Mail Bridge.

## How it works

- Every night at the time under Settings → Email (02:30 by default, the server's time; empty turns the
  nightly check off) each mailbox is checked. **Check now** checks one straight away. If the server was off
  at that time, the check runs when it's back.
- Only emails with a PDF or photo attached are taken, and each email only once. Other attachments
  (Word files, calendar invites) and small pictures inside the email (logos, signatures) are skipped.
- Each attachment becomes its own document and is read, filed and indexed like an upload (source
  "email"). A file that's already in PaperJay is recognised and not added twice.
- Emails that didn't pass the filters aren't remembered: if you change the filters later, the next check
  can still take them.
- Emails without attachments (a bill that's only in the email's text) aren't saved yet.

## Privacy

PaperJay connects to the mail server over SSL/TLS (or STARTTLS) and checks the server's certificate. The
app password is stored in PaperJay's database, which means in its backups too, and is never shown again
in the app. Only admins can see or change mailboxes. Removing a mailbox doesn't remove the documents
taken from it.
