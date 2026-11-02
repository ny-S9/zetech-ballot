-- Upgrades an older ballot.db (single-stage election) to the electoral-college version.
-- Run automatically by app.py when the departments table is missing.
CREATE TABLE departments (
    department_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    department_name  TEXT    NOT NULL UNIQUE,
    delegate_seats   INTEGER NOT NULL DEFAULT 1 CHECK (delegate_seats >= 1)
);
INSERT INTO departments (department_name, delegate_seats) VALUES
('ICT', 1), ('Hospitality', 1), ('Law', 1), ('Education', 1), ('Business', 1), ('Nursing', 1);

ALTER TABLE students ADD COLUMN department_id INTEGER REFERENCES departments(department_id);
ALTER TABLE elections ADD COLUMN stage TEXT NOT NULL DEFAULT 'delegates'
    CHECK (stage IN ('delegates', 'executives', 'declared'));

CREATE TABLE delegate_candidates (
    delegate_candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL UNIQUE,
    department_id   INTEGER NOT NULL,
    statement       TEXT,
    photo_path      TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected')),
    applied_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id)    REFERENCES students(student_id),
    FOREIGN KEY (department_id) REFERENCES departments(department_id)
);
CREATE TABLE delegate_votes (
    dvote_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id            INTEGER NOT NULL,
    delegate_candidate_id INTEGER NOT NULL,
    election_id           INTEGER NOT NULL,
    voted_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id)            REFERENCES students(student_id),
    FOREIGN KEY (delegate_candidate_id) REFERENCES delegate_candidates(delegate_candidate_id),
    FOREIGN KEY (election_id)           REFERENCES elections(election_id),
    UNIQUE (student_id, election_id)
);
CREATE TABLE delegates (
    delegate_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    election_id     INTEGER NOT NULL,
    student_id      INTEGER NOT NULL,
    department_id   INTEGER NOT NULL,
    votes           INTEGER NOT NULL DEFAULT 0,
    declared_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (election_id)   REFERENCES elections(election_id),
    FOREIGN KEY (student_id)    REFERENCES students(student_id),
    FOREIGN KEY (department_id) REFERENCES departments(department_id),
    UNIQUE (election_id, student_id)
);
CREATE TABLE leaders (
    leader_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    election_id   INTEGER NOT NULL,
    position_id   INTEGER NOT NULL,
    candidate_id  INTEGER NOT NULL,
    votes         INTEGER NOT NULL DEFAULT 0,
    declared_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (election_id)  REFERENCES elections(election_id),
    FOREIGN KEY (position_id)  REFERENCES positions(position_id),
    FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id),
    UNIQUE (election_id, position_id)
);
