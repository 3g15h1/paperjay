# Home Assistant: "coming up" notifications

`/api/upcoming` lists due and expiry dates found in your documents; its `alerts` are the ones exactly the
number of days away you chose in **Settings → Reminders** (default 14, 3, 1 and 0). Home Assistant can
turn them into a dashboard list and phone notifications. (Only want them in a calendar? See
[phone.md](phone.md); no Home Assistant needed.)

First make a token: **Settings → Your account → Advanced: access keys → New access key** (a read-only account's token is
enough), and put it in Home Assistant's `secrets.yaml` as `paperjay_token: "Token pj_…"`.

## 1. A sensor (configuration.yaml)

```yaml
rest:
  - resource: http://paperjay.local:8000/api/upcoming?days=60
    scan_interval: 1800
    headers:
      Authorization: !secret paperjay_token
    sensor:
      - name: "PaperJay coming up"
        unique_id: paperjay_coming_up
        icon: mdi:calendar-alert
        value_template: "{{ value_json['items'] | count }}"
        json_attributes:
          - items
          - alerts
```

Use `value_json['items']`, not `value_json.items` (in templates `.items` is the dictionary's own method).

## 2. A morning notification (automation)

```yaml
alias: "PaperJay: coming up"
triggers:
  - trigger: time
    at: "09:00:00"
conditions:
  - condition: template
    value_template: "{{ (state_attr('sensor.paperjay_coming_up', 'alerts') or []) | count > 0 }}"
actions:
  - action: notify.mobile_app_your_phone
    data:
      title: "Coming up"
      message: >-
        {% for a in state_attr('sensor.paperjay_coming_up', 'alerts') %}{{ a.text }}
        {% endfor %}
      data:
        url: "{{ state_attr('sensor.paperjay_coming_up', 'alerts')[0].url }}"        # iOS: tap opens it
        clickAction: "{{ state_attr('sensor.paperjay_coming_up', 'alerts')[0].url }}" # Android
```

Each alert's `text` is ready to show, e.g. "Example Mutual insurance expires in 14 days (Thu Oct 22)".

## 3. A dashboard card

```yaml
type: markdown
title: Coming up
content: >-
  {% for i in state_attr('sensor.paperjay_coming_up', 'items') or [] %}
  - **{{ 'Today' if i.days_left == 0 else i.days_left ~ ' d' }}** ·
  [{{ i.vendor }} {{ 'due' if i.label == 'due' else 'expires' }}]({{ i.url }})
  {{- ' · $%.2f' % i.amount if i.amount else '' }}
  {% else %}
  Nothing due in the next 60 days.
  {% endfor %}
```

## Item fields

| Field | Example |
|---|---|
| `id`, `title`, `url` | the document, and a link that opens it |
| `label` | `due` or `expires` |
| `date`, `days_left` | `2026-10-22`, `14` |
| `vendor`, `amount` | `Acme Energy`, `144.37` (bills only) |
| `text` | a sentence ready for a notification |

Bills tagged with one of the "taken care of" tags (default `paid`) are left out, and so are expiry dates
of documents that a newer one replaces (last year's insurance card).
