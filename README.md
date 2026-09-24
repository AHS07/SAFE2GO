# SAFE2GO

Operator assistant for CAT construction machines. Gives machine operators one application for daily tasks, real-time safety alerts, usage feedback, task time estimates, and training.

Safety checks, usage analytics, and time estimates run on local rules and a local machine learning model. They do not need an internet connection or a language model. An optional documentation assistant uses the DeepSeek API to explain manual content.

All machine data is synthetic.

## Features

- Task dashboard: shift details, task queue, live progress, and a planning time estimate per task
- Safety alerts: seatbelt, operator not seated, proximity, tilt, overload, visibility, and temperature with WARNING and CRITICAL levels
- Incident log: automatic incidents from sensor data, manual reports, and acknowledgement for critical events
- Usage analytics: detects excessive idling, repeated overloading, high-RPM travel, repeated seatbelt violations, and unusual idle ratios
- Time estimation: Random Forest model predicts task duration and explains the main factors behind each estimate
- Training hub: short modules and quizzes, recommended automatically based on incidents and usage patterns
- Offline mode: safety, tasks, training, manuals, and emergency guidance keep working when the cloud connection is lost
- Task assignment: admins assign shifts and tasks with validation for qualifications, machine compatibility, and scheduling conflicts
- Documentation assistant: searches machine manuals, with optional explanations from DeepSeek
- Shift summary: tasks done, working and idle time, incidents, coaching, and training to review
- Service suggestions: engine hours since the last service, flagged when a service is due soon or overdue
- Fleet data pipeline (optional): live telemetry is held on the machine while offline and streamed through Kafka to fleet analytics, paced so a reconnect never floods the cloud

## How it works

The system has two logical parts:

- Cloud: task assignment, validation, fleet data, baselines, and the DeepSeek connection
- Edge: runs on each machine and handles telemetry, safety, analytics, and the operator view

In the demo, both parts run in one backend process with separate database schemas. A toggle simulates losing the cloud connection. The two parts exchange data only through a sync service with an outbox on each side, so the edge keeps working while the link is cut.

Kafka is optional and only carries a copy of live telemetry to fleet analytics. Safety checks never wait for it.

## Tech stack

| Area | Technology |
|---|---|
| Backend | Python 3.11, FastAPI, Uvicorn, Pydantic v2 |
| Database | PostgreSQL 16, SQLAlchemy 2.x, Alembic |
| Machine learning | scikit-learn, pandas, NumPy, joblib |
| Real-time | FastAPI WebSockets |
| Telemetry streaming (optional) | Apache Kafka 3.9 in Docker, aiokafka |
| Documentation assistant | TF-IDF retrieval, DeepSeek API |
| Frontend | React 18, TypeScript, Vite, Tailwind CSS |
| Testing | pytest, Vitest |
| Local services | Docker Compose |

## Requirements

- Python 3.11 or later
- Node.js 20 or later
- Docker and Docker Compose
- DeepSeek API key (optional, only for assistant explanations)

## Setup

1. Clone the repository and create the environment file.

   ```bash
   git clone <repository-url>
   cd safe2go
   cp .env.example .env
   ```

   Fill in the values in `.env`. `JWT_SECRET` and `CREDENTIAL_SIGNING_KEY` are required, at least 32 characters each; the backend will not start without them. Generate each with:

   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"
   ```

   `DEEPSEEK_API_KEY` can be left empty.

2. Start the database.

   ```bash
   docker compose up -d db
   ```

   Without Docker, any PostgreSQL 16 server works: create an empty database and set `DATABASE_URL` in `.env` to it.

3. Set up the backend.

   ```bash
   cd backend
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # Linux/macOS:
   source .venv/bin/activate

   pip install -r requirements.txt
   alembic upgrade head
   ```

4. Generate data, train the model, and load demo records.

   ```bash
   python -m simulator.batch.generate --seed 42
   python -m ml.train_eta
   python -m scripts.seed_demo
   ```

5. Start the backend.

   ```bash
   uvicorn app.main:app --reload
   ```

6. Start the frontend in a second terminal.

   ```bash
   cd frontend
   npm install
   npm run dev
   ```

7. Open the address shown by Vite, usually `http://localhost:5173`.

## Running the demo

Before each demo, reset it with `python -m scripts.reset_db` (a few seconds). This removes the previous demo run and keeps the generated history. Demo shifts start 1 hour before the current time and run 8 hours. For a full reset, including generated data, run `python -m scripts.reset_db --full`, then steps 4 and 5 again.

| Account | Sign in | Opens |
|---|---|---|
| `op_beginner`, `op_intermediate`, `op_expert` | Password `demo123`, or shift PIN `1234` | Operator screens |
| `admin` | Password `admin123` | Admin screens at `/admin` |

- The demo bar at the top of each operator screen starts and stops the simulator, sets the speed, injects scenarios for your machine, and cuts or restores the cloud link.
- Start a task to see live progress. The simulator slows to real time while any incident is open.
- End shift runs the end-of-shift idle ratio check on the live data so far and sends the shift summary to the cloud.
- Scenarios only change raw sensor values. The safety and behavior engines decide what becomes an alert or a coaching note.
- Sensor fault makes the proximity sensor report a fault. The check shows as unavailable and an open proximity incident stays open, since a missing reading never counts as clear.
- Training modules open while the machine is parked, which means no task is running.
- Values marked Illustrative (camera view, nearby equipment) are sample visuals, not machine data.
- Summary shows the shift so far. The admin sees the same summary under Assignments once the machine has synced it.
- On the Manuals screen, Explain asks DeepSeek to explain the matching sections in plain language. Without an API key, or with the cloud link cut, the sections are shown with a short notice instead.

To check the whole storyline against a running backend, run the rehearsal. It resets the demo and checks each step, stopping at the first problem:

```bash
cd backend
python -m scripts.demo_rehearsal --runs 3 --base-url http://127.0.0.1:8000
```

### Offline sequence

1. Sign in as an operator and start a task.
2. Cut the cloud link from the demo bar. Safety alerts, tasks, and training keep working. The bar shows how many records wait to sync.
3. In a second browser tab, sign in as `admin` and assign a task to that operator's shift. It shows as Queued.
4. Restore the link. The task reaches the operator's queue within a few seconds and shows as Delivered.

While the link is cut, operators cannot sign in with a password. The sign-in screen switches to the shift PIN, which is checked on the machine.

## Fleet data pipeline (optional)

Each machine keeps a copy of its live sensor data in a local queue (the spool). A background sender forwards it to Kafka, and a cloud reader stores it for fleet analytics. Safety checks run on the machine and never wait for any of this.

To turn it on:

1. Start Kafka: `docker compose up -d kafka`
2. Set `KAFKA_ENABLED=true` in `.env`
3. Restart the backend

What to show:

1. Start the simulator. Open `/admin/pipeline`: records move from the machines to the archive, and the per-minute charts fill in.
2. Cut the cloud link. The spool grows and the demo bar shows how many records wait. Safety alerts keep working.
3. Restore the link. The backlog drains at a capped rate while live data keeps flowing, and the page shows the send rate and the time left.

How it stays correct:

- A record leaves the spool only after Kafka confirms it. A record sent twice is stored once.
- A full spool (250,000 ticks by default, about 23 hours for 3 machines) drops the oldest raw ticks and reports the gap. Incidents are never dropped.
- If Kafka is down, records wait in the spool and the sender retries.

To check it end to end against a running backend with Kafka on:

```bash
python -m scripts.pipeline_rehearsal --offline-seconds 60 --base-url http://127.0.0.1:8000
```

## Assigning work

The admin screens at `/admin` cover:

- Assignments: shifts, their tasks, create shift, create task, cancel, reassign, and the ETA breakdown. Rejected requests show the reason next to the form. A task planned to finish after the shift ends is saved with a warning.
- Operators and machines: qualifications, logins, machine status, and service suggestions. An operator without a login cannot see assigned work; use Create login to give them a username, password, and PIN.
- Sync: cloud changes that were undone because the machine had already started the task, and messages that kept failing for an hour and were set aside, with a Retry button.
- Data pipeline: the Kafka stream from the machines to fleet analytics (see below).

The same actions are available through the admin API under `/api/admin/`. With the backend in dev mode, the interactive API page at `http://localhost:8000/docs` lists every endpoint.

## Tests and evaluation

```bash
cd backend
pytest
python -m evaluation.detection_report
python -m ml.evaluate_eta
```

```bash
cd frontend
npm test
npm run lint
```

Results on the held-out 10 days (seed 42):

| Measure | Result |
|---|---|
| Anomaly detection | 10 of 10 detected, 3 false positives |
| Legit-wait shifts flagged for idling | 0 of 12 |
| Task time, Random Forest | MAE 10.1 min, MAPE 10.2% |
| Task time, historical average | MAE 28.8 min, MAPE 30.5% |

The detection report compares detected usage patterns with labeled test data. The ETA report shows prediction error on a held-out time period. Both results come from synthetic data and show that the pipeline works, not real-world accuracy.

## Icons

The UI ships a small icon font with only the icons it uses. After using a new icon name, rebuild it (TypeScript reports an unknown icon until you do):

```bash
cd frontend
pip install -r scripts/requirements.txt
python scripts/subset_icons.py
```

## Content

Training modules, quizzes, sample manuals, and emergency guidance are plain files in `backend/content/`. All of it is original sample content written for this project. After editing the training catalog, reload it with:

```bash
cd backend
python -m scripts.seed_training
```

## Project structure

```
safe2go/
  backend/     API, cloud and edge modules, simulator, ML, tests
  frontend/    Operator and admin web app
```
