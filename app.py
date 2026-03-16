import os, hashlib, requests, json
import feedparser
import concurrent.futures
from datetime import datetime, timedelta, date, timezone
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
try:
    from birdnetlib import Recording
    from birdnetlib.analyzer import Analyzer
    BIRDNET_AVAILABLE = True
except Exception:
    Recording = None
    Analyzer = None
    BIRDNET_AVAILABLE = False

try:
    from newsapi import NewsApiClient
except Exception:
    NewsApiClient = None

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'ecology-blockchain-2026')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    f"sqlite:///{os.path.join(app.instance_path, 'dissonance_ledger.db')}"
)
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ── MODELS ────────────────────────────────────────────────────────────────────

class User(UserMixin, db.Model):
    id       = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True)
    password = db.Column(db.String(100))

class ArchiveEntry(db.Model):
    id                    = db.Column(db.Integer, primary_key=True)
    species_common        = db.Column(db.String(100))
    species_sci           = db.Column(db.String(100))
    species_name_merlin   = db.Column(db.String(200))
    confidence            = db.Column(db.Float)
    location_name         = db.Column(db.String(200))
    lat                   = db.Column(db.Float)
    lng                   = db.Column(db.Float)
    timestamp             = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    recording_time_of_day = db.Column(db.String(20))
    recording_date        = db.Column(db.Date)
    file_path             = db.Column(db.String(200))
    prev_hash             = db.Column(db.String(64))
    current_hash          = db.Column(db.String(64), unique=True)
    care_signatures       = db.relationship('CareSignature', backref='entry', lazy=True)

class CareSignature(db.Model):
    """Community acknowledgment — mocked for Algorand-ready structure."""
    id                = db.Column(db.Integer, primary_key=True)
    entry_id          = db.Column(db.Integer, db.ForeignKey('archive_entry.id'), nullable=False)
    user_id           = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    care_token        = db.Column(db.String(64), nullable=False)   # SHA-256 witness token
    statement         = db.Column(db.Text)
    timestamp         = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    location_verified = db.Column(db.Boolean, default=False)
    __table_args__    = (db.UniqueConstraint('entry_id', 'user_id'),)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ── ENGINES ───────────────────────────────────────────────────────────────────

try:
    analyzer = Analyzer()
except Exception as e:
    analyzer = None
    print(f"[WARNING] BirdNET Analyzer unavailable: {e}")

# ── HELPERS ───────────────────────────────────────────────────────────────────

CARE_STATEMENT = (
    "I am local to this area. I have heard these sounds. "
    "I acknowledge the urgency of its conservation."
)

def get_coords(location_name):
    try:
        url = f"https://nominatim.openstreetmap.org/search?q={location_name}+Delhi&format=json&limit=1"
        res = requests.get(url, headers={'User-Agent': 'MappingDissonanceBot/1.0'}).json()
        if res:
            return float(res[0]['lat']), float(res[0]['lon'])
    except:
        pass
    return 28.6139, 77.2090

def entry_to_dict(e, care_count=0):
    return {
        'id':                    e.id,
        'species_common':        e.species_common or 'Unknown',
        'species_sci':           e.species_sci or '',
        'species_name_merlin':   e.species_name_merlin or '',
        'confidence':            round(e.confidence, 3) if e.confidence else 0,
        'location_name':         e.location_name or '',
        'lat':                   e.lat or 0,
        'lng':                   e.lng or 0,
        'timestamp':             e.timestamp.strftime('%d %b %Y, %H:%M') if e.timestamp else '',
        'recording_time_of_day': e.recording_time_of_day or '',
        'file_path':             e.file_path or '',
        'current_hash':          e.current_hash or '',
        'care_count':            care_count,
    }

# ── NEWS: CURATED RSS FEEDS ───────────────────────────────────────────────────

CURATED_RSS = [
    ("Mongabay India · Delhi",   "https://india.mongabay.com/feed/?post_type=post&feedtype=bulletpoints&location=delhi"),
    ("Mongabay India · Haryana", "https://india.mongabay.com/feed/?post_type=post&feedtype=bulletpoints&location=haryana"),
    ("Mongabay India · Forests", "https://india.mongabay.com/feed/?post_type=post&feedtype=bulletpoints&topic=forests"),
    ("Mongabay India",           "https://india.mongabay.com/feed/"),
    ("Down To Earth",            "https://www.downtoearth.org.in/feed"),
    ("The Wire Science",         "https://science.thewire.in/feed/"),
]

def _loc_keywords(location):
    loc = location.lower().strip()
    words = [w for w in loc.split() if len(w) > 3]
    return list(dict.fromkeys([loc] + words))

def _rss_score(entry, kws):
    text = (entry.get('title', '') + ' ' +
            entry.get('summary', '') + ' ' +
            ' '.join(t.get('term', '') for t in entry.get('tags', []))).lower()
    return sum(1 for k in kws if k in text)

def _fetch_rss(source_name, url, kws):
    try:
        feed = feedparser.parse(url)
        results = []
        for e in feed.entries:
            score = _rss_score(e, kws)
            if score > 0:
                results.append({
                    'title':       e.get('title', ''),
                    'url':         e.get('link', ''),
                    'source':      {'name': source_name},
                    'publishedAt': e.get('published', ''),
                    '_score':      score,
                })
        return results
    except:
        return []

# ── ROUTES ────────────────────────────────────────────────────────────────────

# ── ARAVALLI / DELHI RIDGE BIRD CHECKLIST ──────────────────
BIRDS = [
    # ── LC ──
    {"common": "Common Babbler",           "scientific": "Argya caudata",             "hindi": "Saat Bhai",       "rajasthani": "Sat Bhaiya",      "tamil": "Varigal Kuruvi",    "iucn": "LC"},
    {"common": "Indian Robin",             "scientific": "Copsychus fulicatus",        "hindi": "Kalchuri",        "rajasthani": "Kali Tithari",    "tamil": "Karuppan Kuruvi",   "iucn": "LC"},
    {"common": "Purple Sunbird",           "scientific": "Cinnyris asiaticus",         "hindi": "Shakar Khor",     "rajasthani": "Phool Chuhi",     "tamil": "Thean Kuruvi",      "iucn": "LC"},
    {"common": "Asian Koel",               "scientific": "Eudynamys scolopaceus",      "hindi": "Koel",            "rajasthani": "Koel",            "tamil": "Kuil",              "iucn": "LC"},
    {"common": "Coppersmith Barbet",       "scientific": "Psilopogon haemacephala",    "hindi": "Chota Hara Tota", "rajasthani": "Tamrathi",        "tamil": "Vetti Kukku",       "iucn": "LC"},
    {"common": "Common Myna",              "scientific": "Acridotheres tristis",        "hindi": "Desi Maina",      "rajasthani": "Maina",           "tamil": "Nattukuruvi",       "iucn": "LC"},
    {"common": "Rose-ringed Parakeet",     "scientific": "Psittacula krameri",          "hindi": "Tota",            "rajasthani": "Hiraman Tota",    "tamil": "Killi",             "iucn": "LC"},
    {"common": "Red-vented Bulbul",        "scientific": "Pycnonotus cafer",            "hindi": "Bulbul",          "rajasthani": "Bulbul",          "tamil": "Kondai Kuruvi",     "iucn": "LC"},
    {"common": "House Sparrow",            "scientific": "Passer domesticus",           "hindi": "Gauraiya",        "rajasthani": "Gauriya Chidiya", "tamil": "Veettu Kuruvi",     "iucn": "LC"},
    {"common": "Black Kite",               "scientific": "Milvus migrans",              "hindi": "Cheel",           "rajasthani": "Chil",            "tamil": "Parundu",           "iucn": "LC"},
    {"common": "Shikra",                   "scientific": "Accipiter badius",            "hindi": "Shikra",          "rajasthani": "Shikra",          "tamil": "Sirukappu Pagal",   "iucn": "LC"},
    {"common": "Brahminy Kite",            "scientific": "Haliastur indus",             "hindi": "Brahmini Cheel",  "rajasthani": "Mota Cheel",      "tamil": "Thirudan Parundu",  "iucn": "LC"},
    {"common": "Black-shouldered Kite",    "scientific": "Elanus caeruleus",            "hindi": "Kapas Chidiya",   "rajasthani": "—",               "tamil": "Vella Parundu",     "iucn": "LC"},
    {"common": "Indian Eagle Owl",         "scientific": "Bubo bengalensis",            "hindi": "Ghughu",          "rajasthani": "Ullu",            "tamil": "Andhai",            "iucn": "LC"},
    {"common": "Spotted Owlet",            "scientific": "Athene brama",                "hindi": "Chugad",          "rajasthani": "Khuddo",          "tamil": "Pidi Andhai",       "iucn": "LC"},
    {"common": "Barn Owl",                 "scientific": "Tyto alba",                   "hindi": "Safed Ullu",      "rajasthani": "Safed Ullu",      "tamil": "Barn Andhai",       "iucn": "LC"},
    {"common": "Common Hoopoe",            "scientific": "Upupa epops",                 "hindi": "Hudhud",          "rajasthani": "Hudhud",          "tamil": "Upupa",             "iucn": "LC"},
    {"common": "Indian Roller",            "scientific": "Coracias benghalensis",       "hindi": "Nilkanth",        "rajasthani": "Neelkanth",       "tamil": "Peeranam Kuruvi",   "iucn": "LC"},
    {"common": "White-throated Kingfisher","scientific": "Halcyon smyrnensis",          "hindi": "Kilkila",         "rajasthani": "Kilkila",         "tamil": "Vanaveli",          "iucn": "LC"},
    {"common": "Common Kingfisher",        "scientific": "Alcedo atthis",               "hindi": "Chota Kilkila",   "rajasthani": "Chhota Kilkila",  "tamil": "Maen Vanaveli",     "iucn": "LC"},
    {"common": "Pied Kingfisher",          "scientific": "Ceryle rudis",                "hindi": "Khandait",        "rajasthani": "Khandaita",       "tamil": "Varai Vanaveli",    "iucn": "LC"},
    {"common": "Indian Peafowl",           "scientific": "Pavo cristatus",              "hindi": "Mor",             "rajasthani": "Dhol Mor",        "tamil": "Mayil",             "iucn": "LC"},
    {"common": "Black Francolin",          "scientific": "Francolinus francolinus",     "hindi": "Kala Teetar",     "rajasthani": "Kalo Titar",      "tamil": "Karuppu Kadu Kozhi","iucn": "LC"},
    {"common": "Grey Francolin",           "scientific": "Ortygornis pondicerianus",    "hindi": "Teetar",          "rajasthani": "Titar",           "tamil": "Kaadu Kozhi",       "iucn": "LC"},
    {"common": "Yellow-footed Green Pigeon","scientific": "Treron phoenicoptera",       "hindi": "Hariyal",         "rajasthani": "Hario",           "tamil": "Pacchai Puraa",     "iucn": "LC"},
    {"common": "Rock Pigeon",              "scientific": "Columba livia",               "hindi": "Kabutar",         "rajasthani": "Kabutar",         "tamil": "Puura",             "iucn": "LC"},
    {"common": "Eurasian Collared Dove",   "scientific": "Streptopelia decaocto",       "hindi": "Dhol Fakhta",     "rajasthani": "Peeli Ghughi",    "tamil": "Tol Puraa",         "iucn": "LC"},
    {"common": "Laughing Dove",            "scientific": "Spilopelia senegalensis",     "hindi": "Chhota Fakhta",   "rajasthani": "Ghughi",          "tamil": "Siriya Puraa",      "iucn": "LC"},
    {"common": "Jungle Babbler",           "scientific": "Argya striata",               "hindi": "Saat Bhai",       "rajasthani": "Jangli Saat Bhai","tamil": "Kaadu Varigal",     "iucn": "LC"},
    {"common": "Small Minivet",            "scientific": "Pericrocotus cinnamomeus",    "hindi": "Phatikia",        "rajasthani": "Chhotki Phatki",  "tamil": "Sirukappu Mini",    "iucn": "LC"},
    {"common": "White-browed Wagtail",     "scientific": "Motacilla maderaspatensis",   "hindi": "Mamola",          "rajasthani": "Mamola",          "tamil": "Vella Puthai",      "iucn": "LC"},
    {"common": "Common Tailorbird",        "scientific": "Orthotomus sutorius",         "hindi": "Phutki",          "rajasthani": "Darji Chidiya",   "tamil": "Thaiyalkaran",      "iucn": "LC"},
    {"common": "Oriental Magpie Robin",    "scientific": "Copsychus saularis",          "hindi": "Dhayal",          "rajasthani": "Dayal",           "tamil": "Dhayar Kuruvi",     "iucn": "LC"},
    {"common": "Ashy Prinia",              "scientific": "Prinia socialis",             "hindi": "Ashy Phutki",     "rajasthani": "Bhoori Phutki",   "tamil": "Saambal Kuruvi",    "iucn": "LC"},
    {"common": "Plain Prinia",             "scientific": "Prinia inornata",             "hindi": "Saada Phutki",    "rajasthani": "Saadi Phutki",    "tamil": "Sada Kuruvi",       "iucn": "LC"},
    {"common": "Indian Grey Hornbill",     "scientific": "Ocyceros birostris",          "hindi": "Dhanesh",         "rajasthani": "Dhanesh",         "tamil": "Irattai Mookkan",   "iucn": "LC"},
    {"common": "Baya Weaver",              "scientific": "Ploceus philippinus",         "hindi": "Baya",            "rajasthani": "Baya",            "tamil": "Thoondi Kuruvi",    "iucn": "LC"},
    {"common": "Indian Silverbill",        "scientific": "Euodice malabarica",          "hindi": "Lal Munia",       "rajasthani": "Chanchri",        "tamil": "Velli Munnai",      "iucn": "LC"},
    {"common": "Red-wattled Lapwing",      "scientific": "Vanellus indicus",            "hindi": "Titihri",         "rajasthani": "Teetihri",        "tamil": "Aal Kaataan",       "iucn": "LC"},
    {"common": "Black-winged Stilt",       "scientific": "Himantopus himantopus",       "hindi": "Teela Titar",     "rajasthani": "Pankha Titar",    "tamil": "Valavai Kuruvi",    "iucn": "LC"},
    {"common": "Indian Pond Heron",        "scientific": "Ardeola grayii",              "hindi": "Andha Bagla",     "rajasthani": "Andha Bagla",     "tamil": "Kuruva Narai",      "iucn": "LC"},
    {"common": "Little Egret",             "scientific": "Egretta garzetta",            "hindi": "Chhota Bagla",    "rajasthani": "Chhoti Bagri",    "tamil": "Siriya Narai",      "iucn": "LC"},
    {"common": "Black-crowned Night Heron","scientific": "Nycticorax nycticorax",       "hindi": "Wak",             "rajasthani": "Wak",             "tamil": "Iravu Narai",       "iucn": "LC"},
    {"common": "Paddyfield Pipit",         "scientific": "Anthus rufulus",              "hindi": "Chitta",          "rajasthani": "Chitta",          "tamil": "Vayalurai Kuruvi",  "iucn": "LC"},
    # ── NT ──
    {"common": "Painted Stork",            "scientific": "Mycteria leucocephala",       "hindi": "Janghil",         "rajasthani": "Janghil",         "tamil": "Ponnarai",          "iucn": "NT"},
    {"common": "Black-headed Ibis",        "scientific": "Threskiornis melanocephalus", "hindi": "Safed Baza",      "rajasthani": "Kali Tokh",       "tamil": "Kattaan Kottan",    "iucn": "NT"},
    {"common": "River Lapwing",            "scientific": "Vanellus duvaucelii",         "hindi": "Pathari",         "rajasthani": "Nadi Titar",      "tamil": "Aatrurai Kaataan",  "iucn": "NT"},
    {"common": "Ferruginous Pochard",      "scientific": "Aythya nyroca",               "hindi": "Ferruginous Batakh","rajasthani": "Lal Batakh",    "tamil": "Irampu Vathu",      "iucn": "NT"},
    # ── VU ──
    {"common": "Indian Spotted Eagle",     "scientific": "Clanga hastata",              "hindi": "Chotti Cheel",    "rajasthani": "Chitti Kali Cheel","tamil": "Pulli Erin",       "iucn": "VU"},
    {"common": "Greater Spotted Eagle",    "scientific": "Clanga clanga",               "hindi": "Badi Cheel",      "rajasthani": "Moti Kali Cheel", "tamil": "Pulli Erin",        "iucn": "VU"},
    {"common": "Sarus Crane",              "scientific": "Antigone antigone",            "hindi": "Sarus",           "rajasthani": "Sarus",           "tamil": "Saaras Kurukku",    "iucn": "VU"},
    {"common": "Bristled Grassbird",       "scientific": "Schoenicola striatus",         "hindi": "—",               "rajasthani": "—",               "tamil": "—",                 "iucn": "VU"},
    # ── EN ──
    {"common": "Egyptian Vulture",         "scientific": "Neophron percnopterus",        "hindi": "Safed Gidhh",     "rajasthani": "Gotram",          "tamil": "Vella Parunthu",    "iucn": "EN"},
    {"common": "Lesser Florican",          "scientific": "Sypheotides indicus",          "hindi": "Kharmore",        "rajasthani": "Lehan",           "tamil": "—",                 "iucn": "EN"},
    # ── CR ──
    {"common": "White-rumped Vulture",     "scientific": "Gyps bengalensis",             "hindi": "Gidhh",           "rajasthani": "Gidhh",           "tamil": "Suryan Parunthu",   "iucn": "CR"},
    {"common": "Indian Vulture",           "scientific": "Gyps indicus",                 "hindi": "Desi Gidhh",      "rajasthani": "Desi Gidhh",      "tamil": "Nadu Parunthu",     "iucn": "CR"},
    {"common": "Slender-billed Vulture",   "scientific": "Gyps tenuirostris",            "hindi": "Patli Chonch Gidhh","rajasthani": "—",             "tamil": "—",                 "iucn": "CR"},
    {"common": "Red-headed Vulture",       "scientific": "Sarcogyps calvus",             "hindi": "Raj Gidhh",       "rajasthani": "Lal Matha Gidhh", "tamil": "Sivappu Parunthu",  "iucn": "CR"},
    {"common": "Sociable Lapwing",         "scientific": "Vanellus gregarius",           "hindi": "—",               "rajasthani": "—",               "tamil": "—",                 "iucn": "CR"},
]

@app.route('/')
def landing():
    return render_template('landing.html')

@app.route('/mapping-dissonance')
def mapping_dissonance():
    entries = ArchiveEntry.query.order_by(ArchiveEntry.timestamp.desc()).all()

    # One query for all care counts
    care_counts = dict(
        db.session.query(CareSignature.entry_id, func.count(CareSignature.id))
        .group_by(CareSignature.entry_id)
        .all()
    )

    entries_json = json.dumps([
        entry_to_dict(e, care_counts.get(e.id, 0)) for e in entries
    ])
    return render_template('archive.html', entries=entries,
                           entries_json=entries_json, care_counts=care_counts)

@app.route('/about')
def about():
    return render_template('about.html', birds=BIRDS)

@app.route('/bibliography')
def bibliography():
    return render_template('bibliography.html')

@app.route('/bird-list')
def bird_list():
    return render_template('bird_list.html', birds=BIRDS)

@app.route('/robots.txt')
def robots_txt():
    return send_from_directory(app.root_path, 'robots.txt')

# ── CARE ROUTES ───────────────────────────────────────────────────────────────

@app.route('/care/sign/<int:entry_id>', methods=['POST'])
def care_sign(entry_id):
    if not current_user.is_authenticated:
        return jsonify({'status': 'unauthenticated'}), 401

    ArchiveEntry.query.get_or_404(entry_id)

    existing = CareSignature.query.filter_by(
        entry_id=entry_id, user_id=current_user.id
    ).first()

    if existing:
        count = CareSignature.query.filter_by(entry_id=entry_id).count()
        return jsonify({
            'status': 'already_signed',
            'care_token': existing.care_token,
            'count': count,
        })

    body         = request.get_json(silent=True) or {}
    loc_verified = bool(body.get('location_verified', False))

    # Generate witness token (Algorand-ready: hash of entry + user + timestamp)
    token_src = f"{entry_id}:{current_user.id}:{datetime.now(timezone.utc).isoformat()}"
    token = hashlib.sha256(token_src.encode()).hexdigest()

    sig = CareSignature(
        entry_id=entry_id,
        user_id=current_user.id,
        care_token=token,
        statement=CARE_STATEMENT,
        location_verified=loc_verified,
    )
    db.session.add(sig)
    db.session.commit()

    count = CareSignature.query.filter_by(entry_id=entry_id).count()
    return jsonify({'status': 'signed', 'care_token': token, 'count': count})

@app.route('/care/status/<int:entry_id>')
def care_status(entry_id):
    count = CareSignature.query.filter_by(entry_id=entry_id).count()

    user_signed = False
    user_token  = None
    if current_user.is_authenticated:
        sig = CareSignature.query.filter_by(
            entry_id=entry_id, user_id=current_user.id
        ).first()
        if sig:
            user_signed = True
            user_token  = sig.care_token

    recent = (CareSignature.query
              .filter_by(entry_id=entry_id)
              .order_by(CareSignature.timestamp.desc())
              .limit(5).all())

    return jsonify({
        'count':       count,
        'user_signed': user_signed,
        'user_token':  user_token,
        'recent': [
            {'token':     s.care_token[:20],
             'timestamp': s.timestamp.strftime('%d %b %Y') if s.timestamp else ''}
            for s in recent
        ],
    })

@app.route('/ledger')
def ledger():
    rows = (db.session.query(CareSignature, ArchiveEntry, User)
            .join(ArchiveEntry, CareSignature.entry_id == ArchiveEntry.id)
            .join(User, CareSignature.user_id == User.id)
            .order_by(CareSignature.timestamp.desc())
            .all())

    records = []
    for sig, entry, user in rows:
        user_hash = hashlib.sha256(user.username.encode()).hexdigest()[:16]
        records.append({
            'timestamp':         sig.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC') if sig.timestamp else '—',
            'user_hash':         user_hash,
            'species':           entry.species_name_merlin or entry.species_common or 'Unknown',
            'location':          entry.location_name or '—',
            'token':             sig.care_token,
            'location_verified': sig.location_verified or False,
        })

    return render_template('ledger.html', records=records)

# ── NEWS ──────────────────────────────────────────────────────────────────────

@app.route('/get_context_news')
def get_context_news():
    location = request.args.get('location', '')
    kws = _loc_keywords(location) if location else []

    # TIER 1 — Curated Indian environmental RSS
    if kws:
        hits = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            futs = {ex.submit(_fetch_rss, name, url, kws): name for name, url in CURATED_RSS}
            for f in concurrent.futures.as_completed(futs):
                hits.extend(f.result())
        if hits:
            hits.sort(key=lambda x: x['_score'], reverse=True)
            for a in hits:
                a.pop('_score', None)
            seen = set()
            deduped = [a for a in hits if not (a['url'] in seen or seen.add(a['url']))]
            return jsonify({'articles': deduped[:6], '_tier': 1})

    # TIER 2 — GDELT (India-filtered, free)
    try:
        from gdeltdoc import GdeltDoc, Filters
        end   = date.today()
        start = end - timedelta(days=180)
        kw    = f'"{location}" Delhi environment' if location else '"Delhi" environment ecology'
        f = Filters(keyword=kw, country=["IN"], language="english",
                    start_date=str(start), end_date=str(end), num_records=10)
        df = GdeltDoc().article_search(f)
        if not df.empty:
            return jsonify({
                'articles': [
                    {'title': r['title'], 'url': r['url'],
                     'source': {'name': r['domain']}, 'publishedAt': str(r['seendate'])}
                    for _, r in df.iterrows()
                ][:6],
                '_tier': 2,
            })
    except Exception:
        pass

    # TIER 3 — NewsAPI, Delhi-locked
    try:
        newsapi = NewsApiClient(api_key=os.environ.get('NEWS_API_KEY', ''))
        env = ('(environment OR "habitat loss" OR urbanization OR pollution OR '
               'construction OR conservation OR biodiversity OR wildlife OR '
               'deforestation OR "green cover" OR encroachment OR "land use")')
        noise = ('NOT (entertainment OR bollywood OR celebrity OR gadgets OR '
                 '"stock market" OR sports OR cricket OR IPL)')
        query = f'("Delhi" AND "{location}") AND {env} {noise}' if location else f'"Delhi" AND {env} {noise}'
        data  = newsapi.get_everything(q=query, language='en', sort_by='relevancy', page_size=6)
        if not data.get('articles'):
            data = newsapi.get_everything(q=f'"Delhi" AND {env} {noise}',
                                          language='en', sort_by='relevancy', page_size=6)
        data['_tier'] = 3
        return jsonify(data)
    except Exception:
        return jsonify({'articles': [], '_tier': 0})

@app.route('/get_iucn_status/<sci_name>')
def get_iucn_status(sci_name):
    token = 'Vkkhj6JS79gDMKW7iRc33aF45k2fZAnpTdXe'
    try:
        url = f"https://api.iucnredlist.org/api/v4/taxa/scientific_name/{sci_name.replace(' ', '%20')}"
        res = requests.get(url, headers={'Authorization': f'Bearer {token}'}).json()
        if 'assessments' in res and res['assessments']:
            return jsonify({'status': res['assessments'][0]['red_list_category']['name']})
    except:
        pass
    return jsonify({'status': 'Data pending'})

@app.route('/upload', methods=['POST'])
@login_required
def upload():
    file = request.files.get('file')
    if not file:
        return redirect(url_for('mapping_dissonance'))

    loc_name    = request.form.get('location_name', 'Delhi').strip()
    merlin_name = request.form.get('species_name_merlin', '').strip()
    time_of_day = request.form.get('recording_time_of_day', '').strip()
    rec_date_str = request.form.get('recording_date', '').strip()
    rec_date = date.fromisoformat(rec_date_str) if rec_date_str else None

    filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
    path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(path)

    common, sci, conf = merlin_name or 'Unknown', 'Unknown', 0.0
    if analyzer:
        try:
            rec = Recording(analyzer, path)
            rec.analyze()
            if rec.detections:
                common = rec.detections[0]['common_name']
                sci    = rec.detections[0]['scientific_name']
                conf   = rec.detections[0]['confidence']
        except:
            pass

    lat, lng = get_coords(loc_name)
    last   = ArchiveEntry.query.order_by(ArchiveEntry.id.desc()).first()
    prev_h = last.current_hash if last else '0' * 64
    new_h  = hashlib.sha256(f"{prev_h}{common}{filename}".encode()).hexdigest()

    db.session.add(ArchiveEntry(
        species_common=common, species_sci=sci,
        species_name_merlin=merlin_name, confidence=conf,
        location_name=loc_name, lat=lat, lng=lng,
        recording_time_of_day=time_of_day, recording_date=rec_date,
        file_path=filename, prev_hash=prev_h, current_hash=new_h,
    ))
    db.session.commit()
    return redirect(url_for('mapping_dissonance'))

@app.route('/audio/<filename>')
def serve_audio(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user, remember=True)
            return redirect(url_for('mapping_dissonance'))
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        hashed_pw = generate_password_hash(request.form['password'], method='pbkdf2:sha256')
        db.session.add(User(username=request.form['username'], password=hashed_pw))
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

_BIRD_IUCN  = {b['common']: b['iucn'] for b in BIRDS}
_IUCN_RANK  = {'CR': 5, 'EN': 4, 'VU': 3, 'NT': 2, 'LC': 1}
_IUCN_LABEL = {5: 'CR', 4: 'EN', 3: 'VU', 2: 'NT', 1: 'LC'}

@app.route('/api/landing-records')
def landing_records():
    """Return un-clustered ArchiveEntry records for the parchment map."""
    entries = (ArchiveEntry.query
            .filter(ArchiveEntry.lat.isnot(None), ArchiveEntry.lng.isnot(None))
            .order_by(ArchiveEntry.timestamp.desc())
            .limit(200)
            .all())

    # Fetch all care counts efficiently in one query
    care_counts = dict(
        db.session.query(CareSignature.entry_id, func.count(CareSignature.id))
        .group_by(CareSignature.entry_id)
        .all()
    )

    records = []
    for e in entries:
        records.append({
            'id': e.id,
            'lat': e.lat,
            'lng': e.lng,
            'species_common': e.species_common or 'Unknown',
            'location_name': e.location_name or 'Field Recording',
            'timestamp': e.timestamp.strftime('%d %b %Y, %H:%M') if e.timestamp else '',
            'file_path': e.file_path,
            'care_count': care_counts.get(e.id, 0),
            'iucn': _BIRD_IUCN.get(e.species_common, 'LC')
        })
    
    return jsonify(records)

@app.route('/api/recording-pins')
def recording_pins():
    """Return geolocated ArchiveEntry clusters for the landing map."""
    rows = (ArchiveEntry.query
            .filter(ArchiveEntry.lat.isnot(None), ArchiveEntry.lng.isnot(None))
            .with_entities(
                ArchiveEntry.id,
                ArchiveEntry.lat, ArchiveEntry.lng,
                ArchiveEntry.species_common,
                ArchiveEntry.location_name,
                ArchiveEntry.recording_date)
            .order_by(ArchiveEntry.timestamp.desc())
            .limit(200)
            .all())

    # Cluster by location_name — merge points within ~500m
    clusters = {}
    for r in rows:
        key = r.location_name or 'field-{:.3f}-{:.3f}'.format(r.lat, r.lng)
        if key not in clusters:
            clusters[key] = {'lat': r.lat, 'lng': r.lng,
                             'count': 0, 'species': set(), 'location': r.location_name or 'Field Recording',
                             'iucn_rank': 0}
        clusters[key]['count'] += 1
        if r.species_common:
            clusters[key]['species'].add(r.species_common)
            rank = _IUCN_RANK.get(_BIRD_IUCN.get(r.species_common, 'LC'), 1)
            if rank > clusters[key]['iucn_rank']:
                clusters[key]['iucn_rank'] = rank

    pins = []
    for i, (_, c) in enumerate(clusters.items()):
        species_list = ', '.join(sorted(c['species'])[:5])
        if not species_list:
            species_list = 'Unknown'
        pins.append({
            'id': 'db_' + str(i),
            'position': [c['lng'], c['lat']],
            'count': c['count'],
            'species': species_list,
            'location': c['location'],
            'iucn': _IUCN_LABEL.get(c['iucn_rank'], 'LC')
        })

    return jsonify(pins)

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('landing'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=False, use_reloader=False, port=5001)