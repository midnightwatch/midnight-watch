# Midnight Watch

**A daily, AI-run Doomsday Clock.** Every day at 00:00 UTC, five AI experts and a chair read the world's news and reset a simulated Doomsday Clock, then explain why.

**Live site:** https://midnightwatch.github.io/midnight-watch/

> This is a simulation. The real Doomsday Clock is set once a year by the Bulletin of the Atomic Scientists. Midnight Watch is not affiliated with the Bulletin.

## What it does

- Collects the last 24 hours of world news from wire services and international outlets (RSS, no API keys).
- Five AI "experts" each read the news and vote: Nuclear, Climate, Bio, AI & tech, Geopolitics.
- A sixth AI, the chair, weighs the votes against yesterday's reading and sets the new time.
- The site shows the clock, the chair's verdict, each expert's view, the headlines behind it, a daily archive with a calendar, a 30-day chart, and the official clock's full history since 1947.
- The official Bulletin reading is checked daily and shown next to ours, so you can see the difference.

## The rules

The AI is not trusted to behave on its own. Hard rules in code:

- Maximum move of 5 seconds per day in either direction.
- Every move must cite a real headline. No citation, no move.
- The chair cannot move against the experts' combined score.
- A move of 3 seconds or more needs at least two experts voting the same way.
- "No change" is a normal, expected outcome.
- Starting point: the official reading at launch (85 seconds, January 2026).

## How it runs

| Part | Tool | Cost |
|---|---|---|
| Hosting | GitHub Pages | free |
| Daily job | GitHub Actions (cron, 00:00 UTC) | free |
| News | RSS feeds (AP, Reuters, BBC, Al Jazeera, DW, UN News, Bulletin, IAEA, Carbon Brief, NPR, Guardian, France 24) | free |
| AI | Groq (primary), Gemini (backup), both free tier | free |
| Visitor count | GoatCounter | free |

Files:

| File | Purpose |
|---|---|
| `index.html` | The whole website. Reads the JSON files in `data/`. |
| `brain.py` | The daily job: fetch news, ask the six AIs, apply the rules, write the data files. |
| `share_image.py` | Draws the picture shown when the link is shared in chat apps. |
| `.github/workflows/daily.yml` | Runs `brain.py` every day and commits the result. |
| `data/latest.json` | Today's reading. |
| `data/history.json` | One line per day: date and seconds. |
| `data/days/` | Full archive, one file per day. |
| `data/official.json` | The official Doomsday Clock history, updated automatically. |
| `data/share.png` | Today's share image. |

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
```

## Author

Sai Balaji Thadishetty

## License

MIT. See [LICENSE](LICENSE).
