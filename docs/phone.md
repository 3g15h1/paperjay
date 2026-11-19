# PaperJay on your phone

## Install it like an app

Open PaperJay in Safari (iPhone) or Chrome (Android) and choose **Add to Home Screen** (Safari: the share
button; Chrome: the menu). It opens full screen, like an app.

## Scan documents with the camera

**Add** (the round button in the middle of the bar; **Add documents** on a computer) **→ Scan with camera.** Point the phone at the page: PaperJay outlines the page while you aim,
and when you hold still for a moment (the ring around the button fills up) it takes the photo by itself. Put
the next page down and it takes that one too; tap **Done** when you're finished. **Auto** switches to
taking each photo with the button instead, and ⚡ turns on the light where the phone allows it.

Each page is cut out, straightened and cleaned up (shadows removed). Tap a page to drag its corners or choose
**Black & white** (like a scanner or fax: sharp and small), **Grayscale** or **Color** (for colored stamps
and logos). **Save** makes one PDF of all the pages, or, if you choose **Separate documents** above it, one document per page. A dark table or background under the page helps it find
the edges.

### One-time setup: the secure address

Phones only let web apps use the live camera over **https**. PaperJay has its own HTTPS for this, with a
certificate it makes itself; each phone trusts it once. Open **Settings → Camera scanner** on the phone
and follow the steps (iPhone: download a profile, install it, then turn it on under Settings → General →
About → Certificate Trust Settings; Android: install a CA certificate). Then open the https address it
shows and, if PaperJay is on your home screen, add it again from there.

The certificate can only vouch for home-network addresses (192.168.x.x, 10.x, .local, .lan, Tailscale's
100.x and .ts.net), so it can't be misused to fake real websites. If PaperJay is behind a reverse proxy
with HTTPS already, just use that address.

Without the secure address, **Scan with camera** opens the phone's own camera app instead: take the photo,
then check the outline PaperJay found.

## Share from other apps

- **Android:** once PaperJay is on the home screen, it appears in the share sheet. Share a PDF or photo from
  any app (mail, files, scanner apps) and it's added.
- **iPhone:** use a Shortcut, which works from the share sheet too:
  1. Settings → Your account → Advanced: access keys → **New access key**, name it "iPhone", copy it.
  2. Shortcuts → new shortcut → **Receive** *Images and PDFs* from *Share Sheet*.
  3. Add **Get Contents of URL**: `http://<paperjay>/api/documents/post_document/`, method **POST**,
     header `Authorization` = `Token <your token>`, request body **Form**, field `document` (type File) =
     *Shortcut Input*.
  4. Optionally add **Show Notification** "Sent to PaperJay".

  A shortcut you used with Paperless-ngx works unchanged once you swap the address and the token.

## Coming-up dates in your calendar

**Settings → Your account → Get my calendar link**, then on iPhone: Settings → Calendar → Accounts →
Add Account → Other → **Add Subscribed Calendar** and paste the link (Android: Google Calendar on the web →
Other calendars → From URL). Due and expiry dates show up with reminders at 09:00 on the days you chose
in **Settings → Reminders**. The link can only read the calendar.

## Away from home

Keep PaperJay on your home network and use a VPN (e.g. Tailscale) to reach it from outside.
