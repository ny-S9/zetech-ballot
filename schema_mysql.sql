-- ZETECH ELECTRONIC BALLOT SYSTEM  -  MySQL version of the schema
-- Used automatically by app.py when DATABASE_URL points to a MySQL database (e.g. TiDB Cloud, XAMPP).
-- Tables are only created if they do not exist, and the sample data is only added on the very first run.

CREATE TABLE IF NOT EXISTS departments (
    department_id    INT AUTO_INCREMENT PRIMARY KEY,
    department_name  VARCHAR(100) NOT NULL UNIQUE,
    delegate_seats   INT NOT NULL DEFAULT 1,
    CHECK (delegate_seats >= 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS students (
    student_id        INT AUTO_INCREMENT PRIMARY KEY,
    admission_number  VARCHAR(40)  NOT NULL UNIQUE,
    full_name         VARCHAR(150) NOT NULL,
    email             VARCHAR(190) NOT NULL UNIQUE,
    phone_number      VARCHAR(30)  NOT NULL,
    password          VARCHAR(255) DEFAULT NULL,
    department_id     INT NULL,
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (department_id) REFERENCES departments(department_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS admins (
    admin_id          INT AUTO_INCREMENT PRIMARY KEY,
    full_name         VARCHAR(150) NOT NULL,
    phone_number      VARCHAR(30)  NOT NULL,
    department        VARCHAR(100) NOT NULL,
    registration_no   VARCHAR(60)  NOT NULL UNIQUE,
    password          VARCHAR(255) NOT NULL,
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS positions (
    position_id   INT AUTO_INCREMENT PRIMARY KEY,
    position_name VARCHAR(100) NOT NULL UNIQUE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS elections (
    election_id   INT AUTO_INCREMENT PRIMARY KEY,
    title         VARCHAR(255) NOT NULL,
    start_time    DATETIME NULL,
    end_time      DATETIME NULL,
    status        VARCHAR(10) NOT NULL DEFAULT 'closed',
    stage         VARCHAR(12) NOT NULL DEFAULT 'delegates',
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    CHECK (status IN ('open', 'closed', 'paused')),
    CHECK (stage IN ('delegates', 'executives', 'declared'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS candidates (
    candidate_id      INT AUTO_INCREMENT PRIMARY KEY,
    student_id        INT NOT NULL,
    position_id       INT NOT NULL,
    manifesto         TEXT,
    photo_path        VARCHAR(255),
    results_path      VARCHAR(255),
    fee_compliance    TINYINT NOT NULL DEFAULT 0,
    status            VARCHAR(10) NOT NULL DEFAULT 'pending',
    applied_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id)  REFERENCES students(student_id),
    FOREIGN KEY (position_id) REFERENCES positions(position_id),
    UNIQUE (student_id, position_id),
    CHECK (fee_compliance IN (0, 1)),
    CHECK (status IN ('pending', 'approved', 'rejected'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS votes (
    vote_id       INT AUTO_INCREMENT PRIMARY KEY,
    student_id    INT NOT NULL,
    candidate_id  INT NOT NULL,
    position_id   INT NOT NULL,
    election_id   INT NOT NULL,
    voted_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id)   REFERENCES students(student_id),
    FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id),
    FOREIGN KEY (position_id)  REFERENCES positions(position_id),
    FOREIGN KEY (election_id)  REFERENCES elections(election_id),
    UNIQUE (student_id, position_id, election_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS audit_log (
    log_id      INT AUTO_INCREMENT PRIMARY KEY,
    actor_id    INT NULL,
    actor_role  VARCHAR(20) NULL,
    action      VARCHAR(255) NOT NULL,
    details     TEXT,
    logged_at   DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS login_codes (
    student_id  INT PRIMARY KEY,
    code_hash   VARCHAR(100) NOT NULL,
    expires_at  DOUBLE NOT NULL,
    attempts    INT NOT NULL DEFAULT 0,
    sent_at     DOUBLE NOT NULL,
    FOREIGN KEY (student_id) REFERENCES students(student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS delegate_candidates (
    delegate_candidate_id INT AUTO_INCREMENT PRIMARY KEY,
    student_id      INT NOT NULL UNIQUE,
    department_id   INT NOT NULL,
    statement       TEXT,
    photo_path      VARCHAR(255),
    status          VARCHAR(10) NOT NULL DEFAULT 'pending',
    applied_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id)    REFERENCES students(student_id),
    FOREIGN KEY (department_id) REFERENCES departments(department_id),
    CHECK (status IN ('pending', 'approved', 'rejected'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS delegate_votes (
    dvote_id              INT AUTO_INCREMENT PRIMARY KEY,
    student_id            INT NOT NULL,
    delegate_candidate_id INT NOT NULL,
    election_id           INT NOT NULL,
    voted_at              DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id)            REFERENCES students(student_id),
    FOREIGN KEY (delegate_candidate_id) REFERENCES delegate_candidates(delegate_candidate_id),
    FOREIGN KEY (election_id)           REFERENCES elections(election_id),
    UNIQUE (student_id, election_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS delegates (
    delegate_id     INT AUTO_INCREMENT PRIMARY KEY,
    election_id     INT NOT NULL,
    student_id      INT NOT NULL,
    department_id   INT NOT NULL,
    votes           INT NOT NULL DEFAULT 0,
    declared_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (election_id)   REFERENCES elections(election_id),
    FOREIGN KEY (student_id)    REFERENCES students(student_id),
    FOREIGN KEY (department_id) REFERENCES departments(department_id),
    UNIQUE (election_id, student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS leaders (
    leader_id     INT AUTO_INCREMENT PRIMARY KEY,
    election_id   INT NOT NULL,
    position_id   INT NOT NULL,
    candidate_id  INT NOT NULL,
    votes         INT NOT NULL DEFAULT 0,
    declared_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (election_id)  REFERENCES elections(election_id),
    FOREIGN KEY (position_id)  REFERENCES positions(position_id),
    FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id),
    UNIQUE (election_id, position_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id       INT AUTO_INCREMENT PRIMARY KEY,
    category          VARCHAR(20) NOT NULL DEFAULT 'feedback',
    message           TEXT NOT NULL,
    is_anonymous      TINYINT NOT NULL DEFAULT 1,
    sender_name       VARCHAR(150) NULL,
    sender_admission  VARCHAR(40) NULL,
    status            VARCHAR(10) NOT NULL DEFAULT 'new',
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    CHECK (category IN ('feedback', 'suggestion', 'concern', 'other')),
    CHECK (status IN ('new', 'read', 'resolved'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- sample data (only added on the very first run)
INSERT INTO departments (department_name, delegate_seats) VALUES
('ICT', 1),
('Hospitality', 1),
('Law', 1),
('Education', 1),
('Business', 1),
('Nursing', 1);

INSERT INTO students (admission_number, full_name, email, phone_number, department_id) VALUES
('DIT-03-0084/2026', 'Kariuki Susan', 'kariukisusan@students.zetech.ac.ke', '0748672922', 1),
('DIT-03-0085/2026', 'John Mwangi', 'johnmwangi@students.zetech.ac.ke', '0712345678', 1),
('DIT-03-0086/2026', 'Amina Yusuf', 'aminayusuf@students.zetech.ac.ke', '0723456789', 1),
('DIT-03-0087/2026', 'Brian Otieno', 'brianotieno@students.zetech.ac.ke', '0734567890', 1),
('DIT-03-0088/2026', 'Cynthia Wanjiku', 'cynthiawanjiku@students.zetech.ac.ke', '0745678901', 1),
('DIT-03-0089/2026', 'David Kiprop', 'davidkiprop@students.zetech.ac.ke', '0756789012', 1),
('DIT-03-0090/2026', 'Faith Njeri', 'faithnjeri@students.zetech.ac.ke', '0767890123', 1),
('DIT-03-0091/2026', 'George Omondi', 'georgeomondi@students.zetech.ac.ke', '0778901234', 1),
('DIT-03-0092/2026', 'Hellen Chebet', 'hellenchebet@students.zetech.ac.ke', '0789012345', 1),
('DIT-03-0093/2026', 'Ian Kamau', 'iankamau@students.zetech.ac.ke', '0790123456', 1),
('BSCS-03-0112/2025', 'Kevin Mutua', 'kevinmutua@students.zetech.ac.ke', '0711007919', 1),
('BSIT-03-0045/2024', 'Lucy Achieng', 'lucyachieng@students.zetech.ac.ke', '0712015838', 1),
('BAF-03-0021/2025', 'Grace Wambui', 'gracewambui@students.zetech.ac.ke', '0713023757', 5),
('BAF-03-0022/2025', 'Peter Njoroge', 'peternjoroge@students.zetech.ac.ke', '0714031676', 5),
('BCOM-03-0150/2024', 'Mercy Atieno', 'mercyatieno@students.zetech.ac.ke', '0715039595', 5),
('BCOM-03-0151/2024', 'Samuel Kimani', 'samuelkimani@students.zetech.ac.ke', '0716047514', 5),
('BBAM-03-0077/2026', 'Naomi Chepkoech', 'naomichepkoech@students.zetech.ac.ke', '0717055433', 5),
('BBAM-03-0078/2026', 'Dennis Mutiso', 'dennismutiso@students.zetech.ac.ke', '0718063352', 5),
('BIRD-03-0033/2025', 'Esther Nyambura', 'esthernyambura@students.zetech.ac.ke', '0719071271', 5),
('BIRD-03-0034/2025', 'Victor Oduor', 'victoroduor@students.zetech.ac.ke', '0720079190', 5),
('BBIT-03-0090/2026', 'Joy Wangari', 'joywangari@students.zetech.ac.ke', '0721087109', 5),
('BBIT-03-0091/2026', 'Collins Ndungu', 'collinsndungu@students.zetech.ac.ke', '0722095028', 5),
('DBM-03-0201/2026', 'Rose Akinyi', 'roseakinyi@students.zetech.ac.ke', '0723102947', 5),
('DAF-03-0105/2025', 'Martin Karanja', 'martinkaranja@students.zetech.ac.ke', '0724110866', 5),
('BEDA-03-0060/2024', 'Sarah Wairimu', 'sarahwairimu@students.zetech.ac.ke', '0725118785', 4),
('BEDA-03-0061/2024', 'Joseph Kiplagat', 'josephkiplagat@students.zetech.ac.ke', '0726126704', 4),
('BEDS-03-0044/2025', 'Pauline Moraa', 'paulinemoraa@students.zetech.ac.ke', '0727134623', 4),
('BEDS-03-0045/2025', 'Allan Mwende', 'allanmwende@students.zetech.ac.ke', '0728142542', 4),
('BECE-03-0018/2026', 'Beatrice Jepkemoi', 'beatricejepkemoi@students.zetech.ac.ke', '0729150461', 4),
('BECE-03-0019/2026', 'Elijah Waweru', 'elijahwaweru@students.zetech.ac.ke', '0730158380', 4),
('DECE-03-0072/2026', 'Mary Nduku', 'marynduku@students.zetech.ac.ke', '0731166299', 4),
('DECE-03-0073/2026', 'Felix Barasa', 'felixbarasa@students.zetech.ac.ke', '0732174218', 4),
('DEDP-03-0031/2025', 'Irene Kerubo', 'irenekerubo@students.zetech.ac.ke', '0733182137', 4),
('BHM-03-0052/2025', 'Tabitha Wanjiru', 'tabithawanjiru@students.zetech.ac.ke', '0734190056', 2),
('BHM-03-0053/2025', 'Moses Ouma', 'mosesouma@students.zetech.ac.ke', '0735197975', 2),
('BTTM-03-0027/2024', 'Diana Chelangat', 'dianachelangat@students.zetech.ac.ke', '0736205894', 2),
('BTTM-03-0028/2024', 'Eric Kariuki', 'erickariuki@students.zetech.ac.ke', '0737213813', 2),
('DHM-03-0141/2026', 'Lilian Naliaka', 'liliannaliaka@students.zetech.ac.ke', '0738221732', 2),
('DHM-03-0142/2026', 'Hassan Abdi', 'hassanabdi@students.zetech.ac.ke', '0739229651', 2),
('DFB-03-0063/2026', 'Judy Mwikali', 'judymwikali@students.zetech.ac.ke', '0740237570', 2),
('DFB-03-0064/2026', 'Patrick Onyango', 'patrickonyango@students.zetech.ac.ke', '0741245489', 2),
('DTT-03-0036/2025', 'Winnie Cherop', 'winniecherop@students.zetech.ac.ke', '0742253408', 2),
('LLB-03-0014/2024', 'Ann Muthoni', 'annmuthoni@students.zetech.ac.ke', '0743261327', 3),
('LLB-03-0015/2024', 'Brian Kibet', 'briankibet@students.zetech.ac.ke', '0744269246', 3),
('LLB-03-0016/2025', 'Stella Awuor', 'stellaawuor@students.zetech.ac.ke', '0745277165', 3),
('LLB-03-0017/2025', 'Kennedy Mugo', 'kennedymugo@students.zetech.ac.ke', '0746285084', 3),
('LLB-03-0018/2026', 'Faith Jeptoo', 'faithjeptoo@students.zetech.ac.ke', '0747293003', 3),
('LLB-03-0019/2026', 'Dan Odhiambo', 'danodhiambo@students.zetech.ac.ke', '0748300922', 3),
('DLAW-03-0038/2026', 'Caroline Njuguna', 'carolinenjuguna@students.zetech.ac.ke', '0749308841', 3),
('DLAW-03-0039/2026', 'Ibrahim Mohamed', 'ibrahimmohamed@students.zetech.ac.ke', '0750316760', 3),
('DLAW-03-0040/2025', 'Ruth Chebet', 'ruthchebet@students.zetech.ac.ke', '0751324679', 3),
('BSN-03-0081/2024', 'Janet Wanjala', 'janetwanjala@students.zetech.ac.ke', '0752332598', 6),
('BSN-03-0082/2024', 'Isaac Kimutai', 'isaackimutai@students.zetech.ac.ke', '0753340517', 6),
('BSN-03-0083/2025', 'Millicent Auma', 'millicentauma@students.zetech.ac.ke', '0754348436', 6),
('BSN-03-0084/2025', 'Duncan Muriuki', 'duncanmuriuki@students.zetech.ac.ke', '0755356355', 6),
('DKRCHN-03-0120/2026', 'Purity Wangui', 'puritywangui@students.zetech.ac.ke', '0756364274', 6),
('DKRCHN-03-0121/2026', 'Gideon Rotich', 'gideonrotich@students.zetech.ac.ke', '0757372193', 6),
('DKRCHN-03-0122/2026', 'Agnes Nafula', 'agnesnafula@students.zetech.ac.ke', '0758380112', 6),
('DCHN-03-0047/2025', 'Lawrence Maina', 'lawrencemaina@students.zetech.ac.ke', '0759388031', 6),
('DCHN-03-0048/2025', 'Zawadi Mwanaisha', 'zawadimwanaisha@students.zetech.ac.ke', '0760395950', 6);

INSERT INTO positions (position_name) VALUES
('President'),
('Vice President'),
('Finance'),
('Secretary General'),
('Academics Secretary'),
('Sports & Entertainment Secretary');

INSERT INTO elections (title, status, stage) VALUES
('Zetech University Student Elections 2026', 'closed', 'delegates');
