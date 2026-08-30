# PilotBrief ✈️
**Aviation Weather & Automated Pre-Flight Briefing Discord Bot for Student Pilots**

PilotBrief connects to your Google Calendar, tracks upcoming flight lessons, and automatically sends comprehensive, progressive pre-flight briefings via Discord DM at configured intervals (**6h, 3h, 2h, 1h, and 15m** before departure).

---

## Key Features

- **Automated Milestone Alerts**: Delivers progressive DMs before scheduled flights:
  - **6 Hours Out**: Outlook & TAF trends.
  - **3 Hours Out**: Standard Preflight Briefing (TAF, early METAR, active SIGMETs).
  - **2 Hours Out**: Detailed Assessment with Runway Crosswind calculations.
  - **1 Hour Out**: Go / No-Go Decision Briefing with NOTAM highlights & fresh Radar image.
  - **15 Minutes Out**: Final Ramp Check (live METAR, altimeter setting, active runway winds).
- **Aviation Weather & Decoded Data**:
  - Live METARs & TAFs from NOAA Aviation Weather Center (AWC).
  - Color-coded Flight Categories: 🟢 **VFR** | 🔵 **MVFR** | 🔴 **IFR** | 🟣 **LIFR**.
  - Decoded Plain-English summaries (Wind vectors, ceiling, visibility, density altitude, carb icing risk).
- **Runway & Crosswind Component Analysis**:
  - Computes exact headwind, tailwind, and crosswind (plus gust component) for every runway at the departure/destination airport.
  - Automatically highlights the most favorable runway.
- **Student Pilot Personal Minimums**:
  - Configurable personal limits (`/settings minima`).
  - Flags safety warnings if surface winds, gusts, crosswinds, or cloud ceilings violate your minimums.
- **Visual Radar & Airspace Overview Map**:
  - Generates high-resolution PNG maps with route vectors, airport markers, real-time composite NEXRAD precipitation radar, and active SIGMET/AIRMET polygon boundary overlays.
- **Strict Whitelist Security**:
  - Built-in authorization guard restricted strictly to authorized Discord User ID (`454870771039469568`).

---

## Discord Slash Commands

| Command | Description |
|---|---|
| `/flight brief <departure> [destination]` | Instant on-demand weather briefing & radar map for any airport(s). |
| `/flight next` | Generate an instant briefing for your next upcoming scheduled flight lesson. |
| `/flight home <ICAO>` | Set your default home departure airport (e.g. `KPAO`, `KSFO`, `KSQL`). |
| `/flight schedule` | View upcoming synced flight lessons. |
| `/flight sync` | Manually trigger a calendar sync. |
| `/settings link-calendar <ical_url>` | Link your secret Google Calendar iCal feed URL (`.ics`). |
| `/settings minima` | Configure student solo/dual personal minimums (wind, crosswind, gusts, ceiling, vis). |
| `/settings intervals <csv>` | Customize alert milestone countdowns in minutes (e.g. `360,180,120,60,15`). |
| `/settings view` | View current user preferences, minimums, and sync status. |

---

## Deploying on Private Coolify

1. **Push to GitHub**:
   - Push this repository to your GitHub account (public or private).
2. **Add New Service in Coolify**:
   - In Coolify, click **Create New Resource** ➔ **GitHub Repository**.
   - Select your repository and choose **Docker Compose** or **Dockerfile**.
3. **Configure Environment Variables**:
   In your Coolify application environment variables, add:
   ```env
   DISCORD_BOT_TOKEN=your_bot_token_here
   ALLOWED_USER_IDS=454870771039469568
   HOME_ICAO=KPAO
   ```
4. **Persistent Storage Volume**:
   Coolify will automatically mount the `/app/data` volume specified in `docker-compose.yml` to preserve your SQLite database across updates.
5. **Deploy**:
   Click **Deploy**! Once started, your bot will come online and register all slash commands.

---

## How to Get Your Google Calendar iCal URL

1. Open [Google Calendar](https://calendar.google.com) in your browser.
2. Click the gear icon ⚙️ ➔ **Settings**.
3. Under **Settings for my calendars**, click on your flight/lesson calendar.
4. Scroll down to the **Integrate calendar** section.
5. Copy the **Secret address in iCal format** (`https://calendar.google.com/calendar/ical/.../basic.ics`).
6. In Discord, run:
   ```
   /settings link-calendar ical_url: <paste_your_secret_address>
   ```

---

## Running Locally

```bash
# Clone repository
git clone https://github.com/your-username/pilotbrief.git
cd pilotbrief

# Install dependencies
pip install -r requirements.txt

# Configure .env
cp .env.example .env
# Edit .env with your DISCORD_BOT_TOKEN

# Run unit tests
pytest

# Start bot
python main.py
```
