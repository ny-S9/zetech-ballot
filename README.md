# Zetech Electronic Ballot System

Flask + SQLite voting portal built on your 7-table schema (`schema.sql`).

## Run it
```
pip install -r requirements.txt
python app.py
```
Open http://127.0.0.1:5000. The database (`ballot.db`) is created automatically on first run.

## How the election works (electoral college)
1. **Departments** (ICT, Hospitality, Law, Education, Business; the Dean's office can add more and set seats) are the electoral colleges.
2. **Stage 1 - delegates:** students apply as a delegate of their own department. The Dean's office approves them, opens voting, and every student votes once for a delegate candidate of their OWN department (students only see and can vote for the candidates of their own department). After voting stops, the Dean's office declares the delegates: only the top vote-getters of each department win, **one delegate per department by default** (the number is set per department under Control room > Departments).
3. **Stage 2 - executives:** only the declared delegates vote, once per executive position. After voting stops, the Dean's office declares the leaders.
4. **After declaration** every student can see the leaders and the delegates on the *Our leaders* page.
5. Ties at the last seat are flagged and block declaration until the admin ticks "break ties by earliest application".

Live results stay visible to students as before. Set `SHOW_LIVE_RESULTS=0` to hide live counts from students.

**Your data is never lost by an update.** The database lives OUTSIDE the project folder, in a folder called `ZetechBallotData` in your user folder (for example `C:\\Users\\user\\ZetechBallotData\\ballot.db`). Replacing the project with a newer version does not touch it, so students you add in the portal, applications, votes and uploaded photos all stay. If an older version kept `ballot.db` inside the project folder, it is copied to the data folder automatically the first time you start the new version. Back up by copying the `ZetechBallotData` folder.

**If too many delegates were declared** (for example a department had 2 seats and only 2 candidates, so both were declared): open Control room > Election, expand **Declared the wrong delegates? Re-declare the winners**, leave 1 delegate per department and press **Re-declare winners**. It keeps only the top vote-getters of each department and does not change any vote, student or application. It is possible until the first executive vote is cast.

**Live results** show the declared delegate(s) once the delegates are declared: the Dean's office sees every department, a student sees only their own department's delegate.

**Delegate or executive, not both:** a student who applied as a delegate cannot apply for an executive position, even if the delegate application was rejected.

**Known limit:** applications are not tied to one election, so for a second election start from a fresh `ballot.db`.

## Voting screen, manifestos and Contact us
- **Read the whole manifesto:** every candidate card that is cut off has a **Read full manifesto** button. It opens the full text in a pop-up that scrolls. The same button is on the delegate ballot and on *Our leaders*.
- **One button for all votes:** a delegate chooses a candidate for each position, then presses **Review & cast my votes** once. A pop-up lists the choices (and any position left empty) before anything is saved. A bar at the bottom shows how many positions are chosen. A vote still cannot be changed after it is cast, and positions left empty can be voted later.
- **Contact us (students):** a form for feedback, suggestions and concerns. **Send anonymously** is ticked by default: an anonymous message saves only the text and its type. No name, admission number or student id is stored, the time is rounded to the hour, and nothing is written to the audit log. Unticking it lets the student type a name and/or admission number (both optional, stored as typed and not verified). At most 5 messages per hour per person.
- **Messages (Dean's office):** Control room > Feedback shows every message with search, New / Read / Resolved filters, Mark read, Resolve, Reopen, Delete, and a CSV download. A red badge counts new messages. They are stored in the `feedback` table, which is created automatically in an existing database without touching any data.
- **Smaller touches:** messages can be dismissed and fade away, character counters on the text boxes, show/hide password, search and filter boxes on the admin tables, **Live updates** in the Control room (on by default; it refreshes the numbers and tables every 15 seconds without reloading or blinking the page, and never closes a manifesto you are reading) with a **Refresh now** button, Live results that update only the numbers and bars in place (plus a **Refresh now** button), and a countdown on "Send a new code".

## Email verification (students)
After a correct password, students must enter a 6-digit code emailed to their school email (valid 5 minutes, 5 tries, resend every 30 s). Only a hash of the code is stored.

**Demo mode (default):** with no email server configured, the code is printed in the terminal and shown on the page so you can test. Do NOT use demo mode for the real election.

**Real emails (Gmail example):** turn on 2-step verification on a Gmail account, create an App Password, then set these before starting the app.
Windows (PowerShell):
```
$env:SMTP_HOST="smtp.gmail.com"; $env:SMTP_PORT="587"
$env:SMTP_USER="youraddress@gmail.com"; $env:SMTP_PASS="your-16-letter-app-password"
python app.py
```
Mac/Linux: use `export SMTP_HOST=smtp.gmail.com` (and the same for the others).

## Logins
| Who | How to log in |
|---|---|
| Dean's office (admin) | Open the **secret address** `http://127.0.0.1:5000/dean-access` (no link to it exists on the student pages). Registration number `456`, password `456`. Change the password under Control room > Settings. |
| Students | On the normal login page: admission number, e.g. `DIT-03-0085/2026`. First time: **Activate your account** (needs the school email from the `students` table), then enter the code sent by email. |

**Change the secret address** (recommended before sharing the site): set `ADMIN_LOGIN_PATH` before starting the app.
Windows: `set ADMIN_LOGIN_PATH=my-private-door` then `python app.py`, and the admin login is `http://127.0.0.1:5000/my-private-door`. Only people who know the address and the password can get in.

## Sample students (64)
Six departments: ICT, Business, Education, Hospitality, Law, Nursing. Admission numbers look like `COURSE-03-NNNN/YEAR`, with degree and diploma codes, e.g. Business `BAF BCOM BBAM BIRD BBIT DBM DAF`, Education `BEDA BEDS BECE DECE DEDP`, Hospitality `BHM BTTM DHM DFB DTT`, Law `LLB DLAW`, Nursing `BSN DKRCHN DCHN`, ICT `DIT BSCS BSIT`. The 4 `DSE-03-04xx/2025` students (Immanuel Ndunda and three placeholders) are in ICT. The emails are made up, so use demo mode for codes, or add a student with your own email under Control room > Voters.

## What each role can do
- **Student / voter**: activate account, log in, read manifestos, vote once per position, watch live results.
- **Candidate** (any student): *Run for office* page saves position, manifesto, photo, results file and fee confirmation into the `candidates` table (files go in `uploads/`). Can edit while pending, sees status, follows own count on Live results.
- **Admin**: start / pause / stop the election, approve or reject candidates (fee must be verified first), add students, reset a student's password, view the audit log, download a CSV report, create a new election.

## Where things are
- `app.py` routes and logic · `schema.sql` database · `templates/` pages · `public/static/` CSS, logo, `img/v1.jpg`, `img/v2.jpg`
- Flow: admin opens the election only after at least one candidate is approved; applications close while voting is open.

## Security built in
- Email one-time code after the password for every student login
- Passwords hashed (scrypt); sessions expire after 15 minutes idle; CSRF token on every form
- One vote per position enforced by the database `UNIQUE (student_id, position_id, election_id)` and checked in code
- 5 wrong logins lock that account for 5 minutes
- Uploads: allowed types only, random file names; results documents visible only to the owner and admin
- Every login, vote, application and admin action goes into `audit_log` (votes are logged by position, not by candidate; anonymous Contact us messages are not logged at all)

## Deploy on PythonAnywhere
Upload the folder, create a Flask web app pointing at `app.py` (`app` is the Flask object), set env `SECRET_KEY`, and make sure `ballot.db` and `uploads/` are writable.

## Good to know
- Times are stored in UTC and shown in East Africa Time.
- Because `votes` links a student to a candidate (as in your schema), anyone with database access could see who voted for whom. The web pages never show that.

## Share it temporarily for testing
Run `python app.py`, then in a second terminal run a tunnel, e.g. Cloudflare:
`cloudflared tunnel --url http://localhost:5000` and share the `https://...trycloudflare.com` link it prints.
Change the admin password first, and stop the tunnel (Ctrl+C) when testing is done. Debug mode is off by default; only use `FLASK_DEBUG=1` locally.

## Permanent database for Vercel: MySQL (TiDB Cloud, free)
Vercel cannot keep files, so on Vercel the app must use an online MySQL database. Set `DATABASE_URL` and the app creates its tables on the first run, keeps everything there (students added in the portal, applications, votes, photos) and never resets it when you redeploy.
1. Create a free **TiDB Cloud Starter** cluster (tidbcloud.com), click **Connect**, create a password, and copy the details (host, port 4000, user, password, database name).
2. Build the address: `mysql://USER:PASSWORD@HOST:4000/DATABASE` (if the password has symbols like `@` or `/`, replace them with `%40` and `%2F`).
3. In Vercel > Settings > Environment Variables add `DATABASE_URL` with that address (Production), and optionally `SECRET_KEY` (any long text) and `ADMIN_LOGIN_PATH`. Redeploy.
4. Local MySQL (XAMPP) also works: create an empty database, then `DATABASE_URL=mysql://root:@127.0.0.1:3306/zetech_ballot`.
Photos and results files are stored inside the database too. Not tested against a live MySQL server by the author: do a full practice election first.

## Deploy on Vercel
Vercel finds the Flask `app` in `app.py` by itself. Put this whole folder in your GitHub repo and Vercel redeploys it. Use the MySQL section above, otherwise data on Vercel is temporary. Uploads are limited to about 4 MB per submission there.

## Sessions
Students are signed out after 30 minutes without activity, the Dean's office after 4 hours, so the Control room can stay open and be refreshed to see new applications. If the Dean's session does end, the browser is sent back to the Dean's login, not the student login.
