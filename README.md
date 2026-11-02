# Midnight Watch

**A daily, AI-run Doomsday Clock.** Every day at 00:00 UTC, five AI experts and a chair read the world's news and reset a simulated Doomsday Clock, then explain why.

**Live site:** https://midnightwatch.github.io/midnight-watch/

> This is a simulation. The real Doomsday Clock is set once a year by the Bulletin of the Atomic Scientists. Midnight Watch is not affiliated with the Bulletin.

## What it does

- Collects the last 24 hours of world news from wire services and international outlets (RSS, no API keys).
- Five AI "experts" each read the news and vote: Nuclear, Climate, Bio, AI & tech, Geopolitics.
- A sixth AI, the chair, weighs the votes against yesterday's reading and sets the new time.
- The site shows the clock, the chair's verdict, each expert's view, the headlines behind it, a daily archive with a calendar, a 30-day chart, and the official clock's full history since 1947.
- On the 1st of each month the chair writes a monthly report: how the clock moved, what drove it, where the experts disagreed, what to watch next.
- The official Bulletin reading is checked daily and shown next to ours, so you can see the difference.

## The rules

The clock is anchored to the official Bulletin reading (85 seconds, January 2026). The board keeps a short list of open "pressures": dangers that are new since then, each with a weight in seconds. The clock is the official reading minus the total weight. The AI is not trusted to behave on its own. Hard rules in code:

- The clock moves at most 0.5 seconds per day, all pressures together. Readings are kept to a tenth of a second.
- A pressure can change by at most 0.3 seconds a day and can never weigh more than 10 seconds.
- A new pressure needs a cited headline and at least one expert voting "worse"; it starts small.
- Raising, lowering or closing a pressure needs a cited headline. No citation, no change.
- The chair cannot move against the experts' combined score.
- A pressure that has not been in the news for 7 days fades by itself, 10% a day, and closes when it is nearly gone.
- At most 8 pressures can be open. "No change" is a normal, expected outcome.

## How it runs

| Part | Tool | Cost |
|---|---|---|
| Hosting | GitHub Pages | free |
| Daily job | GitHub Actions (cron, runs 23:00 UTC, published 00:00 UTC) | free |
| News | RSS feeds (AP, Reuters, BBC, Al Jazeera, DW, UN News, Bulletin, IAEA, Carbon Brief, NPR, Guardian, France 24) | free |
| AI | Groq (primary), Gemini (backup), both free tier | free |
| Visitor count | GoatCounter | free |

Files:

| File | Purpose |
|---|---|
| `index.html` | The whole website. Reads the JSON files in `data/`. |
| `brain.py` | The daily job: fetch news, ask the six AIs, apply the rules, write the data files. |
| `share_image.py` | Draws the picture shown when the link is shared in chat apps. |
| `.github/workflows/daily.yml` | Runs `brain.py` at 23:00 UTC and commits the result, dated for the next day. The site shows it from 00:00 UTC. |
| `.github/workflows/publish.yml` | Waits until 00:00 UTC and renames `data/share-next.png` to `data/share.png`. |
| `data/latest.json` | Today's reading. |
| `data/history.json` | One line per day: date and seconds. |
| `data/days/` | Full archive, one file per day. |
| `data/official.json` | The official Doomsday Clock history, updated automatically. |
| `data/share.png` | Today's share image. |
| `month.html` | The monthly report page. |
| `embed.html` | The embeddable widget (iframe). |
| `widget.html` | Instructions page for embedding the widget. |
| `data/months/` | Monthly reports, written by the chair on the 1st of each month. |

## Run your own

1. Fork this repo.
2. Get a free API key from [Groq](https://console.groq.com) (and optionally [Google AI Studio](https://aistudio.google.com/apikey) as backup).
3. Repo → Settings → Secrets and variables → Actions → add `GROQ_API_KEY` (and `GEMINI_API_KEY`).
4. Settings → Pages → Deploy from branch `main`, folder `/ (root)`.
5. Actions → Midnight Watch daily reading → Run workflow.
6. Edit the site address in `index.html` (search for `midnightwatch.github.io`) and in `share_image.py`.

To test locally without an API key:

```
pip install -r requirements.txt
python brain.py --news-only    # show the filtered news and stop
python brain.py --dry-run      # fake the AI, write the files
python brain.py --month 2026-10   # write the monthly report for one month
```

## Author

Sai Balaji Thadishetty

## License

MIT. See [LICENSE](LICENSE).
